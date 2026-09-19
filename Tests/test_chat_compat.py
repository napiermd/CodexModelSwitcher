import copy
import importlib.util
import json
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "experiments/chat_compat.py"
SPEC = importlib.util.spec_from_file_location("chat_compat", MODULE_PATH)
compat = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compat)


def sse(data, event=None, newline="\n"):
    lines = []
    if event:
        lines.append(f"event: {event}")
    text = "[DONE]" if data == "[DONE]" else json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    lines.append("data: " + text)
    return (newline.join(lines) + newline * 2).encode()


def events(blocks):
    result = []
    pending = b"".join(blocks).replace(b"\r\n", b"\n")
    for block in pending.split(b"\n\n"):
        if not block:
            continue
        payload = b"\n".join(line[5:].lstrip() for line in block.splitlines() if line.startswith(b"data:"))
        if payload:
            result.append(json.loads(payload))
    return result


class ChatCompatibilityTests(unittest.TestCase):
    def translation(self, **changes):
        source = {
            "model": "vendor/account/exact-model:v7",
            "input": [{"role": "user", "content": "Say hello"}],
        }
        source.update(changes)
        transport = {
            "account": "acct/Prod-β",
            "base_url": "https://fixture.invalid/v1",
            "headers": {"X-Account": "Case-Sensitive-Value"},
        }
        return source, transport, compat.translate_request(source, transport=transport)

    def test_golden_text_json_round_trip_preserves_exact_model_and_transport(self):
        source, transport, translated = self.translation(
            instructions="Be concise.", temperature=0.25, max_output_tokens=40,
        )
        original = copy.deepcopy(source)
        self.assertEqual(translated.body, {
            "model": "vendor/account/exact-model:v7",
            "messages": [
                {"role": "developer", "content": "Be concise."},
                {"role": "user", "content": "Say hello"},
            ],
            "temperature": 0.25,
            "max_completion_tokens": 40,
        })
        self.assertEqual(translated.transport, transport)
        self.assertIsNot(translated.transport, transport)
        self.assertEqual(source, original)

        result = translated.response({
            "id": "chatcmpl-golden", "object": "chat.completion", "created": 19,
            "model": "provider-reported-alias",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": "Hello, café 👋",
            }}],
        })
        self.assertEqual(result, {
            "id": "chatcmpl-golden", "object": "response", "created_at": 19,
            "model": "vendor/account/exact-model:v7", "status": "completed",
            "output": [{
                "id": "chatcmpl-golden:message:0", "type": "message", "status": "completed",
                "role": "assistant", "content": [{
                    "type": "output_text", "text": "Hello, café 👋", "annotations": [],
                }],
            }],
        })

    def test_golden_function_tool_call_and_result_history_round_trip(self):
        source, _, translated = self.translation(
            tools=[{
                "type": "function", "name": "lookup", "description": "Find a record",
                "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}},
                "strict": True,
            }],
            tool_choice={"type": "function", "name": "lookup"},
            parallel_tool_calls=False,
        )
        self.assertEqual(translated.body["tools"], [{"type": "function", "function": {
            "name": "lookup", "description": "Find a record",
            "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}},
            "strict": True,
        }}])
        self.assertEqual(translated.body["tool_choice"], {"type": "function", "function": {"name": "lookup"}})
        self.assertFalse(translated.body["parallel_tool_calls"])

        response = translated.response({
            "id": "chatcmpl-tool", "created": 20,
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "tool_calls": [{
                    "index": 0, "id": "call_lookup", "type": "function",
                    "function": {"name": "lookup", "arguments": "{\"id\":7}"},
                }],
            }}],
        })
        self.assertEqual(response["output"], [{
            "id": "call_lookup", "type": "function_call", "status": "completed",
            "call_id": "call_lookup", "name": "lookup", "arguments": "{\"id\":7}",
        }])

        continued = compat.translate_request({
            "model": source["model"], "tools": source["tools"], "input": [
                {"role": "user", "content": "Find seven"},
                response["output"][0],
                {"type": "function_call_output", "call_id": "call_lookup", "output": "{\"name\":\"Ada\"}"},
                {"role": "user", "content": "Summarize it"},
            ],
        })
        self.assertEqual(continued.body["messages"], [
            {"role": "user", "content": "Find seven"},
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_lookup", "type": "function",
                "function": {"name": "lookup", "arguments": "{\"id\":7}"},
            }]},
            {"role": "tool", "tool_call_id": "call_lookup", "content": "{\"name\":\"Ada\"}"},
            {"role": "user", "content": "Summarize it"},
        ])

    def test_thinking_model_replays_reasoning_on_its_assistant_turn(self):
        source = {
            "model": "zai-org/GLM-5.3",
            "input": [
                {"role": "user", "content": "Inspect it"},
                {"type": "reasoning", "content": [
                    {"type": "reasoning_text", "text": "first"},
                    {"type": "reasoning_text", "text": " second"},
                ]},
                {"type": "function_call", "call_id": "c1", "name": "inspect", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "c1", "output": "done"},
            ],
        }
        original = copy.deepcopy(source)
        messages = compat.translate_request(source).body["messages"]
        self.assertEqual(messages[1], {
            "role": "assistant", "content": None, "reasoning_content": "first second",
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": "inspect", "arguments": "{}"}}],
        })
        self.assertEqual(source, original)

    def test_thinking_model_maps_chat_reasoning_back_to_nonvisible_history(self):
        translated = compat.translate_request({
            "model": "moonshotai/Kimi-K3", "input": [{"role": "user", "content": "Answer"}],
        })
        response = translated.response({
            "id": "chatcmpl-reasoning",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "reasoning_content": "private thought", "content": "answer",
            }}],
        })
        self.assertEqual(response["output"][0], {
            "id": "chatcmpl-reasoning:reasoning:0", "type": "reasoning", "status": "completed",
            "content": [{"type": "reasoning_text", "text": "private thought"}],
        })
        self.assertEqual(response["output"][1]["content"][0]["text"], "answer")

    def test_unlisted_and_encrypted_reasoning_still_fail_closed(self):
        with self.assertRaisesRegex(compat.UnsupportedFeatureError, "reasoning items"):
            compat.translate_request({"model": "plain-model", "input": [
                {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "PRIVATE"}]},
                {"role": "assistant", "content": "answer"},
            ]})
        with self.assertRaisesRegex(compat.UnsupportedFeatureError, "encrypted reasoning"):
            compat.translate_request({"model": "zai-org/GLM-5.3", "input": [
                {"type": "reasoning", "encrypted_content": "PRIVATE"},
            ]})
        translated = compat.translate_request({"model": "plain-model", "input": []})
        with self.assertRaisesRegex(compat.UnsupportedFeatureError, "reasoning_content"):
            translated.response({"choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "reasoning_content": "PRIVATE", "content": "answer",
            }}]})

    def test_stream_maps_chat_reasoning_without_exposing_it_as_answer_text(self):
        translated = compat.translate_request({
            "model": "deepseek-ai/DeepSeek-V4-Pro-0813",
            "input": [{"role": "user", "content": "Answer"}], "stream": True,
        })
        output = events(translated.stream([
            sse({"id": "chatcmpl-reasoning-stream", "choices": [{"index": 0, "delta": {
                "reasoning_content": "private "}, "finish_reason": None}]}),
            sse({"id": "chatcmpl-reasoning-stream", "choices": [{"index": 0, "delta": {
                "reasoning_content": "thought", "content": "answer"}, "finish_reason": None}]}),
            sse({"id": "chatcmpl-reasoning-stream", "choices": [{"index": 0, "delta": {},
                 "finish_reason": "stop"}]}),
            sse("[DONE]"),
        ]))
        completed = output[-1]["response"]
        self.assertEqual(completed["output"][0]["content"][0]["text"], "private thought")
        self.assertEqual(completed["output"][1]["content"][0]["text"], "answer")
        visible = [event.get("delta") for event in output if event["type"] == "response.output_text.delta"]
        self.assertEqual(visible, ["answer"])

    def test_multiple_json_function_calls_stay_distinct(self):
        _, _, translated = self.translation()
        result = translated.response({
            "id": "chatcmpl-many", "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "tool_calls": [
                    {"id": "call_a", "type": "function", "function": {"name": "alpha", "arguments": "{}"}},
                    {"id": "call_b", "type": "function", "function": {"name": "beta", "arguments": "{\"x\":2}"}},
                ],
            }}],
        })
        self.assertEqual([(item["call_id"], item["name"], item["arguments"]) for item in result["output"]], [
            ("call_a", "alpha", "{}"), ("call_b", "beta", "{\"x\":2}"),
        ])

    def test_stream_maps_fragmented_arguments_and_multiple_calls(self):
        _, _, translated = self.translation(stream=True)
        upstream = [
            sse({"id": "chatcmpl-stream", "created": 21, "choices": [{"index": 0, "delta": {
                "role": "assistant", "tool_calls": [
                    {"index": 0, "id": "call_a", "type": "function", "function": {"name": "alpha", "arguments": "{\"a\":"}},
                    {"index": 1, "id": "call_b", "type": "function", "function": {"name": "beta", "arguments": ""}},
                ],
            }, "finish_reason": None}]}),
            sse({"id": "chatcmpl-stream", "choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": "1}"}},
                {"index": 1, "function": {"arguments": "{\"b\":"}},
            ]}, "finish_reason": None}]}),
            sse({"id": "chatcmpl-stream", "choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 1, "function": {"arguments": "2}"}},
            ]}, "finish_reason": "tool_calls"}]}),
            sse("[DONE]"),
        ]
        output = events(translated.stream(upstream))
        self.assertEqual([e["type"] for e in output].count("response.output_item.added"), 2)
        self.assertEqual([(e["item"]["call_id"], e["item"]["arguments"]) for e in output
                          if e["type"] == "response.output_item.done"], [
            ("call_a", "{\"a\":1}"), ("call_b", "{\"b\":2}"),
        ])
        done = output[-1]
        self.assertEqual(done["type"], "response.completed")
        self.assertEqual([item["arguments"] for item in done["response"]["output"]], ["{\"a\":1}", "{\"b\":2}"])

    def test_unicode_and_fragmented_crlf_sse_framing(self):
        _, _, translated = self.translation(stream=True)
        first_payload = json.dumps({"id": "chatcmpl-unicode", "created": 22, "choices": [{
            "index": 0, "delta": {"role": "assistant", "content": "café 👋"}, "finish_reason": None,
        }]}, ensure_ascii=False, separators=(",", ":"))
        split = first_payload.index(',"choices"') + 1
        first = ("event: chat.completion.chunk\r\ndata: " + first_payload[:split] +
                 "\r\ndata: " + first_payload[split:] + "\r\n\r\n").encode()
        second = sse({"id": "chatcmpl-unicode", "choices": [{
            "index": 0, "delta": {}, "finish_reason": "stop",
        }]}, newline="\r\n") + sse("[DONE]", newline="\r\n")
        wire = first + second
        fragments = [wire[index:index + 1] for index in range(len(wire))]
        output = events(translated.stream(fragments))
        deltas = [event["delta"] for event in output if event["type"] == "response.output_text.delta"]
        self.assertEqual(deltas, ["café 👋"])
        self.assertEqual(output[-1]["response"]["output"][0]["content"][0]["text"], "café 👋")

    def test_done_frame_without_trailing_blank_line_completes(self):
        _, _, translated = self.translation(stream=True)
        output = events(translated.stream([
            sse({"id": "chatcmpl-terminal", "choices": [{
                "index": 0, "delta": {"content": "done"}, "finish_reason": "stop",
            }]}),
            b"data: [DONE]",
        ]))
        self.assertEqual(output[-1]["type"], "response.completed")

    def test_premature_eof_emits_failure_and_never_completion(self):
        _, _, translated = self.translation(stream=True)
        output = events(translated.stream([sse({
            "id": "chatcmpl-cut", "choices": [{"index": 0, "delta": {"content": "partial"}, "finish_reason": None}],
        })]))
        self.assertEqual(output[-1]["type"], "response.failed")
        self.assertEqual(output[-1]["response"]["error"]["code"], "premature_eof")
        self.assertNotIn("response.completed", [event["type"] for event in output])

    def test_stream_error_is_explicit_and_the_source_is_never_retried(self):
        _, _, translated = self.translation(stream=True)
        attempts = 0

        def broken_source():
            nonlocal attempts
            attempts += 1
            yield sse({
                "id": "chatcmpl-broken", "choices": [{
                    "index": 0, "delta": {"content": "partial"}, "finish_reason": None,
                }],
            })
            raise OSError("private transport detail")

        output = events(translated.stream(broken_source()))
        self.assertEqual(attempts, 1)
        self.assertEqual(output[-1]["type"], "response.failed")
        self.assertEqual(output[-1]["response"]["error"]["code"], "upstream_stream_error")
        self.assertNotIn("private transport detail", json.dumps(output))
        self.assertNotIn("response.completed", [event["type"] for event in output])

    def test_empty_json_and_stream_completions_are_rejected(self):
        _, _, translated = self.translation()
        with self.assertRaisesRegex(compat.EmptyCompletionError, "no text, refusal, or tool calls"):
            translated.response({"id": "chatcmpl-empty", "choices": [{
                "index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": ""},
            }]})
        _, _, streaming = self.translation(stream=True)
        output = events(streaming.stream([
            sse({"id": "chatcmpl-empty-stream", "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": "stop"}]}),
            sse("[DONE]"),
        ]))
        self.assertEqual(output[-1]["type"], "response.failed")
        self.assertEqual(output[-1]["response"]["error"]["code"], "empty_completion")

    def test_refusal_and_upstream_error_are_explicit(self):
        _, _, translated = self.translation()
        refusal = translated.response({
            "id": "chatcmpl-refusal", "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": None, "refusal": "I cannot do that.",
            }}],
        })
        self.assertEqual(refusal["output"][0]["content"], [{"type": "refusal", "refusal": "I cannot do that."}])
        failed = translated.response({"error": {"message": "account denied", "type": "authentication_error", "code": "bad_account"}})
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error"], {"code": "bad_account", "message": "account denied", "type": "authentication_error"})

    def test_usage_is_mapped_for_json_and_terminal_stream(self):
        usage = {
            "prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18,
            "prompt_tokens_details": {"cached_tokens": 3},
            "completion_tokens_details": {"reasoning_tokens": 2},
        }
        _, _, translated = self.translation()
        result = translated.response({
            "id": "chatcmpl-usage", "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
            "usage": usage,
        })
        expected = {
            "input_tokens": 11, "output_tokens": 7, "total_tokens": 18,
            "input_tokens_details": {"cached_tokens": 3},
            "output_tokens_details": {"reasoning_tokens": 2},
        }
        self.assertEqual(result["usage"], expected)

        _, _, streaming = self.translation(stream=True)
        output = events(streaming.stream([
            sse({"id": "chatcmpl-stream-usage", "choices": [{"index": 0, "delta": {"content": "ok"}, "finish_reason": None}]}),
            sse({"id": "chatcmpl-stream-usage", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
            sse({"id": "chatcmpl-stream-usage", "choices": [], "usage": usage}),
            sse("[DONE]"),
        ]))
        self.assertEqual(output[-1]["response"]["usage"], expected)

    def test_unsupported_responses_features_fail_before_translation(self):
        cases = [
            ({"input": [{"type": "reasoning", "encrypted_content": "opaque"}]}, "encrypted reasoning"),
            ({"tools": [{"type": "custom", "name": "shell"}]}, "custom tools"),
            ({"tools": [{"type": "namespace", "name": "work", "tools": []}]}, "namespace tools"),
            ({"previous_response_id": "resp_provider_owned"}, "previous_response_id"),
        ]
        for changes, message in cases:
            with self.subTest(message=message):
                source = {"model": "exact", "input": []}
                source.update(changes)
                with self.assertRaisesRegex(compat.UnsupportedFeatureError, message):
                    compat.translate_request(source)

    def test_images_become_labeled_notices_on_text_routes(self):
        source = {"model": "exact", "input": [
            {"role": "user", "content": [
                {"type": "input_text", "text": "before"},
                {"type": "input_image", "image_url": "data:image/png;base64,PRIVATE"},
                {"type": "input_text", "text": "after"}]}]}
        body = compat.translate_request(source).body
        content = body["messages"][0]["content"]
        self.assertIn("before", content)
        self.assertIn("after", content)
        self.assertIn("not provided to this model", content)
        self.assertNotIn("PRIVATE", content)

    def test_tool_result_images_become_labeled_notices(self):
        source = {"model": "exact", "input": [
            {"type": "function_call", "call_id": "c1", "name": "view_image", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c1",
             "output": [{"type": "input_image", "image_url": "data:image/png;base64,PRIVATE"}]}]}
        body = compat.translate_request(source).body
        tool_message = body["messages"][-1]
        self.assertEqual(tool_message["role"], "tool")
        self.assertIn("not provided to this model", tool_message["content"])
        self.assertNotIn("PRIVATE", tool_message["content"])

    def test_unknown_fields_and_incomplete_history_fail_closed(self):
        with self.assertRaisesRegex(compat.UnsupportedFeatureError, "request field.*service_tier"):
            compat.translate_request({"model": "exact", "input": [], "service_tier": "priority"})
        with self.assertRaisesRegex(compat.ProtocolError, "unknown function call"):
            compat.translate_request({"model": "exact", "input": [
                {"type": "function_call_output", "call_id": "missing", "output": "orphan"},
            ]})


if __name__ == "__main__":
    unittest.main()
