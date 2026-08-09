"""List directory tool — list folder entries without shell_exec."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_MAX_ENTRIES = 500
_MAX_DEPTH = 4


@ToolRegistry.register("list_dir")
class ListDirTool(BaseTool):
    """List files and folders in a directory."""

    tool_id = "list_dir"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="list_dir",
            description=(
                "Список содержимого одной известной папки (опционально glob). "
                "Когда использовать: «что в папке Downloads/E:\\...». "
                "Когда НЕ использовать: поиск файла по имени по всему диску "
                "(file_search); shell_exec dir /s."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path (e.g. D:\\ or C:\\Users).",
                    },
                    "pattern": {
                        "type": "string",
                        "description": "Optional glob, e.g. '*выписка*.doc*' or '*.docx'.",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": (
                            "Search subfolders (max depth 4). "
                            "Use for finding files by pattern."
                        ),
                    },
                },
                "required": ["path"],
            },
            category="filesystem",
            required_capabilities=["file:read"],
        )

    def execute(self, **params: Any) -> ToolResult:
        raw = params.get("path", "")
        if not raw:
            return ToolResult(
                tool_name="list_dir",
                content="No path provided.",
                success=False,
            )
        path = Path(raw)
        if not path.exists():
            return ToolResult(
                tool_name="list_dir",
                content=f"Path not found: {raw}",
                success=False,
            )
        if not path.is_dir():
            return ToolResult(
                tool_name="list_dir",
                content=f"Not a directory: {raw}",
                success=False,
            )

        pattern = (params.get("pattern") or "*").strip() or "*"
        recursive = bool(params.get("recursive"))
        try:
            if recursive:
                entries = self._recursive_glob(path, pattern)
            else:
                entries = sorted(
                    path.glob(pattern),
                    key=lambda p: (not p.is_dir(), p.name.lower()),
                )
        except OSError as exc:
            return ToolResult(
                tool_name="list_dir",
                content=f"Cannot list {raw}: {exc}",
                success=False,
            )

        lines: list[str] = [
            f"Directory: {path.resolve()}"
            + (f"  (recursive, pattern={pattern!r})" if recursive else ""),
            "",
        ]
        count = 0
        for entry in entries:
            if count >= _MAX_ENTRIES:
                lines.append(f"... truncated after {_MAX_ENTRIES} entries")
                break
            kind = "DIR " if entry.is_dir() else "FILE"
            try:
                size = "" if entry.is_dir() else f"  {entry.stat().st_size} bytes"
            except OSError:
                size = ""
            try:
                rel = entry.relative_to(path)
                shown = str(rel) if recursive else entry.name
            except ValueError:
                shown = str(entry)
            lines.append(f"{kind}  {shown}{size}")
            count += 1

        if count == 0:
            lines.append("(empty / no matches)")

        return ToolResult(
            tool_name="list_dir",
            content="\n".join(lines),
            success=True,
            metadata={"count": count, "recursive": recursive},
        )

    @staticmethod
    def _recursive_glob(root: Path, pattern: str) -> list[Path]:
        """Glob with a hard depth cap so whole-drive scans stay fast."""
        matches: list[Path] = []
        root_depth = len(root.resolve().parts)
        # rglob is fine when pattern has wildcards; still cap by depth.
        for entry in root.rglob(pattern):
            try:
                depth = len(entry.resolve().parts) - root_depth
            except OSError:
                continue
            if depth > _MAX_DEPTH:
                continue
            matches.append(entry)
            if len(matches) >= _MAX_ENTRIES:
                break
        matches.sort(key=lambda p: (not p.is_dir(), str(p).lower()))
        return matches


__all__ = ["ListDirTool"]
