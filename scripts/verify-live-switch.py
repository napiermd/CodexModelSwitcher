#!/usr/bin/env python3
"""Opt-in proof of independent task models, in-task switching, resume, and tools.

Sends synthetic prompts only. Uses an isolated Codex home, an existing official
subscription auth cache, and either the installed bridge or a source bridge.
"""
import argparse
import copy
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


def model_id(route):
    return 'harbor/' + route[0] + '/' + route[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--installed', action='store_true')
    parser.add_argument('--without-baseten', action='store_true', help='Source verification without requesting a new credential unlock.')
    args = parser.parse_args()
    repo = pathlib.Path(__file__).resolve().parents[1]
    real = pathlib.Path.home()/'.codex'
    saved = json.loads((real/'model-switcher.json').read_text())
    config = tomllib.loads((real/'config.toml').read_text())
    spec = importlib.util.spec_from_file_location('bridge', repo/'ModelHarbor/Support/grok_adapter.py')
    bridge = importlib.util.module_from_spec(spec); spec.loader.exec_module(bridge)
    codex = ('codex-subscription', 'gpt-6-astra')
    grok = ('grok-oauth', 'grok-4.6')
    kimi = ('baseten', 'moonshotai/Kimi-K3')
    deepseek = ('baseten', 'deepseek-ai/DeepSeek-V4.1-Flash')
    routes = [codex, grok] if args.without_baseten else [codex, grok, kimi, deepseek]
    for provider, model in routes:
        if not any(s['id']==provider and any(m['id']==model for m in s['models']) for s in saved['services']):
            raise RuntimeError('Required model is missing: '+model)
    with tempfile.TemporaryDirectory(prefix='harbor-tasks-') as tmp:
        root = pathlib.Path(tmp)
        server = None
        bridge.CONFIG_DIR = real if args.installed else root
        bridge.TOKEN_PATH = real/'model-harbor-bridge-token' if args.installed else root/'bridge-token'
        port = 48118
        if not args.installed:
            bridge.ensure_bridge_token()
            (root/'model-switcher.json').write_text(json.dumps(saved))
            server = bridge.http.server.ThreadingHTTPServer(('127.0.0.1',0), bridge.Handler)
            port = server.server_port
            threading.Thread(target=server.serve_forever, daemon=True).start()
        token = bridge.TOKEN_PATH.read_text().strip()
        def status():
            request = urllib.request.Request(f'http://127.0.0.1:{port}/harbor/status',headers={'X-Model-Harbor-Token':token})
            with urllib.request.urlopen(request,timeout=10) as response: return json.load(response)
        if status().get('routing') != 'per-task': raise RuntimeError('Bridge needs the per-task update')
        before_auth = status()['baseten_auth']
        if args.installed:
            catalog = json.loads((real/'model-catalogs/model-harbor.json').read_text())
        else:
            entries=[]
            for service in saved['services']:
                if service['id'] not in [r[0] for r in routes]: continue
                source=json.loads(pathlib.Path(service['catalogPath']).read_text())['models']
                for model in service['models']:
                    entry=copy.deepcopy(next(e for e in source if e['slug']==model['id']))
                    entry['slug']=model_id((service['id'],model['id']))
                    entry['display_name']=model['name']+' · '+service['id']
                    entry['supports_parallel_tool_calls']=False
                    entries.append(entry)
            catalog={'models':entries}
        for route in routes:
            if not any(e['slug']==model_id(route) for e in catalog['models']): raise RuntimeError('Missing picker entry')
        (root/'catalog.json').write_text(json.dumps(catalog))
        (root/'auth.json').symlink_to(real/'auth.json')
        (root/'marker-mcp.py').write_text('''import json,sys,pathlib
for line in sys.stdin:
 try:q=json.loads(line)
 except ValueError:continue
 method=q.get('method');r={}
 if method=='initialize':r={'protocolVersion':'2024-11-05','capabilities':{'tools':{}},'serverInfo':{'name':'verification','version':'1'}}
 elif method=='tools/list':r={'tools':[{'name':'read_marker','description':'Read the fresh marker, replaced before every turn.','annotations':{'readOnlyHint':True,'destructiveHint':False,'openWorldHint':False},'inputSchema':{'type':'object','properties':{},'additionalProperties':False}}]}
 elif method=='tools/call':r={'content':[{'type':'text','text':pathlib.Path(__file__).with_name('marker').read_text()}]}
 if 'id' in q:print(json.dumps({'jsonrpc':'2.0','id':q['id'],'result':r}),flush=True)
''')
        lines=['model="harbor-selected"','model_provider="model-harbor"','model_reasoning_effort="low"',
               'model_catalog_json='+json.dumps(str(root/'catalog.json')),'approval_policy="never"','sandbox_mode="read-only"',
               '[features]','apps=false','[model_providers.model-harbor]','name="Model Harbor"','wire_api="responses"',
               f'base_url="http://127.0.0.1:{port}/harbor/v1"','supports_websockets=false','requires_openai_auth=true',
               '[model_providers.model-harbor.http_headers]','X-Model-Harbor-Token='+json.dumps(token),
               '[mcp_servers.verification]','command='+json.dumps(shutil.which('python3')),
               'args=['+json.dumps(str(root/'marker-mcp.py'))+']']
        def table(path,values):
            rows=['['+'.'.join(json.dumps(k) for k in path)+']']
            for key,val in values.items():
                if not isinstance(val,dict):rows.append(json.dumps(key)+'='+json.dumps(val))
            for key,val in values.items():
                if isinstance(val,dict):rows+=table(path+[key],val)
            return rows
        lines+=table(['model_providers','baseten'],config['model_providers']['baseten'])
        (root/'config.toml').write_text('\n'.join(lines)+'\n')
        os.chmod(root/'config.toml',0o600)
        q=queue.Queue()
        proc=subprocess.Popen([shutil.which('codex'),'app-server','--stdio'],env=dict(os.environ,CODEX_HOME=str(root)),cwd=root,
                              stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
        def reader():
            for line in proc.stdout:
                try:q.put(json.loads(line))
                except ValueError:pass
        threading.Thread(target=reader,daemon=True).start()
        counter=0
        def rpc(method,params):
            nonlocal counter
            counter+=1
            proc.stdin.write(json.dumps({'id':counter,'method':method,'params':params})+'\n');proc.stdin.flush()
            deadline=time.monotonic()+60
            while time.monotonic()<deadline:
                value=q.get(timeout=max(.1,deadline-time.monotonic()))
                if value.get('id')==counter:
                    if 'error' in value:raise RuntimeError(json.dumps(value['error']))
                    return value['result']
            raise TimeoutError(method)
        threads={};memories={};report=[]
        def start(name,route):
            result=rpc('thread/start',{'cwd':str(root),'model':model_id(route),'modelProvider':'model-harbor','approvalPolicy':'never','sandbox':'read-only'})
            threads[name]=result['thread']['id'];memories[name]='copper'+os.urandom(4).hex()
        def turn(name,route,first=False,override=True):
            marker='harbor-'+os.urandom(8).hex();(root/'marker').write_text(marker)
            prompt=('The word for this conversation is '+memories[name]+'. Remember that exact word from this message. ' if first else 'Use the conversation word I gave you in my first message. ')
            prompt+='The external marker just changed. Your first action must be a fresh verification MCP read_marker call. Reply with the conversation word and NEW marker, nothing else. The conversation word is in the conversation; do not search files or use a shell. The only tool needed is verification.read_marker.'
            params={'threadId':threads[name],'input':[{'type':'text','text':prompt}],'effort':'low'}
            if override:params['model']=model_id(route)
            rpc('turn/start',params)
            deadline=time.monotonic()+240;messages=[];calls=0
            while time.monotonic()<deadline:
                event=q.get(timeout=max(.1,deadline-time.monotonic()));p=event.get('params',{})
                if p.get('threadId')!=threads[name]:continue
                if event.get('method')=='item/completed':
                    item=p.get('item',{})
                    if item.get('type')=='agentMessage':messages.append(item.get('text',''))
                    if item.get('type')=='mcpToolCall' and item.get('status')=='completed':calls+=1
                if event.get('method')=='turn/completed':
                    if p['turn']['status']!='completed':raise RuntimeError('Turn failed: '+json.dumps(p['turn'].get('error')))
                    break
            else:raise TimeoutError('turn')
            text='\n'.join(messages)
            row={'task':name,'model':route[1],'tool_calls':calls,'memory_verified':memories[name] in text,'marker_verified':marker in text,
                 'route_verified':status()['last_request']=={'provider':route[0],'model':route[1],'state':'completed'},'explicit_override':override}
            report.append(row);print(json.dumps(row),flush=True)
            if calls<1 or not all(row[k] for k in ('memory_verified','marker_verified','route_verified')):raise RuntimeError('Verification failed')
        try:
            rpc('initialize',{'clientInfo':{'name':'harbor_task_verification','version':'2'},'capabilities':{'experimentalApi':True}})
            proc.stdin.write('{"method":"initialized"}\n');proc.stdin.flush()
            listed=rpc('model/list',{})
            visible={e.get('model') for e in listed['data']}
            if not all(model_id(r) in visible for r in routes):raise RuntimeError('Picker entries missing')
            print(json.dumps({'picker_model_count':len(visible)}),flush=True)
            start('A',grok);start('B',codex)
            turn('A',grok,first=True,override=False)
            turn('B',codex,first=True,override=False)
            # Change A only; B's next turn omits model and must remember its original.
            turn('A',codex)
            turn('B',codex,override=False)
            if not args.without_baseten:
                start('C',kimi);turn('C',kimi,first=True,override=False)
                turn('A',grok)
                turn('C',deepseek)
                turn('B',codex,override=False)
                turn('C',deepseek,override=False)
            rpc('thread/unsubscribe',{'threadId':threads['A']})
            resumed=rpc('thread/resume',{'threadId':threads['A']})
            expected=grok if not args.without_baseten else codex
            if resumed['model']!=model_id(expected):raise RuntimeError('Resumed task did not retain model')
            turn('A',expected,override=False)
            after_auth=status()['baseten_auth']
            reads=after_auth['helper_reads']-before_auth['helper_reads']
            if reads>(0 if before_auth['state']=='ready' or args.without_baseten else 1):raise RuntimeError('Credential helper repeated')
            print(json.dumps({'passed':True,'tasks':len(threads),'turns':len(report),'codex_process_id':proc.pid,'restarts':0,
                              'baseten_credential_helper_reads':reads,'credential_reuses':after_auth['reuses']-before_auth['reuses']}),flush=True)
        finally:
            proc.terminate()
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
            if server:server.shutdown();server.server_close()

if __name__=='__main__':main()
