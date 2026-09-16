import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bridge', ROOT / 'ModelHarbor/Support/grok_adapter.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class BasetenReasoningTests(unittest.TestCase):
    def routed(self, model, effort):
        with patch.object(bridge, 'route_for_turn', return_value={'provider': 'baseten', 'model': model}), patch.object(bridge, 'baseten_headers', return_value={}):
            return bridge.routed_request({'model': 'harbor/baseten/' + model, 'input': [], 'reasoning': {'effort': effort}}, {})[0].request

    def test_verified_xhigh_reaches_provider_without_downgrade(self):
        for model in ('moonshotai/Kimi-K3', 'zai-org/GLM-5.3', 'deepseek-ai/DeepSeek-V4-Pro-0813'):
            with self.subTest(model=model):
                request = self.routed(model, 'xhigh')
                self.assertEqual(request['reasoning']['effort'], 'xhigh')
                self.assertEqual(request['model'], model)

    def test_unsupported_max_is_not_silently_downgraded(self):
        for effort in ('max', 'ultra'):
            with self.assertRaisesRegex(ValueError, 'not translated silently'):
                self.routed('moonshotai/Kimi-K3', effort)

    def test_kimi_code_explicitly_enables_thinking_without_fictional_depth(self):
        request = self.routed('moonshotai/Kimi-K2.7-Code', 'high')
        self.assertEqual(request['chat_template_args'], {'enable_thinking': True})
        self.assertNotIn('reasoning', request)
        with self.assertRaisesRegex(ValueError, 'no documented depth'):
            self.routed('moonshotai/Kimi-K2.7-Code', 'xhigh')

    def test_all_role_efforts_are_advertised_by_their_models(self):
        manifest = json.loads((ROOT / 'ModelHarbor/Support/baseten-models.json').read_text())
        models = {m['id']: m for m in manifest['models']}
        for role in manifest['roles']:
            self.assertIn(role['effort'], models[role['model']]['efforts'])
        self.assertEqual(len([r for r in manifest['roles'] if r['kind'] == 'coder']), 3)
