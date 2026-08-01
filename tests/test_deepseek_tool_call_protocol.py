from __future__ import annotations

import json

import pytest
from openai.types.chat.chat_completion_message import ChatCompletionMessage

from memslides.utils.config import (
    MalformedToolCallError,
    _normalize_embedded_tool_calls,
)
from memslides.utils.typings import ChatCompletionMessageFunctionToolCall, Function


def _tool_call(name: str, arguments: dict) -> ChatCompletionMessageFunctionToolCall:
    return ChatCompletionMessageFunctionToolCall(
        id="call_original",
        type="function",
        function=Function(
            name=name,
            arguments=json.dumps(arguments, ensure_ascii=False),
        ),
    )


def _tools(*names: str) -> list[dict]:
    schemas = {
        "thinking": {
            "type": "object",
            "properties": {"thought": {"type": "string"}},
            "required": ["thought"],
            "additionalProperties": False,
        },
        "write_html_file": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["file_path", "content"],
            "additionalProperties": False,
        },
    }
    return [
        {
            "type": "function",
            "function": {"name": name, "parameters": schemas[name]},
        }
        for name in names
    ]


def test_deepseek_dsml_nested_in_thinking_is_split_into_valid_calls() -> None:
    thought = (
        "已完成前四页；下一步写第 5 页。<｜end_of_thinking｜>\n"
        "<｜DSML｜tool_calls>\n"
        '<｜DSML｜invoke name="write_html_file">\n'
        '<｜DSML｜parameter name="content" string="true">'
        "<!doctype html><html><body>第五页</body></html>"
    )
    message = ChatCompletionMessage(
        role="assistant",
        content=None,
        tool_calls=[
            _tool_call(
                "thinking",
                {"thought": thought, "file_path": "outputs/slide_05.html"},
            )
        ],
    )

    _normalize_embedded_tool_calls(
        message,
        _tools("thinking", "write_html_file"),
        model="deepseek-v4-pro",
    )

    assert [call.function.name for call in message.tool_calls] == [
        "thinking",
        "write_html_file",
    ]
    thinking_args = json.loads(message.tool_calls[0].function.arguments)
    write_args = json.loads(message.tool_calls[1].function.arguments)
    assert thinking_args == {"thought": "已完成前四页；下一步写第 5 页。"}
    assert write_args == {
        "file_path": "outputs/slide_05.html",
        "content": "<!doctype html><html><body>第五页</body></html>",
    }
    assert message.tool_calls[0].id == "call_original"
    assert message.tool_calls[1].id != "call_original"


def test_unavailable_embedded_tool_requests_protocol_retry() -> None:
    thought = (
        "继续。\n<｜DSML｜tool_calls>\n"
        '<｜DSML｜invoke name="write_html_file">\n'
        '<｜DSML｜parameter name="content" string="true"><html></html>'
    )
    message = ChatCompletionMessage(
        role="assistant",
        content=None,
        tool_calls=[
            _tool_call(
                "thinking",
                {"thought": thought, "file_path": "outputs/slide_05.html"},
            )
        ],
    )

    with pytest.raises(MalformedToolCallError, match="not available"):
        _normalize_embedded_tool_calls(
            message,
            _tools("thinking"),
            model="deepseek-v4-pro",
        )


def test_normal_thinking_call_is_unchanged() -> None:
    message = ChatCompletionMessage(
        role="assistant",
        content=None,
        tool_calls=[_tool_call("thinking", {"thought": "完成 1-4 页，继续第 5 页"})],
    )

    _normalize_embedded_tool_calls(
        message,
        _tools("thinking"),
        model="deepseek-v4-pro",
    )

    assert len(message.tool_calls) == 1
    assert json.loads(message.tool_calls[0].function.arguments) == {
        "thought": "完成 1-4 页，继续第 5 页"
    }
