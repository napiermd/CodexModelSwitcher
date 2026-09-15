"""Local Responses adapter. Credentials are supplied per request, never stored here."""
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

ADDRESS = ('127.0.0.1', 48118)
MAX_BODY = 32 * 1024 * 1024


def flat_name(namespace, name, custom=False):
    identity = json.dumps([namespace, name, custom], separators=(',', ':')).encode()
    return 'cms_' + hashlib.sha256(identity).hexdigest()[:24] + '_' + re.sub(r'[^A-Za-z0-9_-]', '_', name)[:30]


class Translation:
    def __init__(self, source):
        self.names = {}
        self.groups = {}
        self.pending = set()
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
        if self.path != '/health':
            return self.error(404, 'Not found')
        body = b'{"adapter":"codex-model-switcher-grok","version":1}'
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        started = False
        try:
            if self.path != '/v1/responses':
                return self.error(404, 'Only Responses requests are supported')
            if self.headers.get('Origin') or self.headers.get('Transfer-Encoding'):
                return self.error(400, 'Browser origins and transfer-encoded requests are not supported')
            authorization = self.headers.get('Authorization', '')
            if not authorization.startswith('Bearer ') or len(authorization) < 12:
                return self.error(401, 'A provider credential is required')
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
            translation = Translation(json.loads(raw))
            request = urllib.request.Request('https://api.x.ai/v1/responses',
                data=json.dumps(translation.request, separators=(',', ':')).encode(),
                headers={'Authorization': authorization, 'Content-Type': 'application/json',
                         'Accept': 'text/event-stream' if translation.request.get('stream') else 'application/json'})
            opener = urllib.request.build_opener(NoRedirect)
            with opener.open(request, timeout=180) as upstream:
                content_type = upstream.headers.get('Content-Type', 'application/json')
                self.send_response(upstream.status)
                self.send_header('Content-Type', content_type)
                self.send_header('Cache-Control', 'no-cache')
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
                    self.wfile.write(json.dumps(translation.output(json.load(upstream))).encode())
        except urllib.error.HTTPError as error:
            if not started:
                # Provider errors contain schema diagnostics, never request headers.
                body = error.read(16384)
                self.error(error.code, body.decode('utf-8', errors='replace'))
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            self.close_connection = True
        except Exception as error:
            if not started:
                self.error(502, 'Adapter failed: ' + type(error).__name__)
            self.close_connection = True


if __name__ == '__main__':
    server = http.server.ThreadingHTTPServer(ADDRESS, Handler)
    server.daemon_threads = True
    parent_pid = os.getppid()
    def watch_parent():
        while os.getppid() == parent_pid:
            time.sleep(2)
        server.shutdown()
    threading.Thread(target=watch_parent, daemon=True).start()
    server.serve_forever()
