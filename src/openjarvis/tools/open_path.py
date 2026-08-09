"""Open a file or folder with the Windows default application."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec


@ToolRegistry.register("open_path")
class OpenPathTool(BaseTool):
    """Open a path with the OS default handler (Word, Explorer, etc.)."""

    tool_id = "open_path"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="open_path",
            description=(
                "Открыть файл или папку приложением Windows по умолчанию "
                "(Word, Foxit/PDF, Explorer). "
                "Когда использовать: пользователь просит открыть/показать "
                "существующий файл и полный путь уже известен "
                "(из file_search, контекста или сообщения). "
                "Когда НЕ использовать: закрытие (close_path); поиск по имени "
                "(file_search); создание/клонирование Word (office_word)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Full path to file or folder, e.g. E:\\report.docx",
                    },
                },
                "required": ["path"],
            },
            category="filesystem",
            required_capabilities=["file:read"],
        )

    def execute(self, **params: Any) -> ToolResult:
        raw = (params.get("path") or "").strip()
        if not raw:
            return ToolResult(
                tool_name="open_path",
                content="No path provided.",
                success=False,
            )
        path = Path(raw)
        if not path.exists():
            return ToolResult(
                tool_name="open_path",
                content=f"Path not found: {raw}",
                success=False,
            )

        resolved = str(path.resolve())
        try:
            if sys.platform == "win32":
                os.startfile(resolved)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.run(["open", resolved], check=False)
            else:
                subprocess.run(["xdg-open", resolved], check=False)
        except OSError as exc:
            return ToolResult(
                tool_name="open_path",
                content=f"Failed to open {resolved}: {exc}",
                success=False,
            )

        kind = "folder" if path.is_dir() else "file"
        try:
            from openjarvis.tools.session_context import note_opened

            note_opened(resolved)
        except Exception:
            pass
        return ToolResult(
            tool_name="open_path",
            content=f"Opened {kind}: {resolved}",
            success=True,
        )


__all__ = ["OpenPathTool"]
