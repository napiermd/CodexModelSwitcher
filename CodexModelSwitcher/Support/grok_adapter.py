"""Local Responses bridge for Grok OAuth and direct Baseten inference."""
import copy
import hashlib
import http.server
import json
import os
import threading
import time
import re
import socket
import urllib.error
import urllib.request
import datetime
import hmac
import pathlib
import secrets
import subprocess
import tomllib
from collections import OrderedDict

ADDRESS = ('127.0.0.1', int(os.environ.get('MODEL_HARBOR_PORT', '48118')))
MAX_BODY = 32 * 1024 * 1024
OAUTH_BASE = 'https://cli-chat-proxy.grok.com/v1'
TOKEN_PATH = pathlib.Path(os.environ.get('MODEL_HARBOR_TOKEN_PATH', str(pathlib.Path.home() / '.codex/model-harbor-bridge-token')))
AUTH_PATH = pathlib.Path.home() / '.grok/auth.json'
AUTH_LOCK = threading.Lock()
CONFIG_DIR = pathlib.Path(os.environ.get('MODEL_HARBOR_CONFIG_DIR', str(pathlib.Path.home() / '.codex')))
ROUTE_LOCK = threading.Lock()
TURN_ROUTES = OrderedDict()
LAST_ROUTE = None



def grok_binary():
    for path in [pathlib.Path.home() / '.local/bin/grok', pathlib.Path('/opt/homebrew/bin/grok'), pathlib.Path('/usr/local/bin/grok')]:
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    raise ValueError('Install the official Grok client before signing in.')


def session():
    if not AUTH_PATH.exists():
        raise ValueError('Sign in to Grok in Model Harbor.')
    sessions = [a for a in json.loads(AUTH_PATH.read_text()).values()
                if a.get('oidc_issuer') == 'https://auth.x.ai' and a.get('auth_mode') == 'oidc' and a.get('key')]
    if len(sessions) != 1:
        raise ValueError('Select a Grok account by signing in again.')
    return sessions[0]


def expired(auth):
    expiry = auth.get('expires_at')
    if not expiry:
        return True
    return datetime.datetime.fromisoformat(expiry.replace('Z', '+00:00')).timestamp() < time.time() + 90


def oauth_headers():
    with AUTH_LOCK:
        auth = session()
        if expired(auth):
            # The official client owns refresh rotation and its cross-process lock.
            env = {k: v for k, v in os.environ.items() if k not in ('XAI_API_KEY', 'GROK_DEPLOYMENT_KEY', 'GROK_AUTH_PATH', 'GROK_HOME')}
            subprocess.run([grok_binary(), 'models'], env=env, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45, check=True)
            auth = session()
            if expired(auth):
                raise ValueError('Grok could not refresh your session. Sign in again.')
        version = subprocess.check_output([grok_binary(), '--version'], timeout=5, text=True).split()[1]
        return {'Authorization': 'Bearer ' + auth['key'], 'X-XAI-Token-Auth': 'xai-grok-cli',
                'x-grok-client-version': version, 'User-Agent': 'ModelHarbor/1.0'}


def ensure_bridge_token():
    TOKEN_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        fd = os.open(TOKEN_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        TOKEN_PATH.chmod(0o600)
        return
    with os.fdopen(fd, 'w') as f:
        f.write(secrets.token_urlsafe(48))


def oauth_models():
    req = urllib.request.Request(OAUTH_BASE + '/models', headers=oauth_headers())
    with urllib.request.build_opener(NoRedirect).open(req, timeout=30) as response:
        entries = json.load(response)['data']
    models = []
    for entry in entries:
        if entry.get('api_backend') != 'responses':
            continue
        models.append({'slug': entry['id'], 'display_name': entry.get('name', entry['id']),
                       'description': entry.get('description', ''), 'base_instructions': 'You are a coding assistant. Follow the user instructions and use the available tools.',
                       'default_reasoning_level': entry.get('reasoning_effort', 'high'),
                       'supported_reasoning_levels': [{'effort': e['value'], 'description': e.get('description', e['value'])} for e in entry.get('reasoning_efforts', [])],
                       'shell_type': 'shell_command', 'visibility': 'list', 'supported_in_api': True,
                       'priority': len(models), 'context_window': entry.get('context_window', 128000),
                       'max_context_window': entry.get('context_window', 128000), 'input_modalities': ['text', 'image'],
                       'support_verbosity': False, 'truncation_policy': {'mode': 'tokens', 'limit': 10000},
                       'supports_parallel_tool_calls': False, 'experimental_supported_tools': []})
    if not models:
        raise ValueError('This Grok account has no Responses models available.')
    return {'email': session().get('email', 'Signed in'), 'models': models}


def flat_name(namespace, name, custom=False):
    identity = json.dumps([namespace, name, custom], separators=(',', ':')).encode()
    return 'cms_' + hashlib.sha256(identity).hexdigest()[:24] + '_' + re.sub(r'[^A-Za-z0-9_-]', '_', name)[:30]


def selected_route():
    data = json.loads((CONFIG_DIR / 'model-switcher.json').read_text())
    selection = data.get('selectedModel') or {}
    provider, model = selection.get('serviceID'), selection.get('modelID')
    if provider not in ('grok-oauth', 'baseten'):
        raise ValueError('Choose a Grok or Baseten model in Model Harbor.')
    service = next((s for s in data['services'] if s['id'] == provider), None)
    if not service or model not in [m['id'] for m in service['models']]:
        raise ValueError('The selected model is no longer available. Choose another model in Model Harbor.')
    return {'provider': provider, 'model': model}


def route_for_turn(source, headers):
    # Codex sends a canonical turn ID across inference/tool-result requests.
    metadata = source.get('client_metadata') or {}
    nested = metadata.get('x-codex-turn-metadata') or headers.get('x-codex-turn-metadata', '{}')
    if isinstance(nested, str):
        try:
            nested = json.loads(nested)
        except ValueError:
            nested = {}
    turn_id = nested.get('turn_id') or metadata.get('turn_id')
    thread_id = nested.get('thread_id') or metadata.get('thread_id') or metadata.get('session_id', '')
    key = (str(thread_id), str(turn_id)) if turn_id else None
    with ROUTE_LOCK:
        if key in TURN_ROUTES:
            TURN_ROUTES.move_to_end(key)
            return dict(TURN_ROUTES[key])
        route = selected_route()
        if key:
            TURN_ROUTES[key] = route
            # Bound idle history; active turns are touched on every tool-result request.
            if len(TURN_ROUTES) > 4096:
                TURN_ROUTES.popitem(last=False)
        return dict(route)


def baseten_headers():
    config = tomllib.loads((CONFIG_DIR / 'config.toml').read_text())
    provider = config.get('model_providers', {}).get('baseten', {})
    if provider.get('base_url', '').rstrip('/') != 'https://inference.baseten.co/v1':
        raise ValueError('Baseten must use the direct inference.baseten.co endpoint.')
    auth = provider.get('auth') or {}
    if auth.get('command'):
        # Reuse the existing local provider's credential helper (e.g. 1Password).
        result = subprocess.run([auth['command'], *auth.get('args', [])],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                timeout=min(auth.get('timeout_ms', 30000) / 1000, 60), check=True)
        key = result.stdout.strip()
    elif provider.get('env_key'):
        key = os.environ.get(provider['env_key'], '').strip()
    else:
        raise ValueError('Baseten authentication is not configured.')
    if not key or '\n' in key or '\r' in key:
        raise ValueError('Baseten authentication returned no usable credential.')
    return {'Authorization': 'Bearer ' + key, 'User-Agent': 'ModelHarbor/1.0'}


def routed_request(source, headers):
    if source.get('model') != 'harbor-selected':
        raise ValueError('Choose Model Harbor selection in Codex to use live switching.')
    if source.get('previous_response_id'):
        raise ValueError('Model Harbor needs full conversation history when switching providers.')
    route = route_for_turn(source, headers)
    source = copy.deepcopy(source)
    source['model'] = route['model']
    source['store'] = False
    if route['provider'] == 'baseten':
        reasoning = source.get('reasoning')
        if isinstance(reasoning, dict) and reasoning.get('effort') == 'xhigh':
            reasoning['effort'] = 'high'
        upstream_headers = baseten_headers()
        base = 'https://inference.baseten.co/v1'
    else:
        upstream_headers = oauth_headers()
        base = OAUTH_BASE
    return Translation(source), upstream_headers, base, route


class Translation:
    def __init__(self, source):
        self.names = {}
        self.groups = {}
        self.pending = set()
        self.response_status = None
        self.request = copy.deepcopy(source)
        if str(source.get('model', '')).startswith('grok-4.20'):
            self.request.pop('reasoning', None)
        self.request['tools'] = self.tools(source.get('tools', []))
        self.request['input'] = self.input_items(source.get('input', []))
        if isinstance(source.get('tool_choice'), dict):
            self.request['tool_choice'] = self.choice(source['tool_choice'])

    def tools(self, source, namespace=None):
        result = []
        for original in source:
            tool = copy.deepcopy(original)
            kind = tool.get('type')
            if kind == 'namespace':
                child_namespace = tool['name'] if namespace is None else namespace + '.' + tool['name']
                children = tool.get('tools', [])
                if any(child.get('type') not in ('function', 'custom') for child in children):
                    raise ValueError('Unsupported nested namespace tool')
                alias = flat_name(child_namespace, 'dispatch')
                self.groups[alias] = (child_namespace, {child['name']: child for child in children})
                result.append({'type': 'function', 'name': alias,
                    'description': (tool.get('description', '') + '\nCall a tool in namespace ' + child_namespace +
                        '. Put its name in tool and its JSON-encoded arguments in arguments. For a custom tool, put the raw input string in arguments.\nTools:\n' + json.dumps(children, separators=(',', ':'))),
                    'parameters': {'type': 'object', 'properties': {
                        'tool': {'type': 'string', 'enum': [child['name'] for child in children]},
                        'arguments': {'type': 'string'}}, 'required': ['tool', 'arguments'], 'additionalProperties': False}})
                continue
            if kind in ('function', 'custom'):
                name = tool['name']
                custom = kind == 'custom'
                if namespace or custom:
                    alias = flat_name(namespace, name, custom)
                    self.names[alias] = (namespace, name, custom)
                    tool['name'] = alias
                if custom:
                    tool = {'type': 'function', 'name': tool['name'],
                            'description': tool.get('description', '') + '\nPass the exact raw tool input in the input field.',
                            'parameters': {'type': 'object', 'properties': {'input': {'type': 'string'}},
                                           'required': ['input'], 'additionalProperties': False}}
                tool.pop('defer_loading', None)
            if kind == 'web_search':
                tool.pop('external_web_access', None)
            result.append(tool)
        return result

    def input_items(self, source):
        if isinstance(source, str):
            return source
        # Codex replays opaque reasoning blobs in a format xAI cannot decode.
        # Conversation text, function calls, and tool results remain in the history.
        result = [copy.deepcopy(item) for item in source if item.get('type') != 'reasoning']
        for item in result:
            if item.get('type') in ('function_call', 'custom_tool_call'):
                custom = item['type'] == 'custom_tool_call'
                namespace = item.pop('namespace', None)
                group = flat_name(namespace, 'dispatch') if namespace else None
                if group in self.groups:
                    arguments = item.pop('input', '') if custom else item.get('arguments', '{}')
                    item['arguments'] = json.dumps({'tool': item['name'], 'arguments': arguments})
                    item['type'] = 'function_call'
                    item['name'] = group
                else:
                    if namespace or custom:
                        item['name'] = flat_name(namespace, item['name'], custom)
                    if custom:
                        item['type'] = 'function_call'
                        item['arguments'] = json.dumps({'input': item.pop('input', '')})
            elif item.get('type') == 'custom_tool_call_output':
                item['type'] = 'function_call_output'
        return result

    def choice(self, choice):
        result = copy.deepcopy(choice)
        namespace = result.pop('namespace', None)
        if 'name' in result and namespace:
            result['name'] = flat_name(namespace, result['name'])
        if 'tools' in result:
            result['tools'] = [self.choice(tool) for tool in result['tools']]
        return result

    def output(self, obj):
        if isinstance(obj, list):
            return [self.output(x) for x in obj]
        if not isinstance(obj, dict):
            return obj
        result = {key: self.output(value) for key, value in obj.items()}
        if result.get('type') == 'function_call' and result.get('name') in self.groups:
            namespace, children = self.groups[result['name']]
            call = json.loads(result.get('arguments') or '{}')
            if call.get('tool') not in children:
                raise ValueError('Dispatcher returned an unknown tool')
            result['namespace'] = namespace
            result['name'] = call['tool']
            result['arguments'] = call['arguments']
            if children[call['tool']]['type'] == 'custom':
                result['type'] = 'custom_tool_call'
                result['input'] = result.pop('arguments')
        if result.get('type') == 'function_call' and result.get('name') in self.names:
            namespace, name, custom = self.names[result['name']]
            result['name'] = name
            if namespace:
                result['namespace'] = namespace
            if custom:
                result['type'] = 'custom_tool_call'
                arguments = result.pop('arguments', '{}')
                result['input'] = json.loads(arguments or '{}').get('input', '')
        return result

    def event(self, block):
        lines = block.decode('utf-8').splitlines()
        payload = '\n'.join(line[5:].lstrip() for line in lines if line.startswith('data:'))
        if not payload or payload == '[DONE]':
            return block + b'\n\n'
        event = json.loads(payload)
        if event.get('type') in ('response.completed', 'response.failed', 'response.incomplete'):
            self.response_status = event.get('response', {}).get('status') or event['type'].split('.')[-1]
        item = event.get('item', {})
        if event.get('type') == 'response.output_item.added' and item.get('name') in self.groups:
            self.pending.add(item.get('id'))
            return b''
        if event.get('type', '').startswith('response.function_call_arguments.') and event.get('item_id') in self.pending:
            return b''
        obj = self.output(event)
        retained = [line for line in lines if not line.startswith('data:')]
        retained.append('data: ' + json.dumps(obj, separators=(',', ':')))
        return ('\n'.join(retained) + '\n\n').encode()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def setup(self):
        super().setup()
        self.connection.settimeout(180)

    def log_message(self, *_):
        pass

    def error(self, code, message):
        body = json.dumps({'error': message}).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_GET(self):
        if self.path == '/harbor/status':
            if self.headers.get('Origin') or not self.local_authorized():
                return self.error(401, 'Local authorization required')
            try:
                body = json.dumps({'selected': selected_route(), 'last_request': LAST_ROUTE}).encode()
            except (ValueError, OSError, KeyError):
                return self.error(409, 'Choose a Grok or Baseten model in Model Harbor.')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == '/oauth/status':
            if self.headers.get('Origin') or not self.local_authorized():
                return self.error(401, 'Local authorization required')
            try:
                body = json.dumps(oauth_models()).encode()
            except Exception:
                return self.error(401, 'Grok sign-in needs attention. Sign in again in Model Harbor.')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path != '/health':
            return self.error(404, 'Not found')
        body = b'{"adapter":"codex-model-switcher-grok","version":1}'
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def local_authorized(self):
        try:
            return hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + TOKEN_PATH.read_text().strip())
        except OSError:
            return False

    def do_POST(self):
        global LAST_ROUTE
        started = False
        try:
            oauth = self.path == '/oauth/v1/responses'
            routed = self.path == '/harbor/v1/responses'
            if self.path not in ('/v1/responses', '/oauth/v1/responses', '/harbor/v1/responses'):
                return self.error(404, 'Only Responses requests are supported')
            if self.headers.get('Origin') or self.headers.get('Transfer-Encoding'):
                return self.error(400, 'Browser origins and transfer-encoded requests are not supported')
            authorization = self.headers.get('Authorization', '')
            if not authorization.startswith('Bearer ') or len(authorization) < 12:
                return self.error(401, 'A provider credential is required')
            if (oauth or routed) and not self.local_authorized():
                return self.error(401, 'Local authorization required')
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_BODY:
                return self.error(413, 'Request body exceeds the adapter limit')
            raw = self.rfile.read(length)
            encoding = self.headers.get('Content-Encoding', 'identity')
            if encoding == 'zstd':
                from compression import zstd
                raw = zstd.ZstdDecompressor().decompress(raw, max_length=MAX_BODY + 1)
            elif encoding != 'identity':
                return self.error(415, 'Unsupported content encoding')
            if len(raw) > MAX_BODY:
                return self.error(413, 'Decoded body exceeds the adapter limit')
            source = json.loads(raw)
            route = None
            if routed:
                translation, headers, base, route = routed_request(source, self.headers)
            else:
                translation = Translation(source)
                headers = oauth_headers() if oauth else {'Authorization': authorization}
                base = OAUTH_BASE if oauth else 'https://api.x.ai/v1'
            if route:
                with ROUTE_LOCK:
                    LAST_ROUTE = dict(route, state='started')
            headers.update({'Content-Type': 'application/json',
                            'Accept': 'text/event-stream' if translation.request.get('stream') else 'application/json'})
            request = urllib.request.Request(base + '/responses',
                data=json.dumps(translation.request, separators=(',', ':')).encode(),
                headers=headers)
            opener = urllib.request.build_opener(NoRedirect)
            with opener.open(request, timeout=180) as upstream:
                content_type = upstream.headers.get('Content-Type', 'application/json')
                self.send_response(upstream.status)
                self.send_header('Content-Type', content_type)
                self.send_header('Cache-Control', 'no-cache')
                if route:
                    self.send_header('X-Model-Harbor-Provider', route['provider'])
                    self.send_header('X-Model-Harbor-Model', route['model'])
                self.send_header('Connection', 'close')
                self.end_headers()
                self.close_connection = True
                started = True
                if 'text/event-stream' in content_type:
                    block = []
                    for line in upstream:
                        if line.strip():
                            block.append(line.rstrip(b'\r\n'))
                        elif block:
                            self.wfile.write(translation.event(b'\n'.join(block)))
                            self.wfile.flush()
                            block = []
                    if block:
                        self.wfile.write(translation.event(b'\n'.join(block)))
                else:
                    response = json.load(upstream)
                    translation.response_status = response.get('status')
                    self.wfile.write(json.dumps(translation.output(response)).encode())
                if route:
                    with ROUTE_LOCK:
                        LAST_ROUTE = dict(route, state=translation.response_status or 'finished')
        except urllib.error.HTTPError as error:
            if not started:
                # Provider errors contain schema diagnostics, never request headers.
                body = error.read(16384)
                self.error(error.code, body.decode('utf-8', errors='replace'))
        except ValueError as error:
            if not started:
                self.error(400, str(error))
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            self.close_connection = True
        except Exception as error:
            if not started:
                self.error(502, 'Adapter failed: ' + type(error).__name__)
            self.close_connection = True


if __name__ == '__main__':
    ensure_bridge_token()
    server = http.server.ThreadingHTTPServer(ADDRESS, Handler)
    server.daemon_threads = True
    parent_pid = os.getppid()
    def watch_parent():
        while os.getppid() == parent_pid:
            time.sleep(2)
        server.shutdown()
    threading.Thread(target=watch_parent, daemon=True).start()
    server.serve_forever()
