import copy
import importlib.util
import json
import pathlib
import unittest

path = pathlib.Path(__file__).parents[1] / 'ModelHarbor/Support/grok_adapter.py'
spec = importlib.util.spec_from_file_location('grok_adapter', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class TranslationTests(unittest.TestCase):
    def translation(self, count=1):
        return module.Translation({'model':'grok-4.20-0309-reasoning','reasoning':{'effort':'high'},'tools':[
            {'type':'namespace','name':'mcp__fixture','tools':[
                {'type':'function','name':'read_'+str(i),'parameters':{'type':'object','properties':{}}} for i in range(count)]}],
            'input':[{'role':'user','content':'Read the marker'}]})

    def test_grok_slider_effort_values_reach_upstream_unchanged(self):
        for model, efforts in [('grok-4.6', ['low', 'medium', 'high', 'xhigh']),
                               ('grok-4.5', ['low', 'medium', 'high'])]:
            for effort in efforts:
                with self.subTest(model=model, effort=effort):
                    translated = module.Translation({'model': model, 'input': [], 'reasoning': {'effort': effort}})
                    self.assertEqual(translated.request['reasoning']['effort'], effort)

    def test_tool_free_request_drops_stale_tool_choice_without_mutating_source(self):
        for supplied_tools in (False, True):
            for choice in ({'type': 'function', 'name': 'exec'}, 'required', 'auto', 'none'):
                for translation, native in ((module.Translation, False), (module.Translation, True),
                                            (module.AzureTranslation, False)):
                    with self.subTest(supplied_tools=supplied_tools, choice=choice,
                                      translation=translation.__name__, native=native):
                        source = {'model': 'fixture-model',
                                  'input': [{'role': 'user', 'content': 'Summarize this task'}],
                                  'tool_choice': choice}
                        if supplied_tools:
                            source['tools'] = []
                        original = copy.deepcopy(source)
                        translated = translation(source, native_tools=native).request
                        self.assertNotIn('tool_choice', translated)
                        self.assertFalse(translated.get('tools'))
                        self.assertEqual(translated['input'], original['input'])
                        self.assertEqual(source, original)

    def test_nonempty_tools_keep_forced_choice_in_every_request_path(self):
        source = {'tools': [{'type': 'function', 'name': 'exec', 'parameters': {'type': 'object'}}],
                  'tool_choice': {'type': 'function', 'name': 'exec'}, 'input': []}
        for translation, native in ((module.Translation, False), (module.Translation, True),
                                    (module.AzureTranslation, False)):
            with self.subTest(translation=translation.__name__, native=native):
                translated = translation(source, native_tools=native).request
                self.assertEqual(translated['tools'], source['tools'])
                self.assertEqual(translated['tool_choice'], {'type': 'function', 'name': 'exec'})

    def test_tool_free_azure_compaction_preserves_opaque_history_and_tool_result(self):
        reasoning = {'type': 'reasoning', 'id': 'rs_fixture', 'summary': [],
                     'encrypted_content': 'synthetic-opaque',
                     'extension': {'tool_choice': {'type': 'function', 'name': 'opaque'}}}
        source = {'tools': [], 'tool_choice': {'type': 'function', 'name': 'exec'}, 'input': [
            reasoning, {'type': 'custom_tool_call_output', 'id': 'ctco_fixture',
                        'call_id': 'call_fixture', 'output': 'synthetic-result'}]}
        translated = module.AzureTranslation(source).request
        self.assertNotIn('tool_choice', translated)
        self.assertEqual(translated['input'], [reasoning, {'type': 'function_call_output',
                         'call_id': 'call_fixture', 'output': 'synthetic-result'}])
        self.assertIn('tool_choice', source)
        self.assertEqual(source['input'][1]['type'], 'custom_tool_call_output')

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
        self.assertEqual(sent,[{'type':'function_call','call_id':'call-1','name':alias,
                                'arguments':json.dumps({'tool':'read_0','arguments':'{"file":"a.txt"}'})},result])

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

    def test_native_tools_and_cross_provider_history_remain_valid(self):
        tools=[{'type':'namespace','name':'functions','tools':[{'type':'custom','name':'apply_patch'}]}]
        history=[{'type':'custom_tool_call','id':'ctc_foreign','call_id':'call1','name':'apply_patch','namespace':'functions','input':'patch'},
                 {'type':'custom_tool_call_output','id':'ctco_foreign','call_id':'call1','output':'done'},
                 {'type':'function_call','id':'foreign_item','call_id':'call2','namespace':'mcp__test','name':'read','arguments':'{}'},
                 {'type':'reasoning','encrypted_content':'foreign'}]
        native=module.Translation({'tools':tools,'input':history}, native_tools=True)
        self.assertEqual(native.request['tools'],tools)
        self.assertEqual(native.request['input'][0],{'type':'custom_tool_call','call_id':'call1','name':'apply_patch','namespace':'functions','input':'patch'})
        self.assertEqual(native.request['input'][1]['call_id'],'call1')
        self.assertEqual(native.request['input'][2]['namespace'],'mcp__test')
        self.assertEqual(len(native.request['input']),3)
        self.assertFalse(any('id' in item for item in native.request['input']))
        translated=module.Translation({'tools':tools,'input':history})
        self.assertEqual(translated.request['input'][0]['type'],'function_call')
        self.assertFalse(any('id' in item for item in translated.request['input']))
        self.assertEqual(translated.request['input'][1],{'type':'function_call_output','call_id':'call1','output':'done'})
        self.assertEqual(translated.request['input'][2]['call_id'],'call2')
        self.assertEqual(history[1]['id'],'ctco_foreign')
        self.assertEqual(history[0]['id'],'ctc_foreign')

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
        module.BASETEN_CREDENTIALS = module.BasetenCredentials()
        self.data = {'services': [
            {'id':'grok-oauth','models':[{'id':'grok-4.6'}]},
            {'id':'codex-subscription','models':[{'id':'gpt-6-astra'}]},
            {'id':'baseten','models':[{'id':'moonshotai/Kimi-K3'}, {'id':'deepseek-ai/DeepSeek-V4.1-Flash'}]}
        ]}
        self.select('grok-oauth','grok-4.6')

    def tearDown(self):
        self.config.stop()
        self.temp.cleanup()

    def select(self, provider, model):
        self.model_id = 'harbor/' + provider + '/' + model
        self.data['selectedModel'] = {'serviceID':provider, 'modelID':model}
        (self.root/'model-switcher.json').write_text(json.dumps(self.data))

    def test_tasks_and_tool_continuations_keep_independent_models(self):
        first = {'model':'harbor/grok-oauth/grok-4.6', 'client_metadata':{'thread_id':'a','turn_id':'1'}}
        second = {'model':'harbor/baseten/moonshotai/Kimi-K3', 'client_metadata':{'thread_id':'b','turn_id':'1'}}
        self.assertEqual(module.route_for_turn(first, {})['model'], 'grok-4.6')
        self.assertEqual(module.route_for_turn(second, {})['model'], 'moonshotai/Kimi-K3')
        self.select('baseten', 'deepseek-ai/DeepSeek-V4.1-Flash')
        self.assertEqual(module.route_for_turn(first, {})['model'], 'grok-4.6')
        # A turn keeps its original route even if a client changes model mid-tool-call.
        first['model'] = 'harbor/codex-subscription/gpt-6-astra'
        self.assertEqual(module.route_for_turn(first, {})['model'], 'grok-4.6')
        first['client_metadata']['turn_id'] = '2'
        self.assertEqual(module.route_for_turn(first, {})['model'], 'gpt-6-astra')
        second['client_metadata']['turn_id'] = '2'
        self.assertEqual(module.route_for_turn(second, {})['model'], 'moonshotai/Kimi-K3')
        # A restarted bridge derives the route from the task request, not its last default.
        module.TURN_ROUTES.clear()
        self.assertEqual(module.route_for_turn(first, {})['model'], 'gpt-6-astra')
        self.assertEqual(module.route_for_turn(second, {})['model'], 'moonshotai/Kimi-K3')

    def test_legacy_alias_is_frozen_across_default_changes(self):
        self.data['legacyModel'] = {'serviceID':'grok-oauth','modelID':'grok-4.6'}
        self.select('baseten', 'moonshotai/Kimi-K3')
        self.assertEqual(module.requested_route('harbor-selected')['model'], 'grok-4.6')
        self.select('codex-subscription', 'gpt-6-astra')
        module.TURN_ROUTES.clear()
        self.assertEqual(module.requested_route('harbor-selected')['model'], 'grok-4.6')
        del self.data['legacyModel']
        self.select('codex-subscription', 'gpt-6-astra')
        with self.assertRaises(ValueError):
            module.requested_route('harbor-selected')

    def test_unavailable_or_malformed_model_never_falls_back(self):
        for model in ('harbor/baseten', 'harbor/baseten/unknown', 'harbor/openai/gpt-6-astra',
                      'grok-4.6', None, 'harbor/codex-subscription/grok-4.6'):
            with self.assertRaises(ValueError):
                module.requested_route(model)

    def test_request_snapshot_is_independent_of_later_selection(self):
        from unittest.mock import patch
        with patch.object(module, 'oauth_headers', return_value={'Authorization':'Bearer oauth-test'}):
            translation, headers, base, route = module.routed_request({'model':self.model_id,'input':'hello'}, {})
        self.select('baseten','moonshotai/Kimi-K3')
        self.assertEqual(translation.request['model'],'grok-4.6')
        self.assertEqual(headers['Authorization'],'Bearer oauth-test')
        self.assertEqual(base, module.OAUTH_BASE)
        self.assertEqual(route, {'provider':'grok-oauth','model':'grok-4.6'})

    def test_no_oauth_credential_goes_to_baseten_and_reasoning_is_preserved(self):
        from unittest.mock import patch
        self.select('baseten', 'moonshotai/Kimi-K3')
        with patch.object(module, 'oauth_headers') as oauth, patch.object(module, 'baseten_headers', return_value={'Authorization':'Bearer baseten-test'}):
            t, headers, base, route = module.routed_request({'model':self.model_id,'reasoning':{'effort':'xhigh'}}, {})
        oauth.assert_not_called()
        self.assertEqual(headers, {'Authorization':'Bearer baseten-test'})
        self.assertEqual(base, 'https://inference.baseten.co/v1')
        self.assertEqual(t.request['reasoning']['effort'], 'xhigh')

    def test_subscription_uses_only_codex_supplied_auth_and_never_api_billing(self):
        from unittest.mock import patch
        self.select('codex-subscription', 'gpt-6-astra')
        incoming = {'Authorization':'Bearer header.payload.signature', 'ChatGPT-Account-ID':'account-1',
                    'X-Model-Harbor-Token':'local-secret', 'Cookie':'must-not-forward'}
        with patch.object(module, 'oauth_headers') as grok, patch.object(module, 'baseten_headers') as baseten:
            t, headers, base, route = module.routed_request({'model':self.model_id,'stream':True}, incoming)
        grok.assert_not_called(); baseten.assert_not_called()
        self.assertEqual(t.request['model'], 'gpt-6-astra')
        self.assertEqual(base, 'https://chatgpt.com/backend-api/codex')
        self.assertEqual(headers['Authorization'], incoming['Authorization'])
        self.assertEqual(headers['ChatGPT-Account-ID'], 'account-1')
        self.assertNotIn('X-Model-Harbor-Token', headers)
        self.assertNotIn('Cookie', headers)
        for invalid in ({}, {'Authorization':'Bearer sk-api-key', 'ChatGPT-Account-ID':'account-1'},
                        {'Authorization':'Bearer header.payload.signature'}):
            with self.assertRaises(ValueError):
                module.routed_request({'model':self.model_id}, invalid)

    def test_other_providers_never_receive_codex_credentials(self):
        from unittest.mock import patch
        incoming = {'Authorization':'Bearer codex.access.token', 'ChatGPT-Account-ID':'codex-account',
                    'X-Model-Harbor-Token':'local-secret'}
        for provider, model, helper in [('grok-oauth','grok-4.6','oauth_headers'), ('baseten','moonshotai/Kimi-K3','baseten_headers')]:
            self.select(provider, model)
            with patch.object(module, helper, return_value={'Authorization':'Bearer own-credential'}):
                _, headers, _, _ = module.routed_request({'model':self.model_id}, incoming)
            self.assertEqual(headers, {'Authorization':'Bearer own-credential'})

    def test_missing_selection_and_server_side_history_fail_closed(self):
        self.select('openai', '__native__')
        with self.assertRaisesRegex(ValueError, 'Choose a named Model Harbor model'):
            module.routed_request({'model':self.model_id}, {})
        with self.assertRaisesRegex(ValueError, 'full conversation history'):
            module.routed_request({'model':self.model_id,'previous_response_id':'resp1'}, {})
        with self.assertRaisesRegex(ValueError, 'Model Harbor model'):
            module.routed_request({'model':'grok-4.6'}, {})

    def test_credential_helper_failure_never_falls_back(self):
        from unittest.mock import patch
        (self.root/'config.toml').write_text('[model_providers.baseten]\nbase_url="https://inference.baseten.co/v1"\n[model_providers.baseten.auth]\ncommand="/fake/op"\nargs=["read","op://a/b/c"]\n')
        with patch.object(module.subprocess, 'run', side_effect=module.subprocess.CalledProcessError(1,['/fake/op'])) as run:
            with self.assertRaisesRegex(module.BasetenCredentialError, "Reconnect Baseten"):
                module.baseten_headers()
        self.assertTrue(run.call_args.kwargs['capture_output'])

    def helper_config(self, item='op://a/b/c'):
        (self.root/'config.toml').write_text('[model_providers.baseten]\nbase_url="https://inference.baseten.co/v1"\n[model_providers.baseten.auth]\ncommand="/fake/op"\nargs=["read","'+item+'"]\n')

    def test_baseten_tool_continuations_and_model_switches_unlock_once(self):
        from unittest.mock import patch
        self.helper_config()
        result = module.subprocess.CompletedProcess(['/fake/op'], 0, 'secret-key\n')
        with patch.object(module.subprocess, 'run', return_value=result) as run:
            for model in ['moonshotai/Kimi-K3', 'moonshotai/Kimi-K3', 'deepseek-ai/DeepSeek-V4.1-Flash', 'moonshotai/Kimi-K3']:
                self.select('baseten', model)
                _, headers, base, _ = module.routed_request({'model':self.model_id,'input':'hello'}, {})
                self.assertEqual(headers['Authorization'], 'Bearer secret-key')
                self.assertEqual(base, 'https://inference.baseten.co/v1')
        self.assertEqual(run.call_count, 1)
        self.assertEqual(module.BASETEN_CREDENTIALS.snapshot, {'state':'ready','helper_reads':1,'reuses':3})
        self.assertNotIn('secret-key', ''.join(f.read_text() for f in self.root.iterdir()))

    def test_simultaneous_baseten_requests_share_one_unlock(self):
        from concurrent.futures import ThreadPoolExecutor
        from unittest.mock import patch
        import threading
        self.helper_config()
        started, release = threading.Event(), threading.Event()
        def unlock(*args, **kwargs):
            started.set()
            self.assertTrue(release.wait(5))
            return module.subprocess.CompletedProcess(['/fake/op'], 0, 'shared-key')
        with patch.object(module.subprocess, 'run', side_effect=unlock) as run, ThreadPoolExecutor(max_workers=8) as pool:
            pending = [pool.submit(module.baseten_headers) for _ in range(8)]
            self.assertTrue(started.wait(5))
            release.set()
            self.assertEqual([p.result(timeout=5)['Authorization'] for p in pending], ['Bearer shared-key']*8)
        self.assertEqual(run.call_count, 1)

    def test_canceled_failed_and_empty_unlocks_do_not_prompt_again(self):
        from unittest.mock import patch
        for outcome in [module.subprocess.CalledProcessError(1, ['/fake/op'], stderr='private diagnostic'),
                        module.subprocess.TimeoutExpired(['/fake/op'], 1),
                        module.subprocess.CompletedProcess(['/fake/op'], 0, ''),
                        module.subprocess.CompletedProcess(['/fake/op'], 0, 'bad\nkey')]:
            with self.subTest(outcome=type(outcome).__name__):
                module.BASETEN_CREDENTIALS = module.BasetenCredentials()
                self.helper_config()
                behavior = {'side_effect':outcome} if isinstance(outcome, Exception) else {'return_value':outcome}
                with patch.object(module.subprocess, 'run', **behavior) as run:
                    for _ in range(4):
                        with self.assertRaisesRegex(module.BasetenCredentialError, 'Automatic retries are paused') as error:
                            module.baseten_headers()
                        self.assertNotIn('private diagnostic', str(error.exception))
                self.assertEqual(run.call_count, 1)
                self.assertEqual(module.BASETEN_CREDENTIALS.snapshot['state'], 'needs_reconnect')

    def test_reconnect_and_changed_helper_load_the_current_key(self):
        from unittest.mock import patch
        self.helper_config()
        keys = [module.subprocess.CompletedProcess([], 0, key) for key in ['first','second','third']]
        with patch.object(module.subprocess, 'run', side_effect=keys) as run:
            self.assertEqual(module.baseten_headers()['Authorization'], 'Bearer first')
            module.BASETEN_CREDENTIALS.reset()
            self.assertEqual(module.baseten_headers()['Authorization'], 'Bearer second')
            module.BASETEN_CREDENTIALS.reject('Bearer first')
            self.assertEqual(module.baseten_headers()['Authorization'], 'Bearer second')
            self.helper_config('op://a/new/c')
            self.assertEqual(module.baseten_headers()['Authorization'], 'Bearer third')
            self.assertEqual(module.baseten_headers()['Authorization'], 'Bearer third')
        self.assertEqual(run.call_count, 3)

    def test_baseten_auth_rejection_does_not_automatically_unlock_again(self):
        from unittest.mock import patch
        self.helper_config()
        with patch.object(module.subprocess, 'run', return_value=module.subprocess.CompletedProcess([],0,'rejected-key')) as run:
            header = module.baseten_headers()['Authorization']
            module.BASETEN_CREDENTIALS.reject(header)
            for _ in range(3):
                with self.assertRaisesRegex(module.BasetenCredentialError, 'Baseten rejected'):
                    module.baseten_headers()
        self.assertEqual(run.call_count, 1)
        self.assertIsNone(module.BASETEN_CREDENTIALS.key)

    def test_http_reconnect_is_explicit_authenticated_and_recovers_a_canceled_unlock(self):
        import threading, http.client
        from unittest.mock import patch
        self.helper_config()
        token = self.root/'token'; token.write_text('test-local-bridge-token')
        with patch.object(module,'TOKEN_PATH',token), patch.object(module.subprocess,'run',side_effect=[
                module.subprocess.CalledProcessError(1, ['/fake/op']),
                module.subprocess.CompletedProcess([],0,'recovered-key')]) as run:
            with self.assertRaises(module.BasetenCredentialError):
                module.baseten_headers()
            server = module.http.server.ThreadingHTTPServer(('127.0.0.1',0),module.Handler)
            worker = threading.Thread(target=server.serve_forever,daemon=True); worker.start()
            def request(path, headers, method='POST'):
                conn = http.client.HTTPConnection(*server.server_address,timeout=5)
                conn.request(method,path,headers=headers)
                response=conn.getresponse(); result=(response.status,response.read())
                conn.close(); return result
            try:
                path='/harbor/baseten/reconnect'
                auth={'Authorization':'Bearer test-local-bridge-token'}
                self.assertEqual(request(path,{})[0],401)
                self.assertEqual(request(path,dict(auth,Origin='https://example.com'))[0],401)
                self.assertEqual(run.call_count,1)
                self.assertEqual(request(path,auth)[0],200)
                self.assertEqual(module.baseten_headers()['Authorization'],'Bearer recovered-key')
                status,body=request('/harbor/status',auth,'GET')
                self.assertEqual(status,200)
                self.assertNotIn(b'recovered-key',body)
                self.assertEqual(json.loads(body)['baseten_auth'],{'state':'ready','helper_reads':2,'reuses':1})
                self.assertEqual(run.call_count,2)
            finally:
                server.shutdown(); server.server_close(); worker.join()

    def test_http_bridge_requires_local_token_and_preserves_routed_stream(self):
        import threading, urllib.request, urllib.error, io
        from unittest.mock import patch
        token = self.root/'token'; token.write_text('test-local-bridge-token')
        response = {'type':'response.completed','response':{'id':'r','model':'grok-4.6','status':'completed','output':[]}}
        body = ('event: response.completed\ndata: '+json.dumps(response)+'\n\n').encode()
        class Upstream(io.BytesIO):
            status = 200
            headers = {}
            def __next__(self):
                line = self.readline()
                if not line:
                    raise AssertionError('Must stop at response.completed without waiting for server EOF')
                return line
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
                c.request('POST','/harbor/v1/responses',body=json.dumps({'model':self.model_id,'stream':True,'input':'hello'}),headers={'Authorization':'Bearer test-local-bridge-token'})
                r = c.getresponse()
                self.assertEqual(r.status,200)
                self.assertEqual(r.getheader('X-Model-Harbor-Model'),'grok-4.6')
                self.assertIn(b'response.completed',r.read())
                self.assertEqual(opener.request.get_header('Authorization'),'Bearer upstream-oauth')
                self.assertEqual(json.loads(opener.request.data)['model'],'grok-4.6')
                c.close()
                c = http.client.HTTPConnection(*server.server_address, timeout=5)
                c.request('POST','/harbor/v1/responses',body=json.dumps({'model':self.model_id,'stream':True}),
                          headers={'Authorization':'Bearer codex.access.token','X-Model-Harbor-Token':'wrong-local-token'})
                self.assertEqual(c.getresponse().status,401); c.close()
                c = http.client.HTTPConnection(*server.server_address, timeout=5)
                c.request('POST','/harbor/v1/responses',body=json.dumps({'model':self.model_id,'stream':True}),
                          headers={'Authorization':'Bearer codex.access.token','X-Model-Harbor-Token':'test-local-bridge-token',
                                   'ChatGPT-Account-ID':'codex-account'})
                r = c.getresponse()
                self.assertEqual(r.status,200)
                self.assertEqual(r.getheader('Content-Type'),'text/event-stream')
                self.assertIn(b'response.completed',r.read())
                self.assertNotIn('Chatgpt-account-id',opener.request.headers)
                self.assertNotIn('X-model-harbor-token',opener.request.headers)
                self.assertEqual(opener.request.get_header('Authorization'),'Bearer upstream-oauth')
                c.close()
            finally:
                server.shutdown(); server.server_close(); worker.join()

if __name__ == '__main__':
    unittest.main()
