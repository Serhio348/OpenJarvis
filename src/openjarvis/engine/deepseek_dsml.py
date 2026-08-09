"""Parse DeepSeek V4 DSML tool-call markup from assistant content.

DeepSeek V4 may return tools as OpenAI ``tool_calls`` *or* embed them in
content using ``<｜DSML｜tool_calls>…`` blocks. When the API leaves them
only in content, the orchestrator never executes tools and the UI shows
raw DSML. This module recovers those calls.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

# Official DeepSeek token uses fullwidth vertical line U+FF5C.
# Some API/UI paths double the bars: ｜｜DSML｜｜
_DSML_ALTS = (
    "｜DSML｜",
    "｜｜DSML｜｜",
    "|DSML|",
    "||DSML||",
)


def _tag_alts(kind: str) -> str:
    """Build an alternation for open/close DSML tags of a given kind."""
    parts = [rf"<{re.escape(tok)}{kind}" for tok in _DSML_ALTS]
    parts.append(rf"<{kind}")
    return "(?:" + "|".join(parts) + ")"


def _close_alts(kind: str) -> str:
    parts = [rf"</{re.escape(tok)}{kind}>" for tok in _DSML_ALTS]
    parts.append(rf"</{kind}>")
    return "(?:" + "|".join(parts) + ")"


_INVOKE_RE = re.compile(
    _tag_alts("invoke")
    + r"\s+name=\"([^\"]+)\">"
    + r"(.*?)"
    + _close_alts("invoke"),
    re.DOTALL | re.IGNORECASE,
)

_PARAM_RE = re.compile(
    _tag_alts("parameter")
    + r"\s+name=\"([^\"]+)\"\s+string=\"(true|false)\">"
    + r"(.*?)"
    + _close_alts("parameter"),
    re.DOTALL | re.IGNORECASE,
)

# Degraded form sometimes visible in UI after token stripping
_LOOSE_INVOKE_RE = re.compile(
    r"invoke\s+name=\"([^\"]+)\">\s*(.*?)\s*(?=invoke\s+name=\"|</|$)",
    re.DOTALL | re.IGNORECASE,
)


def _parse_params(block: str) -> dict[str, Any]:
    args: dict[str, Any] = {}
    for match in _PARAM_RE.finditer(block):
        key, is_string, raw = match.group(1), match.group(2), match.group(3)
        value = raw.strip()
        if is_string.lower() == "true":
            args[key] = value
        else:
            try:
                args[key] = json.loads(value)
            except json.JSONDecodeError:
                args[key] = value
    if args:
        return args
    # Fallback: JSON object in the invoke body
    stripped = block.strip()
    if stripped.startswith("{"):
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return args


def parse_dsml_tool_calls(content: str) -> list[dict[str, Any]]:
    """Extract tool calls from DSML markup in *content*."""
    if not content or "invoke" not in content.lower():
        return []

    calls: list[dict[str, Any]] = []
    for match in _INVOKE_RE.finditer(content):
        name = match.group(1)
        args = _parse_params(match.group(2))
        calls.append(
            {
                "id": f"dsml_{uuid.uuid4().hex[:10]}",
                "name": name,
                "arguments": json.dumps(args, ensure_ascii=False),
            }
        )

    if calls:
        return calls

    # Last resort for mangled markup without closing tags
    if _DSML in content or "parameter name=" in content:
        for match in _LOOSE_INVOKE_RE.finditer(content):
            name = match.group(1)
            args = _parse_params(match.group(2))
            if not args and not match.group(2).strip():
                continue
            calls.append(
                {
                    "id": f"dsml_{uuid.uuid4().hex[:10]}",
                    "name": name,
                    "arguments": json.dumps(args, ensure_ascii=False),
                }
            )
    return calls


def strip_dsml_markup(content: str) -> str:
    """Remove DSML tool-call blocks from visible assistant text."""
    if not content:
        return content
    cleaned = content
    for tok in _DSML_ALTS:
        cleaned = re.sub(
            rf"<{re.escape(tok)}tool_calls>.*?</{re.escape(tok)}tool_calls>",
            "",
            cleaned,
            flags=re.DOTALL | re.IGNORECASE,
        )
    cleaned = _INVOKE_RE.sub("", cleaned)
    # Drop any leftover bare invoke leftovers
    cleaned = re.sub(
        r"invoke\s+name=\"[^\"]+\">.*?(?:</invoke>|$)",
        "",
        cleaned,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return cleaned.strip()


def content_has_dsml(content: str) -> bool:
    if not content:
        return False
    low = content.lower()
    return "invoke name=" in low or "dsml" in low


__all__ = ["parse_dsml_tool_calls", "strip_dsml_markup"]
