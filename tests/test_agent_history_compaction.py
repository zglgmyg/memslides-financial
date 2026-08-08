from __future__ import annotations

import json

from memslides.agents.agent import Agent
from memslides.utils.typings import ChatMessage, Role


def test_old_write_html_call_is_replaced_with_plain_summary() -> None:
    agent = object.__new__(Agent)
    agent.chat_history = [
        ChatMessage(role=Role.SYSTEM, content="system"),
        ChatMessage(role=Role.USER, content="create slides"),
        ChatMessage(
            role=Role.ASSISTANT,
            content="",
            tool_calls=[
                {
                    "id": "write-1",
                    "type": "function",
                    "function": {
                        "name": "write_html_file",
                        "arguments": json.dumps(
                            {
                                "file_path": "outputs/slide_04.html",
                                "content": "<html><body>complete slide</body></html>",
                            }
                        ),
                    },
                }
            ],
        ),
        ChatMessage(
            role=Role.TOOL,
            content="Successfully wrote HTML file to: outputs/slide_04.html",
            tool_call_id="write-1",
        ),
        ChatMessage(role=Role.USER, content="continue"),
        ChatMessage(role=Role.ASSISTANT, content="working on the next slide"),
    ]

    agent._sliding_window_truncate(keep_recent=1)

    old_assistant = agent.chat_history[2]
    assert old_assistant.tool_calls is None
    assert "slide_04.html" in old_assistant.text
    assert "Historical tool state" in old_assistant.text
    assert "write_html_file" in old_assistant.text
    assert "read_file" in old_assistant.text
    assert "旧 HTML 已压缩" not in old_assistant.text
    assert all(message.tool_call_id != "write-1" for message in agent.chat_history)
