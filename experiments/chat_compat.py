"""Isolated Responses-to-Chat-Completions compatibility experiment.

The module only translates values supplied by its caller. It performs no I/O,
chooses no provider, and has no retry or fallback path.
"""

from __future__ import annotations

import codecs
import copy
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any


class CompatibilityError(ValueError):
    """Base class for compatibility failures."""


class UnsupportedFeatureError(CompatibilityError):
    """The request relies on Responses semantics Chat Completions cannot keep."""


class ProtocolError(CompatibilityError):
    """The supplied Chat Completions payload is malformed or inconsistent."""


class EmptyCompletionError(ProtocolError):
    """The provider claimed completion without an assistant result."""


_REQUEST_FIELDS = {
    "model",
    "input",
    "instructions",
    "tools",
    "tool_choice",
    "parallel_tool_calls",
    "temperature",
    "top_p",
    "max_output_tokens",
    "stream",
    "user",
}


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _contains_key(value: Any, key: str) -> bool:
    if isinstance(value, Mapping):
        return key in value or any(_contains_key(child, key) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_key(child, key) for child in value)
    return False


def _reject_lossy_semantics(source: Mapping[str, Any]) -> None:
    if _contains_key(source, "encrypted_content"):
        raise UnsupportedFeatureError("encrypted reasoning cannot be translated to Chat Completions")
    if source.get("previous_response_id") is not None:
        raise UnsupportedFeatureError("previous_response_id is an ambiguous provider-owned history reference")
    if source.get("conversation") is not None:
        raise UnsupportedFeatureError("conversation is an ambiguous provider-owned history reference")

    def inspect(value: Any) -> None:
        if isinstance(value, Mapping):
            kind = value.get("type")
            if kind in {"input_image", "image_url", "image", "computer_screenshot"} or "image_url" in value:
                raise UnsupportedFeatureError("image content is not supported by this text compatibility pilot")
            if kind in {"custom", "custom_tool_call", "custom_tool_call_output"}:
                raise UnsupportedFeatureError("custom tools and custom tool history are not translatable")
            if kind == "namespace" or "namespace" in value:
                raise UnsupportedFeatureError("namespace tools and namespace-only call semantics are not translatable")
            if kind in {"item_reference", "response_reference"}:
                raise UnsupportedFeatureError("prior response item references are ambiguous without provider state")
            if kind == "reasoning":
                raise UnsupportedFeatureError("reasoning items are not translatable to Chat Completions")
            for child in value.values():
                inspect(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                inspect(child)

    inspect(source)


def _text_parts(content: Any, *, context: str) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise ProtocolError(f"{context} content must be text or a list of text parts")
    text = []
    for part in content:
        if isinstance(part, str):
            text.append(part)
            continue
        if not isinstance(part, Mapping):
            raise ProtocolError(f"{context} contains a non-object content part")
        kind = part.get("type")
        if kind in {"input_image", "image_url", "image", "computer_screenshot"} or "image_url" in part:
            raise UnsupportedFeatureError("image content is not supported by this text compatibility pilot")
        if kind in {"input_text", "output_text", "text"} and isinstance(part.get("text"), str):
            text.append(part["text"])
            continue
        if kind == "refusal" and isinstance(part.get("refusal"), str):
            text.append(part["refusal"])
            continue
        raise UnsupportedFeatureError(f"unsupported {context} content part: {kind!r}")
    return "".join(text)


def _tool_output(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return _text_parts(value, context="function result")
    if value is None:
        return ""
    try:
        return _compact(value)
    except (TypeError, ValueError) as error:
        raise ProtocolError("function result is not JSON serializable") from error


def _function_call(item: Mapping[str, Any]) -> dict[str, Any]:
    if item.get("type") != "function_call":
        raise ProtocolError("expected a function_call history item")
    call_id = item.get("call_id") or item.get("id")
    name = item.get("name")
    arguments = item.get("arguments", "")
    if not isinstance(call_id, str) or not call_id:
        raise ProtocolError("function_call requires a call_id")
    if not isinstance(name, str) or not name:
        raise ProtocolError("function_call requires a name")
    if not isinstance(arguments, str):
        raise ProtocolError("function_call arguments must remain a JSON string")
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _messages(source: Any) -> list[dict[str, Any]]:
    if isinstance(source, str):
        return [{"role": "user", "content": source}]
    if not isinstance(source, list):
        raise ProtocolError("Responses input must be text or a list of history items")

    result: list[dict[str, Any]] = []
    known_calls: set[str] = set()
    index = 0
    while index < len(source):
        item = source[index]
        if not isinstance(item, Mapping):
            raise ProtocolError("Responses history items must be objects")
        kind = item.get("type", "message")
        if kind == "function_call":
            calls = []
            while index < len(source):
                candidate = source[index]
                if not isinstance(candidate, Mapping) or candidate.get("type", "message") != "function_call":
                    break
                call = _function_call(candidate)
                if call["id"] in known_calls:
                    raise ProtocolError(f"duplicate function call id: {call['id']}")
                known_calls.add(call["id"])
                calls.append(call)
                index += 1
            result.append({"role": "assistant", "content": None, "tool_calls": calls})
            continue
        if kind == "function_call_output":
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or call_id not in known_calls:
                raise ProtocolError(f"function result references unknown function call: {call_id!r}")
            result.append({"role": "tool", "tool_call_id": call_id, "content": _tool_output(item.get("output"))})
            index += 1
            continue
        if kind in {"reasoning", "item_reference", "response_reference"}:
            raise UnsupportedFeatureError(f"{kind} history cannot be translated without Responses provider state")
        if kind != "message":
            raise UnsupportedFeatureError(f"unsupported Responses history item: {kind!r}")
        role = item.get("role")
        if role not in {"user", "assistant", "system", "developer"}:
            raise ProtocolError(f"unsupported message role: {role!r}")
        message: dict[str, Any] = {"role": role, "content": _text_parts(item.get("content"), context="message")}
        result.append(message)
        index += 1
    return result


def _tools(source: Any) -> list[dict[str, Any]]:
    if not isinstance(source, list):
        raise ProtocolError("tools must be a list")
    result = []
    for tool in source:
        if not isinstance(tool, Mapping):
            raise ProtocolError("each tool must be an object")
        kind = tool.get("type")
        if kind == "custom":
            raise UnsupportedFeatureError("custom tools cannot be translated to Chat Completions")
        if kind == "namespace":
            raise UnsupportedFeatureError("namespace tools cannot be translated to Chat Completions")
        if kind != "function":
            raise UnsupportedFeatureError(f"unsupported tool type: {kind!r}")
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            raise ProtocolError("function tools require a name")
        function = {"name": name}
        for field in ("description", "parameters", "strict"):
            if field in tool:
                function[field] = copy.deepcopy(tool[field])
        extra = set(tool) - {"type", "name", "description", "parameters", "strict"}
        if extra:
            raise UnsupportedFeatureError(f"unsupported function tool field: {sorted(extra)[0]}")
        result.append({"type": "function", "function": function})
    return result


def _tool_choice(source: Any) -> Any:
    if isinstance(source, str) and source in {"auto", "none", "required"}:
        return source
    if not isinstance(source, Mapping):
        raise ProtocolError("tool_choice must be auto, none, required, or a named function")
    if source.get("type") != "function" or not isinstance(source.get("name"), str):
        raise UnsupportedFeatureError("only a named function tool_choice is translatable")
    if set(source) != {"type", "name"}:
        raise UnsupportedFeatureError("namespace-only or extended tool_choice semantics are not translatable")
    return {"type": "function", "function": {"name": source["name"]}}


def _usage(source: Any) -> dict[str, Any] | None:
    if source is None:
        return None
    if not isinstance(source, Mapping):
        raise ProtocolError("usage must be an object")
    result = {
        "input_tokens": source.get("prompt_tokens", 0),
        "output_tokens": source.get("completion_tokens", 0),
        "total_tokens": source.get("total_tokens", 0),
    }
    prompt_details = source.get("prompt_tokens_details")
    completion_details = source.get("completion_tokens_details")
    if prompt_details is not None:
        result["input_tokens_details"] = copy.deepcopy(prompt_details)
    if completion_details is not None:
        result["output_tokens_details"] = copy.deepcopy(completion_details)
    return result


def _error(source: Any) -> dict[str, Any]:
    if not isinstance(source, Mapping):
        return {"code": "upstream_error", "message": str(source), "type": "server_error"}
    return {
        "code": source.get("code") or source.get("type") or "upstream_error",
        "message": source.get("message") or "Chat Completions returned an error",
        "type": source.get("type") or "server_error",
    }


def _text_part(text: str) -> dict[str, Any]:
    return {"type": "output_text", "text": text, "annotations": []}


def _message_item(response_id: str, text: str, refusal: str, *, status: str = "completed") -> dict[str, Any]:
    content = []
    if text:
        content.append(_text_part(text))
    if refusal:
        content.append({"type": "refusal", "refusal": refusal})
    return {
        "id": f"{response_id}:message:0",
        "type": "message",
        "status": status,
        "role": "assistant",
        "content": content,
    }


def _call_item(call: Mapping[str, Any], *, status: str = "completed") -> dict[str, Any]:
    if call.get("type", "function") != "function":
        raise UnsupportedFeatureError("Chat Completions returned a non-function tool call")
    function = call.get("function")
    call_id = call.get("id")
    if not isinstance(function, Mapping) or not isinstance(call_id, str) or not call_id:
        raise ProtocolError("Chat Completions returned a malformed function call")
    name = function.get("name")
    arguments = function.get("arguments", "")
    if not isinstance(name, str) or not name or not isinstance(arguments, str):
        raise ProtocolError("Chat Completions returned a malformed function call")
    return {
        "id": call_id,
        "type": "function_call",
        "status": status,
        "call_id": call_id,
        "name": name,
        "arguments": arguments,
    }


def _base_response(response_id: str, model: str, status: str, output: list[dict[str, Any]], created: Any = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": response_id,
        "object": "response",
    }
    if created is not None:
        result["created_at"] = created
    result.update({"model": model, "status": status, "output": output})
    return result


def _finish_status(finish_reason: Any) -> tuple[str, dict[str, Any] | None]:
    if finish_reason in {"stop", "tool_calls", "function_call"}:
        return "completed", None
    if finish_reason in {"length", "content_filter"}:
        reason = "max_output_tokens" if finish_reason == "length" else "content_filter"
        return "incomplete", {"reason": reason}
    if finish_reason is None:
        raise ProtocolError("Chat Completions response has no finish_reason")
    raise ProtocolError(f"unsupported Chat Completions finish_reason: {finish_reason!r}")


def _json_response(source: Mapping[str, Any], model: str) -> dict[str, Any]:
    response_id = source.get("id") if isinstance(source.get("id"), str) else "resp_chat_compat"
    if "error" in source:
        result = _base_response(response_id, model, "failed", [], source.get("created"))
        result["error"] = _error(source["error"])
        return result
    choices = source.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        if choices == []:
            raise EmptyCompletionError("Chat Completions returned no text, refusal, or tool calls")
        raise ProtocolError("exactly one Chat Completions choice is required")
    choice = choices[0]
    if not isinstance(choice, Mapping) or not isinstance(choice.get("message"), Mapping):
        raise ProtocolError("Chat Completions returned a malformed choice")
    message = choice["message"]
    content = message.get("content")
    text = _text_parts(content, context="assistant response") if content is not None else ""
    refusal_value = message.get("refusal")
    refusal = refusal_value if isinstance(refusal_value, str) else ""
    calls_source = message.get("tool_calls") or []
    if not isinstance(calls_source, list):
        raise ProtocolError("assistant tool_calls must be a list")
    calls = [_call_item(call) for call in calls_source]
    if not text and not refusal and not calls:
        raise EmptyCompletionError("Chat Completions returned no text, refusal, or tool calls")
    status, incomplete = _finish_status(choice.get("finish_reason"))
    output = []
    if text or refusal:
        output.append(_message_item(response_id, text, refusal))
    output.extend(calls)
    result = _base_response(response_id, model, status, output, source.get("created"))
    if incomplete:
        result["incomplete_details"] = incomplete
    usage = _usage(source.get("usage"))
    if usage is not None:
        result["usage"] = usage
    return result


def _event(kind: str, **fields: Any) -> bytes:
    payload = {"type": kind, **fields}
    return f"event: {kind}\ndata: {_compact(payload)}\n\n".encode("utf-8")


def _failure_event(response_id: str, model: str, code: str, message: str) -> bytes:
    response = _base_response(response_id, model, "failed", [])
    response["error"] = {"code": code, "message": message, "type": "server_error"}
    return _event("response.failed", response=response)


def _sse_payloads(chunks: Iterable[bytes | str]):
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    buffer = ""

    def data_from(block: str) -> str:
        block = block.replace("\r\n", "\n").replace("\r", "\n")
        return "\n".join(line[5:].lstrip() for line in block.split("\n") if line.startswith("data:"))

    try:
        for chunk in chunks:
            if isinstance(chunk, str):
                piece = chunk
            elif isinstance(chunk, (bytes, bytearray, memoryview)):
                piece = decoder.decode(bytes(chunk), final=False)
            else:
                raise ProtocolError("SSE chunks must be bytes or text")
            buffer += piece
            while match := re.search(r"\r\n\r\n|\n\n|\r\r", buffer):
                block, buffer = buffer[:match.start()], buffer[match.end():]
                data = data_from(block)
                if data:
                    yield data
        buffer += decoder.decode(b"", final=True)
    except UnicodeDecodeError as error:
        raise ProtocolError("SSE stream contains invalid UTF-8") from error
    if buffer.strip():
        data = data_from(buffer)
        if data:
            yield data


class _StreamTranslator:
    def __init__(self, model: str):
        self.model = model
        self.response_id = "resp_chat_compat"
        self.created = None
        self.text = ""
        self.refusal = ""
        self.message_started = False
        self.message_done = False
        self.calls: dict[int, dict[str, Any]] = {}
        self.order: list[tuple[str, int | None]] = []
        self.output_indices: dict[tuple[str, int | None], int] = {}
        self.finish_reason = None
        self.usage = None
        self.saw_done = False
        self.failed = False

    def _output_index(self, key: tuple[str, int | None]) -> int:
        if key not in self.output_indices:
            self.output_indices[key] = len(self.order)
            self.order.append(key)
        return self.output_indices[key]

    def _start_message(self):
        if self.message_started:
            return []
        self.message_started = True
        output_index = self._output_index(("message", None))
        item = _message_item(self.response_id, "", "", status="in_progress")
        return [_event("response.output_item.added", output_index=output_index, item=item)]

    def _start_call(self, call_index: int, delta: Mapping[str, Any]):
        function = delta.get("function") or {}
        call_id = delta.get("id")
        name = function.get("name")
        if not isinstance(call_id, str) or not call_id or not isinstance(name, str) or not name:
            raise ProtocolError("the first streamed function-call fragment requires id and name")
        call = {"id": call_id, "name": name, "arguments": "", "done": False}
        self.calls[call_index] = call
        output_index = self._output_index(("call", call_index))
        item = {
            "id": call_id,
            "type": "function_call",
            "status": "in_progress",
            "call_id": call_id,
            "name": name,
            "arguments": "",
        }
        return call, [_event("response.output_item.added", output_index=output_index, item=item)]

    def accept(self, source: Mapping[str, Any]):
        output = []
        if "error" in source:
            self.failed = True
            error = _error(source["error"])
            response = _base_response(self.response_id, self.model, "failed", [])
            response["error"] = error
            output.append(_event("response.failed", response=response))
            return output
        if isinstance(source.get("id"), str):
            self.response_id = source["id"]
        if source.get("created") is not None:
            self.created = source["created"]
        if source.get("usage") is not None:
            self.usage = _usage(source["usage"])
        choices = source.get("choices", [])
        if not isinstance(choices, list) or len(choices) > 1:
            raise ProtocolError("stream chunks must contain at most one choice")
        if not choices:
            return output
        choice = choices[0]
        if not isinstance(choice, Mapping):
            raise ProtocolError("stream choice must be an object")
        delta = choice.get("delta") or {}
        if not isinstance(delta, Mapping):
            raise ProtocolError("stream choice delta must be an object")
        text = delta.get("content")
        if text is not None:
            if not isinstance(text, str):
                raise ProtocolError("streamed content delta must be text")
            if text:
                output.extend(self._start_message())
                self.text += text
                output_index = self._output_index(("message", None))
                output.append(_event("response.output_text.delta", item_id=f"{self.response_id}:message:0",
                                     output_index=output_index, content_index=0, delta=text))
        refusal = delta.get("refusal")
        if refusal is not None:
            if not isinstance(refusal, str):
                raise ProtocolError("streamed refusal delta must be text")
            if refusal:
                output.extend(self._start_message())
                self.refusal += refusal
                output_index = self._output_index(("message", None))
                output.append(_event("response.refusal.delta", item_id=f"{self.response_id}:message:0",
                                     output_index=output_index, content_index=1 if self.text else 0, delta=refusal))
        tool_deltas = delta.get("tool_calls") or []
        if not isinstance(tool_deltas, list):
            raise ProtocolError("streamed tool_calls must be a list")
        for tool_delta in tool_deltas:
            if not isinstance(tool_delta, Mapping) or not isinstance(tool_delta.get("index"), int):
                raise ProtocolError("each streamed tool call requires an integer index")
            call_index = tool_delta["index"]
            if tool_delta.get("type", "function") != "function":
                raise UnsupportedFeatureError("stream returned a non-function tool call")
            call = self.calls.get(call_index)
            if call is None:
                call, added = self._start_call(call_index, tool_delta)
                output.extend(added)
            else:
                if tool_delta.get("id") not in {None, call["id"]}:
                    raise ProtocolError("stream changed a function call id")
                function = tool_delta.get("function") or {}
                if function.get("name") not in {None, "", call["name"]}:
                    raise ProtocolError("stream changed a function call name")
            function = tool_delta.get("function") or {}
            arguments = function.get("arguments", "")
            if not isinstance(arguments, str):
                raise ProtocolError("streamed function arguments must be text")
            if arguments:
                call["arguments"] += arguments
                output_index = self._output_index(("call", call_index))
                output.append(_event("response.function_call_arguments.delta", item_id=call["id"],
                                     output_index=output_index, delta=arguments))
        if choice.get("finish_reason") is not None:
            if self.finish_reason is not None:
                raise ProtocolError("stream returned more than one finish_reason")
            self.finish_reason = choice["finish_reason"]
        return output

    def _finalize_items(self):
        output = []
        if self.message_started and not self.message_done:
            output_index = self._output_index(("message", None))
            if self.text:
                output.append(_event("response.output_text.done", item_id=f"{self.response_id}:message:0",
                                     output_index=output_index, content_index=0, text=self.text))
            if self.refusal:
                output.append(_event("response.refusal.done", item_id=f"{self.response_id}:message:0",
                                     output_index=output_index, content_index=1 if self.text else 0, refusal=self.refusal))
            item = _message_item(self.response_id, self.text, self.refusal)
            output.append(_event("response.output_item.done", output_index=output_index, item=item))
            self.message_done = True
        for call_index in sorted(self.calls):
            call = self.calls[call_index]
            if call["done"]:
                continue
            output_index = self._output_index(("call", call_index))
            output.append(_event("response.function_call_arguments.done", item_id=call["id"],
                                 output_index=output_index, arguments=call["arguments"]))
            item = {
                "id": call["id"], "type": "function_call", "status": "completed",
                "call_id": call["id"], "name": call["name"], "arguments": call["arguments"],
            }
            output.append(_event("response.output_item.done", output_index=output_index, item=item))
            call["done"] = True
        return output

    def _completed_output(self):
        items: dict[tuple[str, int | None], dict[str, Any]] = {}
        if self.message_started:
            items[("message", None)] = _message_item(self.response_id, self.text, self.refusal)
        for call_index, call in self.calls.items():
            items[("call", call_index)] = {
                "id": call["id"], "type": "function_call", "status": "completed",
                "call_id": call["id"], "name": call["name"], "arguments": call["arguments"],
            }
        return [items[key] for key in self.order]

    def complete(self):
        if self.finish_reason is None:
            return [_failure_event(self.response_id, self.model, "premature_eof",
                                   "Chat Completions stream ended before a finish_reason")]
        if not self.text and not self.refusal and not self.calls:
            return [_failure_event(self.response_id, self.model, "empty_completion",
                                   "Chat Completions returned no text, refusal, or tool calls")]
        output = self._finalize_items()
        status, incomplete = _finish_status(self.finish_reason)
        response = _base_response(self.response_id, self.model, status, self._completed_output(), self.created)
        if incomplete:
            response["incomplete_details"] = incomplete
        if self.usage is not None:
            response["usage"] = self.usage
        output.append(_event(f"response.{status}", response=response))
        return output


def _stream_response(chunks: Iterable[bytes | str], model: str):
    state = _StreamTranslator(model)
    try:
        for payload in _sse_payloads(chunks):
            if payload == "[DONE]":
                state.saw_done = True
                if not state.failed:
                    yield from state.complete()
                return
            try:
                source = json.loads(payload)
            except json.JSONDecodeError as error:
                raise ProtocolError("SSE data is not valid JSON") from error
            if not isinstance(source, Mapping):
                raise ProtocolError("SSE data must contain a JSON object")
            yield from state.accept(source)
            if state.failed:
                return
    except (CompatibilityError, TypeError, ValueError) as error:
        yield _failure_event(state.response_id, model, "stream_protocol_error", str(error))
        return
    except Exception:
        yield _failure_event(state.response_id, model, "upstream_stream_error",
                             "Chat Completions stream failed before completion")
        return
    if not state.failed:
        yield _failure_event(state.response_id, model, "premature_eof",
                             "Chat Completions stream ended before the [DONE] marker")


class TranslatedRequest:
    """One translated request and the response translators bound to it."""

    def __init__(self, body: Mapping[str, Any], transport: Mapping[str, Any] | None = None):
        self.body = copy.deepcopy(dict(body))
        self.request = self.body
        self.transport = copy.deepcopy(dict(transport or {}))
        self.model = self.body["model"]

    def response(self, source: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(source, Mapping):
            raise ProtocolError("Chat Completions response must be an object")
        return _json_response(source, self.model)

    def stream(self, chunks: Iterable[bytes | str]):
        return _stream_response(chunks, self.model)


def translate_request(source: Mapping[str, Any], *, transport: Mapping[str, Any] | None = None) -> TranslatedRequest:
    """Translate one Responses request without sending it anywhere."""
    if not isinstance(source, Mapping):
        raise ProtocolError("Responses request must be an object")
    _reject_lossy_semantics(source)
    unknown = set(source) - _REQUEST_FIELDS
    if unknown:
        raise UnsupportedFeatureError(f"unsupported Responses request field: {sorted(unknown)[0]}")
    model = source.get("model")
    if not isinstance(model, str) or not model:
        raise ProtocolError("Responses request requires an exact non-empty model string")
    if "input" not in source:
        raise ProtocolError("Responses request requires inline input history")

    body: dict[str, Any] = {"model": model, "messages": []}
    instructions = source.get("instructions")
    if instructions is not None:
        if not isinstance(instructions, str):
            raise ProtocolError("instructions must be text")
        body["messages"].append({"role": "developer", "content": instructions})
    body["messages"].extend(_messages(source["input"]))

    if "tools" in source:
        body["tools"] = _tools(source["tools"])
    if "tool_choice" in source:
        if not body.get("tools") and source["tool_choice"] != "none":
            raise ProtocolError("tool_choice requires at least one function tool")
        body["tool_choice"] = _tool_choice(source["tool_choice"])
    if "parallel_tool_calls" in source:
        if not isinstance(source["parallel_tool_calls"], bool):
            raise ProtocolError("parallel_tool_calls must be boolean")
        body["parallel_tool_calls"] = source["parallel_tool_calls"]
    for field in ("temperature", "top_p", "user"):
        if field in source:
            body[field] = copy.deepcopy(source[field])
    if "max_output_tokens" in source:
        body["max_completion_tokens"] = source["max_output_tokens"]
    if source.get("stream"):
        if source["stream"] is not True:
            raise ProtocolError("stream must be boolean")
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
    elif "stream" in source and source["stream"] is not False:
        raise ProtocolError("stream must be boolean")
    elif source.get("stream") is False:
        body["stream"] = False
    return TranslatedRequest(body, transport)


def translate_response(request: TranslatedRequest, source: Mapping[str, Any]) -> dict[str, Any]:
    """Translate JSON using a previously translated request."""
    if not isinstance(request, TranslatedRequest):
        raise TypeError("request must be a TranslatedRequest")
    return request.response(source)


def translate_stream(request: TranslatedRequest, chunks: Iterable[bytes | str]):
    """Translate SSE chunks using a previously translated request."""
    if not isinstance(request, TranslatedRequest):
        raise TypeError("request must be a TranslatedRequest")
    return request.stream(chunks)


__all__ = [
    "CompatibilityError",
    "EmptyCompletionError",
    "ProtocolError",
    "TranslatedRequest",
    "UnsupportedFeatureError",
    "translate_request",
    "translate_response",
    "translate_stream",
]
