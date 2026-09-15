import importlib.util
import json
import pathlib
import unittest

path = pathlib.Path(__file__).parents[1] / 'CodexModelSwitcher/Support/grok_adapter.py'
spec = importlib.util.spec_from_file_location('grok_adapter', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class TranslationTests(unittest.TestCase):
    def translation(self, count=1):
        return module.Translation({'model':'grok-4.20-0309-reasoning','reasoning':{'effort':'high'},'tools':[
            {'type':'namespace','name':'mcp__fixture','tools':[
                {'type':'function','name':'read_'+str(i),'parameters':{'type':'object','properties':{}}} for i in range(count)]}],
            'input':[{'role':'user','content':'Read the marker'}]})

    def test_large_group_keeps_every_tool_under_provider_limit(self):
        t=self.translation(446)
        self.assertEqual(len(t.request['tools']),1)
        self.assertEqual(len(t.request['tools'][0]['parameters']['properties']['tool']['enum']),446)
        self.assertNotIn('reasoning',t.request)

    def test_tool_call_and_result_survive_roundtrip(self):
        t=self.translation()
        alias=t.request['tools'][0]['name']
        call={'type':'function_call','id':'item-1','call_id':'call-1','name':alias,
              'arguments':json.dumps({'tool':'read_0','arguments':'{"file":"a.txt"}'})}
        codex=t.output(call)
        self.assertEqual(codex,{'type':'function_call','id':'item-1','call_id':'call-1','namespace':'mcp__fixture',
                               'name':'read_0','arguments':'{"file":"a.txt"}'})
        result={'type':'function_call_output','call_id':'call-1','output':'marker-19'}
        sent=t.input_items([codex,result])
        self.assertEqual(sent,[call,result])

    def test_stream_preserves_text_and_emits_complete_namespaced_call(self):
        t=self.translation()
        alias=t.request['tools'][0]['name']
        added={'type':'response.output_item.added','item':{'id':'i','type':'function_call','name':alias,'arguments':''}}
        self.assertEqual(t.event(('data: '+json.dumps(added)).encode()),b'')
        delta={'type':'response.function_call_arguments.delta','item_id':'i','delta':'partial'}
        self.assertEqual(t.event(('data: '+json.dumps(delta)).encode()),b'')
        done={'type':'response.output_item.done','item':{'id':'i','type':'function_call','name':alias,
              'arguments':json.dumps({'tool':'read_0','arguments':'{}'})}}
        event=t.event(('data: '+json.dumps(done)).encode())
        self.assertIn(b'"namespace":"mcp__fixture"',event)
        text=b'event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"hello"}'
        self.assertIn(b'"delta":"hello"',t.event(text))

    def test_custom_tool_roundtrip(self):
        t=module.Translation({'tools':[{'type':'namespace','name':'functions','tools':[{'type':'custom','name':'apply_patch'}]}]})
        call={'type':'function_call','name':t.request['tools'][0]['name'],'call_id':'p',
              'arguments':json.dumps({'tool':'apply_patch','arguments':'*** Begin Patch\n*** End Patch'})}
        out=t.output(call)
        self.assertEqual(out['type'],'custom_tool_call')
        self.assertEqual(out['input'],'*** Begin Patch\n*** End Patch')
        self.assertEqual(t.input_items([out])[0],call)

    def test_opaque_reasoning_does_not_corrupt_tool_history(self):
        t=self.translation()
        history=[{'type':'reasoning','encrypted_content':'opaque'}, {'type':'function_call_output','call_id':'a','output':'b'}]
        self.assertEqual(t.input_items(history),history[1:])

    def test_invalid_dispatcher_name_is_rejected(self):
        t=self.translation()
        with self.assertRaises(ValueError):
            t.output({'type':'function_call','name':t.request['tools'][0]['name'],
                      'arguments':'{"tool":"missing","arguments":"{}"}'})

if __name__=='__main__':unittest.main()
