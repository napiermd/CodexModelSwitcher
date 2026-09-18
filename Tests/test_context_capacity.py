import importlib.util
import io
import json
from pathlib import Path
import unittest

PATH = Path(__file__).parents[1] / 'scripts/verify-context-capacity.py'
SPEC = importlib.util.spec_from_file_location('context_capacity', PATH)
capacity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capacity)


class Response(io.BytesIO):
    status = 200
    def __enter__(self): return self
    def __exit__(self, *_): self.close()


class Opener:
    def __init__(self, body): self.body, self.requests = body, []
    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return Response(json.dumps(self.body).encode())


class ContextCapacityTests(unittest.TestCase):
    def payload(self, declared=250000):
        return {'store': False, 'stream': False, 'harbor_synthetic_input_tokens': declared,
                'input': [{'role': 'user', 'content': 'SYNTHETIC ' * 10}], 'max_output_tokens': 16}

    def test_exact_deployment_and_account_are_preserved(self):
        opener = Opener({'status': 'completed', 'usage': {'input_tokens': 299500, 'cached_input_tokens': 0}})
        result = capacity.run('https://fixture.openai.azure.com/openai/v1', 'exact-deploy',
                              'PRIVATE-key', self.payload(), 300000, opener)
        self.assertTrue(result['verified'])
        request, timeout = opener.requests[0]
        self.assertEqual(request.full_url, 'https://fixture.openai.azure.com/openai/v1/responses')
        self.assertEqual(request.headers['Api-key'], 'PRIVATE-key')
        self.assertEqual(json.loads(request.data)['model'], 'exact-deploy')
        self.assertEqual(timeout, 180)
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_success_requires_completed_usage_at_or_above_declared_load(self):
        for body in [
            {'status': 'completed', 'usage': {'input_tokens': 250000}},
            {'status': 'incomplete', 'usage': {'input_tokens': 300000}},
            {'status': 'completed'},
        ]:
            self.assertFalse(capacity.run('https://fixture.openai.azure.com/openai/v1', 'd', 'k',
                                          self.payload(), 300000, Opener(body))['verified'])

    def test_invalid_or_unsafe_payload_is_rejected_before_dispatch(self):
        for payload in [
            {'store': True, 'stream': False, 'harbor_synthetic_input_tokens': 1},
            {'store': False, 'stream': True, 'harbor_synthetic_input_tokens': 1},
            {'store': False, 'stream': False, 'harbor_synthetic_input_tokens': 300001},
            dict(self.payload(), previous_response_id='PRIVATE'),
            dict(self.payload(), model='other'),
        ]:
            opener = Opener({})
            with self.assertRaises(ValueError):
                capacity.run('https://fixture.openai.azure.com/openai/v1', 'd', 'k', payload, 300000, opener)
            self.assertEqual(opener.requests, [])

    def test_non_azure_or_ambiguous_endpoint_is_rejected_before_key_dispatch(self):
        for endpoint in [
            'https://evil.example/openai/v1',
            'https://fixture.openai.azure.com.evil.example/openai/v1',
            'http://fixture.openai.azure.com/openai/v1',
            'https://user:pass@fixture.openai.azure.com/openai/v1',
            'https://fixture.openai.azure.com:443/openai/v1',
            'https://fixture.openai.azure.com/openai/v1?redirect=evil',
        ]:
            opener = Opener({})
            with self.assertRaises(ValueError):
                capacity.run(endpoint, 'd', 'PRIVATE-key', self.payload(), 300000, opener)
            self.assertEqual(opener.requests, [])
