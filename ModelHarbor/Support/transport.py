"""Transport discipline for the gateway: one opener per workload class and
named failure diagnoses.

Probes never share a connection with streaming turns; failures are classified
into stable, host-named classes with no request content. No retry anywhere.
"""
import socket
import ssl
import urllib.error
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_stream_opener = None
_probe_opener = None


def stream_opener():
    global _stream_opener
    if _stream_opener is None:
        _stream_opener = urllib.request.build_opener(NoRedirect)
    return _stream_opener


def probe_opener():
    global _probe_opener
    if _probe_opener is None:
        _probe_opener = urllib.request.build_opener(NoRedirect)
    return _probe_opener


def _cause_chain(error, depth=8):
    current = error
    for _ in range(depth):
        if current is None:
            return
        yield current
        current = getattr(current, 'reason', None) or getattr(current, '__cause__', None)


def classify(error, host=None):
    """Map a transport failure to a stable class string. Never includes
    headers, bodies, or credentials."""
    for current in _cause_chain(error):
        if isinstance(current, ssl.SSLError):
            return 'tls_error'
        if isinstance(current, socket.gaierror):
            return 'dns_failure'
        if isinstance(current, ConnectionRefusedError):
            return 'connection_refused'
        if isinstance(current, (ConnectionResetError, BrokenPipeError)):
            return 'connection_reset'
        if isinstance(current, TimeoutError) or isinstance(current, socket.timeout):
            return 'timeout'
    if isinstance(error, urllib.error.HTTPError):
        return 'http_' + str(error.code)
    if isinstance(error, urllib.error.URLError):
        return 'connect_error'
    return 'transport_error'


def describe(error, host=None):
    """One-line operator diagnosis: class plus host. Content-free."""
    label = classify(error, host)
    if host:
        return f'{label} reaching {host}'
    return label
