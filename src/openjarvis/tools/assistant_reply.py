"""assistant_reply.py — the only allowed text channel to the user."""

from __future__ import annotations

from typing import Any

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec


@ToolRegistry.register("assistant_reply")
class AssistantReplyTool(BaseTool):
    """Return a user-visible message (answer, clarify, or unsupported)."""

    tool_id = "assistant_reply"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="assistant_reply",
            description=(
                "Сообщение пользователю (единственный текстовый канал). "
                "Когда использовать: приветствие; уточнение какой файл из списка; "
                "короткий статус ТОЛЬКО после успешного open_path/close_path/"
                "file_search/office_word; «пока не умею». "
                "ЗАПРЕЩЕНО: писать «Открыл файл …» или «Закрыл …» без предварительного "
                "вызова open_path / close_path в этом же запросе — такой ответ "
                "отклоняется. Чтобы открыть файл: сначала open_path(path=...), "
                "потом можно assistant_reply со статусом."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Message shown to the user.",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["answer", "clarify", "unsupported"],
                        "description": (
                            "answer=normal reply/status; "
                            "clarify=need more info; "
                            "unsupported=cannot do this yet."
                        ),
                    },
                },
                "required": ["text"],
            },
            category="system",
            required_capabilities=[],
        )

    def execute(self, **params: Any) -> ToolResult:
        text = str(params.get("text") or "").strip()
        kind = str(params.get("kind") or "answer").strip().lower()
        if kind not in {"answer", "clarify", "unsupported"}:
            kind = "answer"
        if not text:
            return ToolResult(
                tool_name="assistant_reply",
                content="Empty assistant_reply text.",
                success=False,
            )
        return ToolResult(
            tool_name="assistant_reply",
            content=text,
            success=True,
            metadata={"kind": kind},
        )


__all__ = ["AssistantReplyTool"]
