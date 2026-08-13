from __future__ import annotations

import asyncio
import json
from types import MethodType, SimpleNamespace

from memslides.agents.deck_designer import DeckDesigner
from memslides.utils.typings import (
    ChatCompletionMessageFunctionToolCall,
    ChatMessage,
    Function,
    Role,
)


def _tool_message(name: str) -> ChatMessage:
    return ChatMessage(
        role=Role.ASSISTANT,
        content="",
        tool_calls=[
            ChatCompletionMessageFunctionToolCall(
                id=f"call_{name}",
                type="function",
                function=Function(name=name, arguments=json.dumps({})),
            )
        ],
    )


def test_loop_appends_task_before_progress(tmp_path, monkeypatch) -> None:
    designer = object.__new__(DeckDesigner)
    designer.workspace = tmp_path
    designer.chat_history = [ChatMessage(role=Role.SYSTEM, content="system")]

    def ensure_initial_user_turn(self, **kwargs) -> None:
        if len(self.chat_history) == 1:
            self.chat_history.append(
                ChatMessage(role=Role.USER, content=f"task:{kwargs['prompt']}")
            )

    async def action(self, **kwargs):
        return ChatMessage(role=Role.ASSISTANT, content="done")

    designer._ensure_initial_user_turn = MethodType(ensure_initial_user_turn, designer)
    designer.action = MethodType(action, designer)
    monkeypatch.setattr(
        "memslides.agents.deck_designer.render_deck_progress_prompt",
        lambda workspace: "progress:PLAN_REFINE",
    )

    request = SimpleNamespace(
        extra_info={"deck_designer_max_iterations": 1},
        designagent_prompt="build the deck",
    )

    async def collect() -> None:
        async for _ in designer.loop(request, "manuscript.md"):
            pass

    asyncio.run(collect())

    assert [message.text for message in designer.chat_history[:3]] == [
        "system",
        "task:build the deck",
        "progress:PLAN_REFINE",
    ]


def test_financial_loop_advances_from_plan_to_write_and_inspect(tmp_path, monkeypatch) -> None:
    designer = object.__new__(DeckDesigner)
    designer.workspace = tmp_path
    designer.chat_history = [ChatMessage(role=Role.SYSTEM, content="system")]
    actions = iter(
        [
            _tool_message("read_file"),
            _tool_message("write_markdown_file"),
            _tool_message("read_file"),
            _tool_message("write_html_file"),
            _tool_message("inspect_slide"),
            ChatMessage(role=Role.ASSISTANT, content="done"),
        ]
    )
    executed: list[str] = []
    initial_markdown_values: list[str] = []

    def ensure_initial_user_turn(self, **kwargs) -> None:
        initial_markdown_values.append(kwargs["markdown_file"])
        self.chat_history.append(ChatMessage(role=Role.USER, content="task"))

    async def action(self, **kwargs):
        message = next(actions)
        self.chat_history.append(message)
        return message

    async def execute(self, tool_calls):
        executed.append(tool_calls[0].function.name)
        return [ChatMessage(role=Role.TOOL, content="ok")]

    designer._ensure_initial_user_turn = MethodType(ensure_initial_user_turn, designer)
    designer.action = MethodType(action, designer)
    designer.execute = MethodType(execute, designer)
    monkeypatch.setattr(
        "memslides.agents.deck_designer.render_deck_progress_prompt",
        lambda workspace: f"progress:{len(executed)}",
    )
    request = SimpleNamespace(
        extra_info={
            "deck_designer_max_iterations": 8,
            "financial_artifacts_read_only": True,
        },
        designagent_prompt="build the deck",
    )

    async def collect() -> None:
        async for _ in designer.loop(request, "manuscript.md"):
            pass

    asyncio.run(collect())

    assert executed == [
        "read_file",
        "write_markdown_file",
        "read_file",
        "write_html_file",
        "inspect_slide",
    ]
    assert initial_markdown_values == [
        "由运行时通过 <financial_deck_source_index> 和 <current_page_source> 逐页提供"
    ]
