"""session_context.py — remember recent file actions for the agent prompt.

Mirrors the QR consultant pattern: inject known paths into the system
prompt so the model does not invent or ask for paths it already used.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Optional

from openjarvis.core.config import get_config_dir

_LOCK = threading.Lock()
_MAX_HITS = 12


def _path() -> Path:
    return get_config_dir() / "session_file_context.json"


def _load() -> dict[str, Any]:
    p = _path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data: dict[str, Any]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def note_opened(path: str) -> None:
    path = (path or "").strip()
    if not path:
        return
    with _LOCK:
        data = _load()
        data["last_opened"] = path
        data["last_closed"] = None
        _save(data)


def note_closed(path: str) -> None:
    path = (path or "").strip()
    if not path:
        return
    with _LOCK:
        data = _load()
        data["last_closed"] = path
        if data.get("last_opened") == path:
            data["last_opened"] = None
        _save(data)


def note_search(query: str, hits: list[str]) -> None:
    with _LOCK:
        data = _load()
        data["last_search_query"] = (query or "").strip()
        data["last_search_hits"] = [h for h in hits if h][:_MAX_HITS]
        _save(data)


def get_last_opened() -> Optional[str]:
    with _LOCK:
        v = _load().get("last_opened")
        return str(v) if v else None


def build_file_context_prompt() -> str:
    """Russian context block for the system prompt (empty if nothing known)."""
    with _LOCK:
        data = _load()
    lines: list[str] = []
    opened = data.get("last_opened")
    closed = data.get("last_closed")
    hits = data.get("last_search_hits") or []
    query = data.get("last_search_query") or ""

    if opened:
        lines.append(f"Последний открытый файл: {opened}")
        lines.append(
            "Если пользователь говорит «его/этот/закрой/открой ещё раз» "
            "без пути — используй этот путь."
        )
    if closed:
        lines.append(f"Последний закрытый файл: {closed}")
    if hits:
        lines.append(
            f"Последний поиск ({query!r}) — найденные пути "
            f"(нумерация для выбора пользователем):"
        )
        for i, h in enumerate(hits, 1):
            lines.append(f"  {i}. {h}")

    if not lines:
        return ""
    return (
        "КОНТЕКСТ ФАЙЛОВ НА ЭТОМ ПК (уже известно — не спрашивай путь зря):\n"
        + "\n".join(lines)
    )


__all__ = [
    "note_opened",
    "note_closed",
    "note_search",
    "get_last_opened",
    "build_file_context_prompt",
]
