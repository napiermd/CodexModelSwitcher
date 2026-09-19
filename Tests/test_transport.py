import importlib.util
from pathlib import Path
import socket
import ssl
import unittest
import urllib.error

PATH = Path(__file__).parents[1] / 'ModelHarbor/Support/transport.py'
SPEC = importlib.util.spec_from_file_location('transport', PATH)
transport = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transport)


class TransportTests(unittest.TestCase):
    def test_workload_classes_use_separate_openers(self):
        self.assertIsNot(transport.stream_opener(), transport.probe_opener())
        self.assertIs(transport.stream_opener(), transport.stream_opener())
        self.assertIs(transport.probe_opener(), transport.probe_opener())

    def test_redirects_are_never_followed_on_any_class(self):
        for opener in (transport.stream_opener(), transport.probe_opener()):
            handlers = getattr(opener, 'handlers', [])
            self.assertTrue(any(type(handler).__name__ == 'NoRedirect' for handler in handlers))

    def test_classifier_table(self):
        cases = [
            (ssl.SSLError('x'), 'tls_error'),
            (socket.gaierror('x'), 'dns_failure'),
            (ConnectionRefusedError('x'), 'connection_refused'),
            (ConnectionResetError('x'), 'connection_reset'),
            (BrokenPipeError('x'), 'connection_reset'),
            (socket.timeout('x'), 'timeout'),
            (urllib.error.URLError('x'), 'connect_error'),
        ]
        for error, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(transport.classify(error), expected)

    def test_cause_chain_unwraps_url_error(self):
        wrapped = urllib.error.URLError(ConnectionRefusedError('refused'))
        self.assertEqual(transport.classify(wrapped), 'connection_refused')

    def test_describe_names_host_without_content(self):
        message = transport.describe(ConnectionResetError('PRIVATE detail'), host='example.openai.azure.com')
        self.assertIn('connection_reset', message)
        self.assertIn('example.openai.azure.com', message)
        self.assertNotIn('PRIVATE', message)


if __name__ == '__main__':
    unittest.main()
