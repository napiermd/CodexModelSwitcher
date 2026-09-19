import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).parents[1] / 'ModelHarbor/Support/chat_reasoning.py'
SPEC = importlib.util.spec_from_file_location('chat_reasoning', PATH)
reasoning = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reasoning)


def reasoning_item(text):
    return {'type': 'reasoning', 'content': [{'type': 'reasoning_text', 'text': text}]}


class ChatReasoningTests(unittest.TestCase):
    def test_family_matching(self):
        self.assertEqual(reasoning.reasoning_family('deepseek-ai/DeepSeek-V4-Pro-0813'), 'deepseek')
        self.assertEqual(reasoning.reasoning_family('zai-org/GLM-5.3'), 'glm')
        self.assertEqual(reasoning.reasoning_family('moonshotai/Kimi-K3'), 'kimi-k3')
        self.assertEqual(reasoning.reasoning_family('minimax/minimax-m3'), 'minimax-m3')

    def test_nonthinking_and_unlisted_models_stay_out(self):
        self.assertIsNone(reasoning.reasoning_family('deepseek-chat'))
        self.assertIsNone(reasoning.reasoning_family('deepseek-ocr'))
        self.assertIsNone(reasoning.reasoning_family('moonshotai/Kimi-K2.7-Code'))
        self.assertIsNone(reasoning.reasoning_family('gpt-5.6-sol'))
        self.assertIsNone(reasoning.reasoning_family(None))

    def test_unlisted_history_is_byte_identical(self):
        history = [reasoning_item('PRIVATE thought'), {'type': 'message', 'role': 'user'}]
        result = reasoning.replay_reasoning(history, 'gpt-5.6-sol')
        self.assertIs(result, history)

    def test_listed_family_replays_as_reasoning_content(self):
        history = [reasoning_item('considered the options')]
        result = reasoning.replay_reasoning(history, 'zai-org/GLM-5.3')
        self.assertEqual(result, [(history[0], 'considered the options')])

    def test_reasoning_is_never_visible_text(self):
        result = reasoning.replay_reasoning([reasoning_item('PRIVATE')], 'deepseek-ai/deepseek-v4-flash')
        self.assertEqual(result[0][1], 'PRIVATE')
        self.assertNotEqual(result[0][0].get('type'), 'message')

    def test_summary_only_and_empty_reasoning(self):
        summary = {'type': 'reasoning', 'summary': [{'type': 'summary_text', 'text': 'summed'}]}
        self.assertEqual(reasoning.replay_reasoning([summary], 'glm-5.3')[0][1], 'summed')
        self.assertEqual(reasoning.replay_reasoning([{'type': 'reasoning'}], 'glm-5.3'), [])

    def test_encrypted_reasoning_is_never_decomposed(self):
        item = {'type': 'reasoning', 'encrypted_content': 'PRIVATE cipher'}
        self.assertEqual(reasoning.replay_reasoning([item], 'glm-5.3'), [])


if __name__ == '__main__':
    unittest.main()
