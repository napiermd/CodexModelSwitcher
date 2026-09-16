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


class OAuthTests(unittest.TestCase):
    def test_rejects_api_key_session(self):
        from unittest.mock import patch
        with patch.object(pathlib.Path, 'exists', return_value=True), patch.object(pathlib.Path, 'read_text', return_value=json.dumps({'xai':{'key':'api-key','auth_mode':'api_key'}})):
            with self.assertRaisesRegex(ValueError, 'Select a Grok account'):
                module.session()

    def test_oauth_headers_use_session_and_actual_client_version(self):
        from unittest.mock import patch
        auth={'key':'oauth-test','expires_at':'2099-01-01T00:00:00Z'}
        with patch.object(module,'session',return_value=auth), patch.object(module,'grok_binary',return_value='/fake/grok'), patch.object(module.subprocess,'check_output',return_value='grok 1.0.30 stable'):
            headers=module.oauth_headers()
        self.assertEqual(headers['Authorization'],'Bearer oauth-test')
        self.assertEqual(headers['User-Agent'],'ModelHarbor/1.0')
        self.assertEqual(headers['x-grok-client-version'],'1.0.30')

    def test_expired_session_refreshes_through_official_client(self):
        from unittest.mock import patch
        old={'key':'old','expires_at':'2000-01-01T00:00:00Z'}
        new={'key':'fresh','expires_at':'2099-01-01T00:00:00Z'}
        with patch.object(module,'session',side_effect=[old,new]), patch.object(module,'grok_binary',return_value='/fake/grok'), patch.object(module.subprocess,'run') as run, patch.object(module.subprocess,'check_output',return_value='grok 1.0.30'):
            self.assertEqual(module.oauth_headers()['Authorization'],'Bearer fresh')
            self.assertEqual(run.call_args.args[0],['/fake/grok','models'])
            self.assertNotIn('XAI_API_KEY',run.call_args.kwargs['env'])

    def test_failed_refresh_does_not_fall_back_to_api_key(self):
        from unittest.mock import patch
        old={'key':'old','expires_at':'2000-01-01T00:00:00Z'}
        with patch.object(module,'session',return_value=old), patch.object(module,'grok_binary',return_value='/fake/grok'), patch.object(module.subprocess,'run'):
            with self.assertRaisesRegex(ValueError,'could not refresh'):
                module.oauth_headers()

class LiveRoutingTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from unittest.mock import patch
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.config = patch.object(module, 'CONFIG_DIR', self.root)
        self.config.start()
        module.TURN_ROUTES.clear()
        self.data = {'services': [
            {'id':'grok-oauth','models':[{'id':'grok-4.6'}]},
            {'id':'baseten','models':[{'id':'moonshotai/Kimi-K3'}, {'id':'deepseek-ai/DeepSeek-V4.1-Flash'}]}
        ]}
        self.select('grok-oauth','grok-4.6')

    def tearDown(self):
        self.config.stop()
        self.temp.cleanup()

    def select(self, provider, model):
        self.data['selectedModel'] = {'serviceID':provider, 'modelID':model}
        (self.root/'model-switcher.json').write_text(json.dumps(self.data))

    def test_next_turn_switches_but_tool_continuation_stays_on_original_model(self):
        first = {'client_metadata':{'x-codex-turn-metadata': json.dumps({'thread_id':'t','turn_id':'1'})}}
        second = {'client_metadata':{'x-codex-turn-metadata': json.dumps({'thread_id':'t','turn_id':'2'})}}
        self.assertEqual(module.route_for_turn(first, {})['model'], 'grok-4.6')
        self.select('baseten', 'moonshotai/Kimi-K3')
        self.assertEqual(module.route_for_turn(first, {})['model'], 'grok-4.6')
        self.assertEqual(module.route_for_turn(second, {})['model'], 'moonshotai/Kimi-K3')
        self.select('baseten', 'deepseek-ai/DeepSeek-V4.1-Flash')
        self.assertEqual(module.route_for_turn({}, {'x-codex-turn-metadata':'{"thread_id":"t","turn_id":"3"}'})['model'], 'deepseek-ai/DeepSeek-V4.1-Flash')

    def test_request_snapshot_is_independent_of_later_selection(self):
        from unittest.mock import patch
        with patch.object(module, 'oauth_headers', return_value={'Authorization':'Bearer oauth-test'}):
            translation, headers, base, route = module.routed_request({'model':'harbor-selected','input':'hello'}, {})
        self.select('baseten','moonshotai/Kimi-K3')
        self.assertEqual(translation.request['model'],'grok-4.6')
        self.assertEqual(headers['Authorization'],'Bearer oauth-test')
        self.assertEqual(base, module.OAUTH_BASE)
        self.assertEqual(route, {'provider':'grok-oauth','model':'grok-4.6'})

    def test_no_oauth_credential_goes_to_baseten_and_reasoning_is_bounded(self):
        from unittest.mock import patch
        self.select('baseten', 'moonshotai/Kimi-K3')
        with patch.object(module, 'oauth_headers') as oauth, patch.object(module, 'baseten_headers', return_value={'Authorization':'Bearer baseten-test'}):
            t, headers, base, route = module.routed_request({'model':'harbor-selected','reasoning':{'effort':'xhigh'}}, {})
        oauth.assert_not_called()
        self.assertEqual(headers, {'Authorization':'Bearer baseten-test'})
        self.assertEqual(base, 'https://inference.baseten.co/v1')
        self.assertEqual(t.request['reasoning']['effort'], 'high')

    def test_missing_selection_and_server_side_history_fail_closed(self):
        self.select('openai', '__native__')
        with self.assertRaisesRegex(ValueError, 'Choose a Grok or Baseten'):
            module.routed_request({'model':'harbor-selected'}, {})
        with self.assertRaisesRegex(ValueError, 'full conversation history'):
            module.routed_request({'model':'harbor-selected','previous_response_id':'resp1'}, {})
        with self.assertRaisesRegex(ValueError, 'Model Harbor selection'):
            module.routed_request({'model':'grok-4.6'}, {})

    def test_credential_helper_failure_never_falls_back(self):
        from unittest.mock import patch
        (self.root/'config.toml').write_text('[model_providers.baseten]\nbase_url="https://inference.baseten.co/v1"\n[model_providers.baseten.auth]\ncommand="/fake/op"\nargs=["read","op://a/b/c"]\n')
        with patch.object(module.subprocess, 'run', side_effect=module.subprocess.CalledProcessError(1,['/fake/op'])) as run:
            with self.assertRaises(module.subprocess.CalledProcessError):
                module.baseten_headers()
        self.assertTrue(run.call_args.kwargs['capture_output'])

    def test_http_bridge_requires_local_token_and_preserves_routed_stream(self):
        import threading, urllib.request, urllib.error, io
        from unittest.mock import patch
        token = self.root/'token'; token.write_text('test-local-bridge-token')
        response = {'type':'response.completed','response':{'id':'r','model':'grok-4.6','status':'completed','output':[]}}
        body = ('event: response.completed\ndata: '+json.dumps(response)+'\n\n').encode()
        class Upstream(io.BytesIO):
            status = 200
            headers = {'Content-Type':'text/event-stream'}
        class Opener:
            def open(self, request, timeout):
                self.request = request
                return Upstream(body)
        opener = Opener()
        with patch.object(module,'TOKEN_PATH',token), patch.object(module,'oauth_headers',return_value={'Authorization':'Bearer upstream-oauth'}), patch.object(module.urllib.request,'build_opener',return_value=opener):
            server = module.http.server.ThreadingHTTPServer(('127.0.0.1',0),module.Handler)
            worker = threading.Thread(target=server.serve_forever,daemon=True); worker.start()
            import http.client
            try:
                c = http.client.HTTPConnection(*server.server_address, timeout=5)
                c.request('POST','/harbor/v1/responses',body='{}',headers={'Authorization':'Bearer wrong-token'})
                self.assertEqual(c.getresponse().status,401); c.close()
                c = http.client.HTTPConnection(*server.server_address, timeout=5)
                c.request('POST','/harbor/v1/responses',body=json.dumps({'model':'harbor-selected','stream':True,'input':'hello'}),headers={'Authorization':'Bearer test-local-bridge-token'})
                r = c.getresponse()
                self.assertEqual(r.status,200)
                self.assertEqual(r.getheader('X-Model-Harbor-Model'),'grok-4.6')
                self.assertIn(b'response.completed',r.read())
                self.assertEqual(opener.request.get_header('Authorization'),'Bearer upstream-oauth')
                self.assertEqual(json.loads(opener.request.data)['model'],'grok-4.6')
                c.close()
            finally:
                server.shutdown(); server.server_close(); worker.join()

if __name__ == '__main__':
    unittest.main()
