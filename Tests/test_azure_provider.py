import io
import json
import unittest
from unittest.mock import patch
import test_provider_connections as connections
bridge = connections.bridge


class AzureConnectionsTests(unittest.TestCase):
    stop = connections.ProviderConnectionsTests.stop
    request = connections.ProviderConnectionsTests.request
    def setUp(self):
        connections.ProviderConnectionsTests.setUp(self)
        self.endpoint = 'https://fixture.openai.azure.com/openai/v1'
        self.key = 'synthetic-azure-secret'
        (self.root / 'model-catalogs').mkdir()
        self.write_effort('none')
        (self.root / 'model-switcher.json').write_text(json.dumps({'services': [
            {'id': 'azure', 'baseURL': self.endpoint, 'models': [{'id': 'coding-prod'}]}]}))
        for name, value in [('AZURE_CONNECTION', None)]:
            p = patch.object(bridge, name, value)
            p.start(); self.addCleanup(p.stop)

    # Reuse only the local HTTP harness, not the OpenRouter-specific tests.
    def write_effort(self, effort):
        (self.root / 'model-catalogs/azure.json').write_text(json.dumps({'models': [{'slug': 'coding-prod', 'default_reasoning_level': effort}]}))

    def configure(self, **kwargs):
        return self.request(path='/harbor/providers/azure', body={'key': self.key, 'endpoint': self.endpoint}, **kwargs)

    def test_auth_and_status_redaction(self):
        for headers in [{}, {'X-Model-Harbor-Token': 'wrong'}, {'X-Model-Harbor-Token': 'synthetic-owner-token', 'Origin': 'https://evil.test'}]:
            self.assertEqual(self.configure(headers=headers)[0], 401)
        self.assertEqual(self.configure()[0], 200)
        status, body = self.request('GET', '/harbor/status')
        self.assertEqual(status, 200)
        self.assertTrue(body['providers']['azure_ready'])
        self.assertNotIn(self.key, json.dumps(body))
        self.assertEqual(self.request(path='/harbor/providers/azure', body={'key': ''})[0], 200)
        self.assertFalse(bridge.provider_status()['azure_ready'])

    def test_endpoint_boundary(self):
        for endpoint in ['http://fixture.openai.azure.com', 'https://fixture.openai.azure.com.evil.test',
                         'https://fixture.openai.azure.com:443', 'https://user:pass@fixture.openai.azure.com',
                         'https://fixture.openai.azure.com/?key=x', 'https://fixture.openai.azure.com/path',
                         'https://127.0.0.1/openai/v1']:
            self.assertEqual(self.request(path='/harbor/providers/azure', body={'key': self.key, 'endpoint': endpoint})[0], 400)
            self.assertIsNone(bridge.AZURE_CONNECTION)
        self.assertEqual(bridge.azure_endpoint('https://fixture.services.ai.azure.com/'), 'https://fixture.services.ai.azure.com/openai/v1')

    def test_azure_routes_exact_deployment_without_subscription_credentials_or_baseten_queue(self):
        self.configure()
        class Response(io.BytesIO):
            status = 200
            headers = {'Content-Type': 'application/json'}
        with patch.object(bridge.urllib.request, 'build_opener') as opener, \
             patch.object(bridge, 'baseten_headers', side_effect=AssertionError('Baseten')), \
             patch.object(bridge, 'open_baseten', side_effect=AssertionError('Baseten pacing')):
            opener.return_value.open.return_value = Response(b'{"status":"completed","output":[]}')
            status, body = self.request(path='/harbor/v1/responses', body={
                'model': 'harbor/azure/coding-prod', 'input': 'Hello', 'stream': False,
                'reasoning': {'effort': 'none'}, 'service_tier': 'priority', 'client_metadata': {'thread_id': 'fixture'}
            }, headers={'Authorization': 'Bearer synthetic-owner-token', 'ChatGPT-Account-ID': 'private-account'})
            request = opener.return_value.open.call_args.args[0]
        self.assertEqual((status, body['status']), (200, 'completed'))
        self.assertEqual(request.full_url, self.endpoint + '/responses')
        self.assertEqual(request.get_header('Api-key'), self.key)
        self.assertNotIn('Authorization', request.headers)
        self.assertNotIn('Chatgpt-account-id', request.headers)
        payload = json.loads(request.data)
        self.assertEqual(payload['model'], 'coding-prod')
        self.assertEqual(payload['input'], 'Hello')
        for key in ['reasoning', 'client_metadata', 'service_tier']: self.assertNotIn(key, payload)
        self.assertEqual(bridge.provider_status()['activity']['azure']['completed'], 1)

    def test_disconnected_or_changed_endpoint_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'Connect Azure'):
            bridge.routed_request({'model': 'harbor/azure/coding-prod', 'input': []}, {})
        self.configure()
        bridge.AZURE_CONNECTION['endpoint'] = 'https://different.openai.azure.com/openai/v1'
        with self.assertRaisesRegex(ValueError, 'endpoint changed'):
            bridge.routed_request({'model': 'harbor/azure/coding-prod', 'input': []}, {})

    def test_auth_failure_invalidates_connection_but_quota_does_not(self):
        for code in [429, 401, 403]:
            self.configure()
            error = bridge.urllib.error.HTTPError(self.endpoint + '/responses', code, 'fixture', {}, io.BytesIO(b'{"error":"fixture"}'))
            with patch.object(bridge.urllib.request, 'build_opener') as opener:
                opener.return_value.open.side_effect = error
                status, _ = self.request(path='/harbor/v1/responses', body={'model': 'harbor/azure/coding-prod', 'input': []}, headers={'Authorization': 'Bearer synthetic-owner-token'})
            self.assertEqual(status, code)
            self.assertEqual(bridge.provider_status()['azure_ready'], code == 429)

    def test_images_and_namespaced_tools_survive_translation_and_stream(self):
        self.configure()
        self.write_effort('medium')
        source = {'model': 'harbor/azure/coding-prod', 'reasoning': {'effort': 'xhigh'},
                  'tools': [{'type': 'namespace', 'name': 'work', 'tools': [{'type': 'function', 'name': 'read', 'parameters': {'type': 'object'}}]}],
                  'input': [{'type': 'function_call_output', 'call_id': 'call_1', 'output': [{'type': 'input_image', 'image_url': 'data:image/png;base64,fixture'}]}]}
        translated, _, _, _ = bridge.routed_request(source, {})
        self.assertEqual(translated.request['reasoning']['effort'], 'medium')
        self.assertIn('data:image/png;base64,fixture', json.dumps(translated.request['input']))
        self.assertEqual(translated.request['tools'][0]['type'], 'function')
        alias = translated.request['tools'][0]['name']
        event = {'type': 'response.output_item.done', 'item': {'type': 'function_call', 'name': alias, 'arguments': json.dumps({'tool': 'read', 'arguments': '{}'}), 'call_id': 'call_2'}}
        result = translated.event(('data: ' + json.dumps(event)).encode()).decode()
        item = json.loads(result.removeprefix('data: '))['item']
        self.assertEqual((item['namespace'], item['name'], item['arguments']), ('work', 'read', '{}'))


if __name__ == '__main__': unittest.main()
