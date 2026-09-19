import copy
import io
import http.client
import json
import unittest
from unittest.mock import patch

import test_azure_provider as azure

bridge = azure.bridge


class AzureHistoryTests(unittest.TestCase):
    setUp = azure.AzureConnectionsTests.setUp
    stop = azure.AzureConnectionsTests.stop
    request = azure.AzureConnectionsTests.request
    configure = azure.AzureConnectionsTests.configure
    write_effort = azure.AzureConnectionsTests.write_effort

    def source(self):
        tools = [{'type': 'custom', 'name': 'patch', 'description': 'Apply a patch'}]
        alias = bridge.Translation({'tools': tools}).request['tools'][0]['name']
        nested = {'type': 'function_call', 'name': alias,
                  'arguments': '{"input":"opaque metadata, not a tool"}'}
        reasoning = {'type': 'reasoning', 'id': 'rs_fixture',
                     'encrypted_content': 'synthetic-ciphertext-\u0000-\u2603',
                     'summary': [{'type': 'summary_text', 'text': 'Synthetic summary'}],
                     'provider_extension': nested}
        compaction = {'type': 'compaction', 'id': 'cmp_fixture',
                      'encrypted_content': 'synthetic-compaction', 'extension': nested}
        unknown = {'type': 'future_provider_item', 'id': 'opaque_fixture', 'item': nested}
        return {'model': 'harbor/azure/coding-prod', 'stream': False,
                'tools': tools, 'include': ['message.output_text.logprobs'],
                'input': [{'role': 'user', 'content': 'Synthetic continuation'}, reasoning,
                          {'type': 'custom_tool_call', 'id': 'ctc_fixture', 'call_id': 'call_patch',
                           'name': 'patch', 'input': 'synthetic patch'},
                          {'type': 'custom_tool_call_output', 'id': 'ctco_fixture',
                           'call_id': 'call_patch', 'output': 'done'}, compaction, unknown]}

    def translated(self, source):
        self.configure()
        binding = bridge.azure_history_binding(
            {'provider': 'azure', 'model': 'coding-prod'}, self.endpoint)
        for item in source.get('input', []):
            bridge.remember_opaque_history(binding, item)
        return bridge.routed_request(source, {})[0]

    def test_azure_preserves_opaque_items_order_and_public_tool_links(self):
        source = self.source()
        original = copy.deepcopy(source)
        result = self.translated(source).request
        self.assertEqual(len(result['input']), 6)
        for index in [0, 1, 4, 5]:
            self.assertEqual(result['input'][index], source['input'][index])
        self.assertEqual([i.get('call_id') for i in result['input'][2:4]], ['call_patch', 'call_patch'])
        self.assertEqual([i['type'] for i in result['input'][2:4]], ['function_call', 'function_call_output'])
        self.assertFalse(any('id' in item for item in result['input'][2:4]))
        self.assertEqual(json.loads(result['input'][2]['arguments']), {'input': 'synthetic patch'})
        self.assertEqual(result['include'], ['message.output_text.logprobs', 'reasoning.encrypted_content'])
        self.assertEqual(source, original)
        source['include'].append('reasoning.encrypted_content')
        self.assertEqual(self.translated(source).request['include'], source['include'])

    def test_foreign_encrypted_history_is_removed_before_azure_dispatch(self):
        self.configure()
        source = self.source()
        original = copy.deepcopy(source)

        class Response(io.BytesIO):
            status = 200
            headers = {'Content-Type': 'application/json'}

        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.return_value = Response(
                b'{"status":"completed","output":[{"type":"message","content":[{"type":"output_text","text":"OK"}]}]}')
            status, body = self.request(path='/harbor/v1/responses', body=source,
                                        headers={'Authorization': 'Bearer synthetic-owner-token'})
            sent = json.loads(opener.return_value.open.call_args.args[0].data)

        self.assertEqual((status, body['status']), (200, 'completed'))
        self.assertEqual(opener.return_value.open.call_count, 1)
        self.assertFalse(any(item.get('type') in ('reasoning', 'compaction')
                             or 'encrypted_content' in item for item in sent['input']))
        self.assertEqual(sent['input'][0], source['input'][0])
        self.assertEqual([item.get('call_id') for item in sent['input'][1:3]],
                         ['call_patch', 'call_patch'])
        self.assertEqual(sent['input'][-1], source['input'][-1])
        self.assertEqual(source, original)

    def test_foreign_encrypted_history_is_removed_before_stream_dispatch(self):
        self.configure()
        source = self.source()
        source['stream'] = True

        class Response(io.BytesIO):
            status = 200
            headers = {'Content-Type': 'text/event-stream'}

        terminal = {'type': 'response.completed', 'response': {'status': 'completed',
                    'output': [{'type': 'message', 'content': [
                        {'type': 'output_text', 'text': 'OK'}]}]}}
        wire = ('event: response.completed\ndata: ' + json.dumps(terminal) + '\n\n').encode()
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.return_value = Response(wire)
            connection = http.client.HTTPConnection(
                '127.0.0.1', self.server.server_port, timeout=4)
            try:
                connection.request('POST', '/harbor/v1/responses', json.dumps(source),
                                   {'Authorization': 'Bearer synthetic-owner-token'})
                response = connection.getresponse()
                payload = response.read()
            finally:
                connection.close()
            sent = json.loads(opener.return_value.open.call_args.args[0].data)

        self.assertEqual(response.status, 200)
        self.assertIn(b'response.completed', payload)
        self.assertNotIn(b'response.failed', payload)
        self.assertEqual(opener.return_value.open.call_count, 1)
        self.assertFalse(any(item.get('type') in ('reasoning', 'compaction')
                             or 'encrypted_content' in item for item in sent['input']))
        self.assertEqual([item.get('call_id') for item in sent['input'][1:3]],
                         ['call_patch', 'call_patch'])

    def test_ciphertext_from_another_azure_binding_is_removed(self):
        self.configure()
        source = self.source()
        protected = [item for item in source['input']
                     if item.get('type') in ('reasoning', 'compaction')]
        other_bindings = [
            bridge.azure_history_binding(
                {'provider': 'azure', 'model': 'coding-prod'},
                'https://other.openai.azure.com/openai/v1'),
            bridge.azure_history_binding(
                {'provider': 'azure', 'model': 'other-deployment'}, self.endpoint),
        ]
        for binding in other_bindings:
            for item in protected:
                bridge.remember_opaque_history(binding, item)

        sent = bridge.routed_request(source, {})[0].request['input']

        self.assertFalse(any(item.get('type') in ('reasoning', 'compaction') for item in sent))
        self.assertEqual(sent[0], source['input'][0])
        self.assertEqual([item.get('call_id') for item in sent[1:3]],
                         ['call_patch', 'call_patch'])
        self.assertEqual(sent[-1], source['input'][-1])

    def test_json_and_sse_preserve_opaque_fields_but_translate_actual_tools(self):
        source = self.source()
        translation = self.translated(source)
        opaque = [source['input'][i] for i in [1, 4, 5]]
        alias = translation.request['tools'][0]['name']
        tool = {'type': 'function_call', 'id': 'fc_fixture', 'call_id': 'call_patch',
                'name': alias, 'arguments': '{"input":"synthetic patch"}'}
        response = {'object': 'response', 'status': 'completed',
                    'output': opaque + [tool], 'metadata': {'item': tool},
                    'usage': {'input_tokens': 12, 'output_tokens': 3}}
        result = translation.output(response)
        self.assertEqual(result['output'][:3], opaque)
        self.assertEqual(result['metadata'], response['metadata'])
        self.assertEqual(result['output'][3], {'type': 'custom_tool_call', 'id': 'fc_fixture',
                         'call_id': 'call_patch', 'name': 'patch', 'input': 'synthetic patch'})
        for item in opaque:
            for kind in ['response.output_item.added', 'response.output_item.done']:
                event = {'type': kind, 'item': item, 'sequence_number': 7}
                block = ('event: ' + kind + '\nid: fixture\ndata: ' + json.dumps(event)).encode()
                self.assertEqual(translation.event(block), block + b'\n\n')
        terminal = {'type': 'response.completed', 'response': response, 'extension': {'item': tool}}
        encoded = translation.event(('data: ' + json.dumps(terminal)).encode())
        decoded = json.loads(encoded.split(b'data: ', 1)[1])
        self.assertEqual(decoded['response'], result)
        self.assertEqual(decoded['extension'], terminal['extension'])
        self.assertEqual(translation.response_status, 'completed')
        self.assertEqual(translation.usage, response['usage'])

    def test_unfamiliar_event_with_tool_shaped_metadata_is_opaque(self):
        translation = self.translated(self.source())
        alias = next(iter(translation.names))
        event = {'type': 'response.future_event', 'item': {'type': 'function_call',
                 'name': alias, 'arguments': '{"input":"opaque"}'}}
        block = ('data: ' + json.dumps(event)).encode()
        self.assertEqual(translation.event(block), block + b'\n\n')

    def test_reasoning_named_like_dispatcher_is_never_suppressed(self):
        source = self.source()
        source['tools'] = [{'type': 'namespace', 'name': 'fixture', 'tools': [
            {'type': 'function', 'name': 'read', 'parameters': {'type': 'object'}}]}]
        translation = self.translated(source)
        item = dict(source['input'][1], name=next(iter(translation.groups)))
        block = ('data: ' + json.dumps({'type': 'response.output_item.added', 'item': item})).encode()
        self.assertEqual(translation.event(block), block + b'\n\n')
        self.assertEqual(translation.pending, set())

    def test_known_ciphertext_is_sent_once_and_rejection_is_not_repaired(self):
        source = self.source()
        self.configure()
        binding = bridge.azure_history_binding(
            {'provider': 'azure', 'model': 'coding-prod'}, self.endpoint)
        for item in source['input']:
            bridge.remember_opaque_history(binding, item)
        failure = {'error': {'code': 'invalid_encrypted_content',
                            'message': 'Synthetic encrypted history is invalid'}}
        error = bridge.urllib.error.HTTPError(self.endpoint + '/responses', 400, 'fixture', {},
                                              io.BytesIO(json.dumps(failure).encode()))
        with patch.object(bridge.urllib.request, 'build_opener') as opener:
            opener.return_value.open.side_effect = error
            status, body = self.request(path='/harbor/v1/responses', body=source,
                                       headers={'Authorization': 'Bearer synthetic-owner-token'})
            self.assertEqual(opener.return_value.open.call_count, 1)
            sent = json.loads(opener.return_value.open.call_args.args[0].data)
        self.assertEqual((status, body), (400, failure))
        for index in [1, 4, 5]:
            self.assertEqual(sent['input'][index], source['input'][index])

    def test_handler_roundtrips_json_and_streamed_reasoning_into_next_tool_turn(self):
        self.configure()
        source = self.source()
        opaque = source['input'][1]
        tool = {'type': 'function_call', 'id': 'fc_read', 'call_id': 'call_read',
                'name': 'read_marker', 'arguments': '{}'}
        response = {'object': 'response', 'status': 'completed', 'output': [opaque, tool]}
        self.write_effort('high')
        for stream in [False, True]:
            with self.subTest(stream=stream):
                class Response(io.BytesIO):
                    status = 200
                    headers = {'Content-Type': 'text/event-stream' if stream else 'application/json'}
                events = [{'type': 'response.output_item.done', 'item': item} for item in response['output']]
                events.append({'type': 'response.completed', 'response': response})
                wire = (b''.join(('data: ' + json.dumps(event) + '\n\n').encode() for event in events)
                        if stream else json.dumps(response).encode())
                first = {'model': source['model'], 'stream': stream, 'input': source['input'][:1]}
                conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=4)
                with patch.object(bridge.urllib.request, 'build_opener') as opener:
                    opener.return_value.open.return_value = Response(wire)
                    try:
                        conn.request('POST', '/harbor/v1/responses', json.dumps(first),
                                     {'Authorization': 'Bearer synthetic-owner-token'})
                        received = conn.getresponse()
                        self.assertEqual(received.status, 200)
                        payload = received.read()
                    finally:
                        conn.close()
                if stream:
                    decoded = [json.loads(block.split(b'data: ', 1)[1])
                               for block in payload.split(b'\n\n') if block]
                    self.assertEqual(decoded, events)
                    output = decoded[-1]['response']['output']
                else:
                    self.assertEqual(json.loads(payload), response)
                    output = json.loads(payload)['output']
                followup = dict(first, stream=False, input=first['input'] + output + [
                    {'type': 'function_call_output', 'call_id': 'call_read', 'output': 'marker-42'}])
                with patch.object(bridge.urllib.request, 'build_opener') as opener:
                    Response.headers = {'Content-Type': 'application/json'}
                    opener.return_value.open.return_value = Response(b'{"status":"completed","output":[]}')
                    status, _ = self.request(path='/harbor/v1/responses', body=followup,
                                            headers={'Authorization': 'Bearer synthetic-owner-token'})
                    self.assertEqual(status, 200)
                    sent = json.loads(opener.return_value.open.call_args.args[0].data)
                    self.assertEqual(opener.return_value.open.call_count, 1)
                self.assertEqual(sent['input'][1], opaque)
                self.assertEqual(sent['input'][2], {k: v for k, v in tool.items() if k != 'id'})
                self.assertEqual(sent['input'][3], followup['input'][3])

    def test_other_provider_history_policy_stays_unchanged(self):
        source = self.source()
        for native in [False, True]:
            result = bridge.Translation(source, native_tools=native).request
            self.assertFalse(any(item.get('type') == 'reasoning' for item in result['input']))

    def test_malformed_include_fails_before_dispatch(self):
        source = self.source()
        for invalid in ['reasoning.encrypted_content', None, [123]]:
            source['include'] = invalid
            with self.assertRaisesRegex(ValueError, 'include'):
                self.translated(source)


if __name__ == '__main__':
    unittest.main()
