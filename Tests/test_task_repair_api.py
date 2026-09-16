import http.client
import http.server
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('repair_api_adapter', Path(__file__).resolve().parents[1] / 'ModelHarbor/Support/grok_adapter.py')
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class RepairAPITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        token = Path(temporary.name) / 'token'
        token.write_text('synthetic-owner-token')
        self.monitor = Mock(snapshot={'enabled': True, 'pending': 0, 'state': 'ready'})
        for name, value in [('TOKEN_PATH', token), ('TASK_REPAIRS', self.monitor)]:
            replacement = patch.object(adapter, name, value)
            replacement.start()
            self.addCleanup(replacement.stop)
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), adapter.Handler)
        self.server.daemon_threads = True
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=2)

    def request(self, path='/harbor/repairs/enable', headers=None, body=b''):
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            client.request('POST', path, body=body, headers=headers or {})
            response = client.getresponse()
            return response.status, response.read()
        finally:
            client.close()

    def test_unauthorized_and_browser_requests_cannot_enable_repairs(self):
        for headers in [{}, {'X-Model-Harbor-Token': 'incorrect'},
                        {'X-Model-Harbor-Token': 'synthetic-owner-token', 'Origin': 'https://example.org'}]:
            with self.subTest(headers=list(headers)):
                self.assertEqual(self.request(headers=headers)[0], 401)
        self.monitor.set_enabled.assert_not_called()

    def test_owner_can_enable_and_disable_without_a_body(self):
        headers = {'X-Model-Harbor-Token': 'synthetic-owner-token'}
        status, body = self.request(headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['state'], 'ready')
        self.monitor.set_enabled.assert_called_once_with(True)
        self.assertEqual(self.request('/harbor/repairs/disable', headers)[0], 200)
        self.monitor.set_enabled.assert_called_with(False)

    def test_request_data_is_rejected_before_any_setting_change(self):
        headers = {'X-Model-Harbor-Token': 'synthetic-owner-token'}
        self.assertEqual(self.request(headers=headers, body=b'{"path":"arbitrary"}')[0], 400)
        self.monitor.set_enabled.assert_not_called()


if __name__ == '__main__':
    unittest.main()
