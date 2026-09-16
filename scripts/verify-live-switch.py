#!/usr/bin/env python3
"""Opt-in live proof: one Codex process and thread, four models, real tool calls.

Uses existing Grok OAuth and Baseten credential helpers. Sends only synthetic
verification prompts; does not change the user's config, sessions, or selection.
"""
import argparse
import importlib.util
import json
import os
import pathlib
import queue
import shutil
import subprocess
import tempfile
import threading
import time
import tomllib
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--subscription', action='store_true', help='Include current ChatGPT subscription; official Codex manages auth.')
    parser.add_argument('--installed', action='store_true', help='Use the running app; waits for selections made in its menu.')
    parser.add_argument('--verify-upgrade', action='store_true', help='Start with the old provider config, update it while Codex is open, then test a new task.')
    args = parser.parse_args()
    if args.verify_upgrade and (not args.subscription or args.installed):
        parser.error('--verify-upgrade requires --subscription and uses an isolated bridge')
    repo = pathlib.Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('bridge', repo/'CodexModelSwitcher/Support/grok_adapter.py')
    bridge = importlib.util.module_from_spec(spec); spec.loader.exec_module(bridge)
    real = pathlib.Path.home()/'.codex'
    original = tomllib.loads((real/'config.toml').read_text())['model_providers']['baseten']
    saved = json.loads((real/'model-switcher.json').read_text())
    models = [('grok-oauth','grok-4.6'), ('baseten','moonshotai/Kimi-K3'),
              ('baseten','deepseek-ai/DeepSeek-V4.1-Flash'), ('grok-oauth','grok-4.5'), ('grok-oauth','grok-4.6')]
    if args.subscription:
        native = json.loads((real/'models_cache.json').read_text())['models']
        native = [m for m in native if m.get('visibility') == 'list' and m.get('slug') == 'gpt-6-astra']
        if not native: raise RuntimeError('The current Codex catalog does not include GPT-6 Astra.')
        saved['services'].append({'id':'codex-subscription', 'models':[{'id':m['slug']} for m in native]})
        models = [('codex-subscription',native[0]['slug']), ('grok-oauth','grok-4.6'), ('baseten','moonshotai/Kimi-K3'), ('codex-subscription',native[0]['slug'])]
    if args.verify_upgrade:
        models = models[:1]
    with tempfile.TemporaryDirectory(prefix='harbor-live-') as tmp:
        root = pathlib.Path(tmp)
        bridge.CONFIG_DIR = real if args.installed else root
        bridge.TOKEN_PATH = real/'model-harbor-bridge-token' if args.installed else root/'bridge-token'
        server = None
        port = 48118
        if not args.installed:
            bridge.ensure_bridge_token()
            server = bridge.http.server.ThreadingHTTPServer(('127.0.0.1',0), bridge.Handler)
            port = server.server_port
            worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        catalog = json.loads((real/'model-catalogs/grok-oauth.json').read_text())
        entry = catalog['models'][0]
        entry.update(slug='harbor-selected',display_name='Model Harbor selection', default_reasoning_level='low')
        (root/'catalog.json').write_text(json.dumps({'models':[entry]}))
        # Only the existing credential command/config is copied; no provider secret.
        lines = ['model="harbor-selected"','model_provider="model-harbor"','model_reasoning_effort="low"',
                 'model_catalog_json='+json.dumps(str(root/'catalog.json')), 'approval_policy="never"', 'sandbox_mode="read-only"',
                 '[features]', 'apps=false', '[model_providers.model-harbor]', 'name="Model Harbor"', 'wire_api="responses"',
                 'base_url="http://127.0.0.1:'+str(port)+'/harbor/v1"','supports_websockets=false',
                 '[model_providers.model-harbor.auth]', 'command="/bin/cat"', 'args=['+json.dumps(str(bridge.TOKEN_PATH))+']']
        if args.subscription:
            # Point this short test at the existing official auth cache without
            # copying or printing its credentials.
            (root/'auth.json').symlink_to(real/'auth.json')
            lines = lines[:-3] + ['requires_openai_auth=true',
                '[model_providers.model-harbor.http_headers]',
                'X-Model-Harbor-Token='+json.dumps(bridge.TOKEN_PATH.read_text().strip())]
        def table(path, values):
            rows=['['+'.'.join(path)+']']
            for key,val in values.items():
                if not isinstance(val,dict): rows.append(json.dumps(key)+'='+json.dumps(val))
            for key,val in values.items():
                if isinstance(val,dict): rows += table(path+[key],val)
            return rows
        lines += table(['model_providers','baseten'],original)
        (root/'marker-mcp.py').write_text('''import json,sys,pathlib
for line in sys.stdin:
 try:q=json.loads(line)
 except ValueError:continue
 method=q.get('method');r={}
 if method=='initialize':r={'protocolVersion':'2024-11-05','capabilities':{'tools':{}},'serverInfo':{'name':'verification','version':'1'}}
 elif method=='tools/list':r={'tools':[{'name':'read_marker','description':'Read the current verification marker; changes each turn.','annotations':{'readOnlyHint':True,'destructiveHint':False,'openWorldHint':False},'inputSchema':{'type':'object','properties':{},'additionalProperties':False}}]}
 elif method=='tools/call':r={'content':[{'type':'text','text':pathlib.Path(__file__).with_name('marker').read_text()}]}
 if 'id' in q:print(json.dumps({'jsonrpc':'2.0','id':q['id'],'result':r}),flush=True)
''')
        lines += ['[mcp_servers.verification]','command='+json.dumps(shutil.which('python3')),
                  'args=['+json.dumps(str(root/'marker-mcp.py'))+']']
        final_config = '\n'.join(lines)+'\n'
        initial_config = final_config
        if args.verify_upgrade:
            old_auth = '[model_providers.model-harbor.auth]\ncommand="/bin/cat"\nargs=['+json.dumps(str(bridge.TOKEN_PATH))+']'
            new_auth = 'requires_openai_auth=true\n[model_providers.model-harbor.http_headers]\nX-Model-Harbor-Token='+json.dumps(bridge.TOKEN_PATH.read_text().strip())
            initial_config = final_config.replace(new_auth, old_auth)
            if initial_config == final_config: raise RuntimeError('Upgrade fixture was not changed')
        (root/'config.toml').write_text(initial_config)
        env = dict(os.environ, CODEX_HOME=str(root))
        q = queue.Queue()
        proc = subprocess.Popen([shutil.which('codex'),'app-server','--stdio'],env=env,cwd=root,
                                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
        def reader():
            for line in proc.stdout:
                try:q.put(json.loads(line))
                except ValueError:pass
        threading.Thread(target=reader,daemon=True).start()
        counter = 0
        def rpc(method, params):
            nonlocal counter
            counter += 1
            proc.stdin.write(json.dumps({'id':counter,'method':method,'params':params})+'\n');proc.stdin.flush()
            deadline=time.monotonic()+60
            while time.monotonic()<deadline:
                value=q.get(timeout=max(0.1,deadline-time.monotonic()))
                if value.get('id')==counter:
                    if 'error' in value:raise RuntimeError(json.dumps(value['error']))
                    return value['result']
            raise TimeoutError(method)
        report=[]
        try:
            rpc('initialize',{'clientInfo':{'name':'harbor_verification','version':'1'},'capabilities':{'experimentalApi':True}})
            proc.stdin.write('{"method":"initialized"}\n');proc.stdin.flush()
            if args.verify_upgrade:
                (root/'config.toml').write_text(final_config)
            started=rpc('thread/start',{'cwd':str(root),'model':'harbor-selected','modelProvider':'model-harbor','approvalPolicy':'never','sandbox':'read-only','ephemeral':True})
            thread=started['thread']['id']
            for index,(provider,model) in enumerate(models):
                marker='harbor-'+os.urandom(8).hex()
                (root/'marker').write_text(marker)
                if args.installed:
                    print(json.dumps({'select_in_harbor':model}),flush=True)
                    deadline = time.monotonic()+300
                    while bridge.selected_route() != {'provider':provider,'model':model}:
                        if time.monotonic()>deadline:raise TimeoutError('Waiting for Model Harbor selection')
                        time.sleep(0.25)
                else:
                    saved['selectedModel']={'serviceID':provider,'modelID':model}
                    (root/'model-switcher.json').write_text(json.dumps(saved))
                memory='remember-switch-8529'
                prompt=('Remember '+memory+'. ' if index==0 else 'Recall the memory word from my first message. ')
                prompt+=' This is verification turn '+str(index+1)+' ('+os.urandom(6).hex()+'). The external marker file was just replaced. Your first action must be a fresh call to the verification MCP read_marker tool. Previously returned markers are now invalid. Reply with the memory word and the NEW tool result, nothing else.'
                result=rpc('turn/start',{'threadId':thread,'input':[{'type':'text','text':prompt}],'model':'harbor-selected','effort':'low'})
                deadline=time.monotonic()+180
                messages=[];tool_calls=0
                while time.monotonic()<deadline:
                    value=q.get(timeout=max(0.1,deadline-time.monotonic()))
                    method=value.get('method');params=value.get('params',{})
                    if method=='item/completed':
                        item=params.get('item',{})
                        if item.get('type')=='agentMessage':messages.append(item.get('text',''))
                        if item.get('type')=='mcpToolCall' and item.get('status')=='completed':tool_calls+=1
                    if method=='turn/completed':
                        turn=params['turn']
                        if turn['status']!='completed':raise RuntimeError('Turn failed: '+json.dumps(turn.get('error')))
                        break
                else:raise TimeoutError('turn')
                text='\n'.join(messages)
                last_route = bridge.LAST_ROUTE
                if args.installed:
                    request = urllib.request.Request('http://127.0.0.1:48118/harbor/status', headers={'Authorization':'Bearer '+bridge.TOKEN_PATH.read_text().strip()})
                    with urllib.request.urlopen(request,timeout=10) as response:
                        last_route=json.load(response)['last_request']
                row={'model':model,'provider':provider,'tool_calls':tool_calls,'marker_verified':marker in text,
                     'memory_verified':memory in text,'route_verified':last_route==dict(model=model,provider=provider,state='completed')}
                report.append(row);print(json.dumps(row),flush=True)
                if not all([row['tool_calls']>0,row['marker_verified'],row['memory_verified'],row['route_verified']]):
                    raise RuntimeError('Live verification failed: '+repr(text))
            print(json.dumps({'passed':True,'turns':len(report),'codex_process_id':proc.pid,'thread_id':thread,'restarts':0}),flush=True)
        finally:
            proc.terminate()
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
            if server:
                server.shutdown();server.server_close();worker.join()

if __name__=='__main__':main()
