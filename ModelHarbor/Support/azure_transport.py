"""Azure HTTP transport with an absolute, cancellable request budget."""
import errno
import enum
import http.client
import math
import select
import socket
import ssl
import threading
import time
import urllib.request


class RequestDeadline(TimeoutError):
    pass


class RequestCancelled(Exception):
    pass


class _ResolverPool:
    def __init__(self, limit=4, resolver=socket.getaddrinfo):
        self._slots = threading.BoundedSemaphore(limit)
        self._resolver = resolver

    def resolve(self, host, port, budget):
        while not self._slots.acquire(timeout=min(budget.poll_seconds, budget.remaining())):
            budget.check()
        ready = threading.Event()
        result = {}

        def resolve():
            try:
                result['addresses'] = self._resolver(host, port, 0, socket.SOCK_STREAM)
            except Exception as error:
                result['error'] = error
            finally:
                self._slots.release()
                ready.set()

        try:
            budget.check()
            worker = threading.Thread(target=resolve, name='harbor-azure-dns', daemon=True)
            worker.start()
        except BaseException:
            self._slots.release()
            raise
        while not ready.wait(min(budget.poll_seconds, budget.remaining())):
            budget.check()
        budget.check()
        if 'error' in result:
            raise result['error']
        return result['addresses']


_RESOLVER = _ResolverPool()


class DispatchState(enum.IntEnum):
    NOT_STARTED = 0
    REQUEST_HEADERS_POSSIBLE = 1
    MODEL_BYTES_POSSIBLE = 2
    RESPONSE_HEADERS_RECEIVED = 3


class RequestBudget:
    def __init__(self, seconds=180, *, cancelled=None, clock=time.monotonic, poll_seconds=.1):
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 180:
            raise ValueError('Request budget must be positive and at most 180 seconds')
        if type(poll_seconds) not in (int, float) or not math.isfinite(poll_seconds) or not 0 < poll_seconds <= .1:
            raise ValueError('Cancellation polling must be positive and at most 0.1 seconds')
        self.deadline = clock() + seconds
        self.poll_seconds = poll_seconds
        self._clock = clock
        self._cancelled = cancelled or (lambda: False)
        self._lock = threading.Lock()
        self._sockets = set()
        self._stop_reason = None
        self._dispatch_state = DispatchState.NOT_STARTED
        self._done = threading.Event()
        self._watcher = threading.Thread(target=self._watch, name='harbor-azure-budget', daemon=True)
        self._watcher.start()

    @property
    def stop_reason(self):
        with self._lock:
            return self._stop_reason

    @property
    def dispatch_possible(self):
        with self._lock:
            return self._dispatch_state >= DispatchState.MODEL_BYTES_POSSIBLE

    @property
    def dispatch_state(self):
        with self._lock:
            return self._dispatch_state

    @property
    def retry_safe(self):
        with self._lock:
            return self._dispatch_state < DispatchState.MODEL_BYTES_POSSIBLE

    @property
    def response_headers_received(self):
        with self._lock:
            return self._dispatch_state >= DispatchState.RESPONSE_HEADERS_RECEIVED

    @staticmethod
    def _interrupt(sock):
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _abort(self, reason):
        with self._lock:
            if self._stop_reason is None:
                self._stop_reason = reason
            sockets = tuple(self._sockets)
        for sock in sockets:
            self._interrupt(sock)

    def _refresh(self):
        if self.stop_reason is not None:
            return
        if self._clock() >= self.deadline:
            self._abort('deadline')
            return
        try:
            cancelled = self._cancelled()
        except Exception:
            cancelled = True
        if cancelled:
            self._abort('cancelled')

    def _watch(self):
        while not self._done.is_set():
            self._refresh()
            if self.stop_reason is not None:
                return
            self._done.wait(min(self.poll_seconds, max(0, self.deadline - self._clock())))

    def check(self):
        self._refresh()
        reason = self.stop_reason
        if reason == 'deadline':
            raise RequestDeadline('Azure request deadline exceeded')
        if reason == 'cancelled':
            raise RequestCancelled('Azure request cancelled')
        if self._done.is_set():
            raise RuntimeError('Azure request budget already finished')

    def remaining(self):
        self.check()
        remaining = self.deadline - self._clock()
        if remaining <= 0:
            self._abort('deadline')
            self.check()
        return remaining

    def cancelled(self):
        self._refresh()
        return self.stop_reason is not None

    def register(self, sock):
        self._refresh()
        with self._lock:
            stopped = self._stop_reason is not None or self._done.is_set()
            if not stopped:
                self._sockets.add(sock)
        if stopped:
            self._interrupt(sock)
            self.check()

    def unregister(self, sock):
        with self._lock:
            self._sockets.discard(sock)

    def _advance_dispatch_state(self, state):
        self.check()
        with self._lock:
            if self._stop_reason is None and not self._done.is_set():
                self._dispatch_state = max(self._dispatch_state, state)
                return
        self.check()

    def mark_request_headers_possible(self):
        self._advance_dispatch_state(DispatchState.REQUEST_HEADERS_POSSIBLE)

    def mark_model_bytes_possible(self):
        self._advance_dispatch_state(DispatchState.MODEL_BYTES_POSSIBLE)

    def mark_response_headers_received(self):
        self._advance_dispatch_state(DispatchState.RESPONSE_HEADERS_RECEIVED)

    def io(self, operation, *args, **kwargs):
        self.check()
        try:
            result = operation(*args, **kwargs)
        except Exception:
            self.check()
            raise
        self.check()
        return result

    def http_handlers(self):
        self.check()
        return [_HTTPHandler(self), _HTTPSHandler(self)]

    def finish(self):
        self._done.set()
        if threading.current_thread() is not self._watcher:
            self._watcher.join()
        with self._lock:
            self._sockets.clear()


def _wait_socket(sock, budget, *, reading=False):
    while True:
        budget.check()
        readable, writable, exceptional = budget.io(select.select,
            [sock] if reading else [], [] if reading else [sock], [sock],
            min(budget.poll_seconds, budget.remaining()))
        if readable or writable or exceptional:
            return


def _connect(host, port, source_address, budget):
    addresses = _RESOLVER.resolve(host, port, budget)
    last_error = OSError('Azure endpoint resolved to no addresses')
    for family, kind, protocol, _, address in addresses:
        budget.check()
        sock = socket.socket(family, kind, protocol)
        try:
            budget.register(sock)
            sock.setblocking(False)
            if source_address:
                sock.bind(source_address)
            error = budget.io(sock.connect_ex, address)
            if error not in (0, errno.EISCONN):
                if error not in (errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY, errno.EINTR):
                    raise OSError(error, 'Azure connection failed')
                _wait_socket(sock, budget)
                error = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if error:
                    raise OSError(error, 'Azure connection failed')
            sock.settimeout(budget.remaining())
            return sock
        except OSError as error:
            last_error = error
            budget.unregister(sock)
            sock.close()
            budget.check()
        except BaseException:
            budget.unregister(sock)
            sock.close()
            raise
    raise last_error


class _Connection:
    def __init__(self, *args, budget, **kwargs):
        self._budget = budget
        self._tunnelling = False
        self._origin_send_index = 0
        super().__init__(*args, **kwargs)

    def connect(self):
        self.sock = _connect(self.host, self.port, self.source_address, self._budget)
        try:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if self._tunnel_host:
                self._tunnelling = True
                try:
                    self._tunnel()
                finally:
                    self._tunnelling = False
        except BaseException:
            self.close()
            raise

    def _send_output(self, message_body=None, encode_chunked=False):
        self._origin_send_index = 0
        return super()._send_output(message_body, encode_chunked)

    def send(self, data):
        if self.sock is None:
            self.connect()
        self.sock.settimeout(self._budget.remaining())
        if not self._tunnelling:
            if self._origin_send_index == 0 and _headers_only(data):
                self._budget.mark_request_headers_possible()
            else:
                self._budget.mark_model_bytes_possible()
            self._origin_send_index += 1
        return self._budget.io(super().send, data)


class _HTTPConnection(_Connection, http.client.HTTPConnection):
    pass


class _HTTPSConnection(_Connection, http.client.HTTPSConnection):
    def connect(self):
        super().connect()
        raw = self.sock
        secure = None
        try:
            self._budget.check()
            secure = self._context.wrap_socket(raw, server_hostname=self._tunnel_host or self.host,
                                               do_handshake_on_connect=False)
            self.sock = secure
            self._budget.register(secure)
            self._budget.unregister(raw)
            secure.setblocking(False)
            while True:
                self._budget.check()
                try:
                    secure.do_handshake()
                    break
                except ssl.SSLWantReadError:
                    _wait_socket(secure, self._budget, reading=True)
                except ssl.SSLWantWriteError:
                    _wait_socket(secure, self._budget)
            secure.settimeout(self._budget.remaining())
        except BaseException:
            self._budget.unregister(raw)
            raw.close()
            if secure is not None:
                self._budget.unregister(secure)
                secure.close()
            self.sock = None
            self._budget.check()
            raise


class _HTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, budget):
        super().__init__()
        self._budget = budget

    def http_open(self, request):
        try:
            self._budget.check()
            response = self.do_open(lambda *args, **kwargs: _HTTPConnection(*args, budget=self._budget, **kwargs), request)
            self._budget.mark_response_headers_received()
            return response
        except Exception:
            self._budget.check()
            raise


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, budget):
        context = ssl.create_default_context()
        context.set_alpn_protocols(['http/1.1'])
        super().__init__(context=context)
        self._budget = budget

    def https_open(self, request):
        try:
            self._budget.check()
            response = self.do_open(lambda *args, **kwargs: _HTTPSConnection(*args, budget=self._budget, **kwargs),
                                    request, context=self._context)
            self._budget.mark_response_headers_received()
            return response
        except Exception:
            self._budget.check()
            raise


def _headers_only(data):
    try:
        value = memoryview(data).tobytes()
    except TypeError:
        return False
    delimiter = value.find(b'\r\n\r\n')
    return delimiter >= 0 and delimiter + 4 == len(value)
