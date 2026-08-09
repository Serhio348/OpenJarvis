"""file_search.py — find files by name on this PC (Windows-friendly)."""

from __future__ import annotations

import os
import shutil
import string
import subprocess
import time
from pathlib import Path
from typing import Any, Iterable

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_MAX_RESULTS = 40
_DEFAULT_TIMEOUT_SEC = 25.0
_SKIP_DIR_NAMES = {
    "$recycle.bin",
    "system volume information",
    "windows",
    "program files",
    "program files (x86)",
    "programdata",
    "node_modules",
    ".git",
    ".svn",
    ".hg",
    "__pycache__",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".rustup",
    ".cargo",
    ".nuget",
    ".dotnet",
    ".cache",
    ".local",
    ".npm",
    ".conda",
    "appdata",
    "application data",
    "crossdevice",
    "intel",
    "msocache",
    "recovery",
    "perflogs",
    "cache",
    "caches",
    "temp",
    "tmp",
}

_DOC_EXTS = {".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md"}


def _default_roots() -> list[Path]:
    home = Path.home()
    roots: list[Path] = []
    for name in ("Desktop", "Documents", "Downloads", "OneDrive"):
        p = home / name
        if p.is_dir():
            roots.append(p)
    if home.is_dir():
        roots.append(home)

    # Other fixed drives (D:, E:, …) — full volume roots.
    for letter in string.ascii_uppercase:
        drive = Path(f"{letter}:/")
        if not drive.exists():
            continue
        # Prefer user folders on C:; skip scanning all of C:\Windows etc.
        if letter.upper() == "C":
            users = Path("C:/Users")
            if users.is_dir() and users not in roots:
                roots.append(users)
            continue
        roots.append(drive)

    # Dedupe while preserving order
    seen: set[str] = set()
    out: list[Path] = []
    for r in roots:
        key = str(r.resolve()).lower()
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _normalize_query(query: str) -> tuple[str, bool]:
    """Return (needle_lower, is_glob)."""
    q = (query or "").strip()
    if not q:
        return "", False
    if any(ch in q for ch in "*?"):
        return q, True
    return q.lower(), False


def _name_matches(name: str, needle: str, is_glob: bool) -> bool:
    if is_glob:
        from fnmatch import fnmatch

        return fnmatch(name.lower(), needle.lower())

    low = name.lower()
    # Short queries ("cv") must not match inside longer tokens ("riscv").
    if len(needle) <= 3:
        stem = Path(low).stem
        if stem == needle or stem.startswith(needle + " ") or stem.endswith(" " + needle):
            return True
        # token boundary: start/end or non-alnum around needle
        import re

        return re.search(rf"(^|[^a-z0-9а-яё]){re.escape(needle)}([^a-z0-9а-яё]|$)", low) is not None
    return needle in low


def _should_skip_dir(name: str) -> bool:
    low = name.lower()
    if low in _SKIP_DIR_NAMES:
        return True
    # Skip hidden/tooling dirs under the user profile.
    if low.startswith("."):
        return True
    return False


def _rank_hit(path: str) -> tuple:
    p = Path(path)
    home = str(Path.home()).lower()
    low = path.lower()
    in_home_docs = 0
    for marker in ("\\desktop\\", "\\documents\\", "\\downloads\\", "/desktop/", "/documents/", "/downloads/"):
        if marker in low:
            in_home_docs = 1
            break
    in_home = 1 if low.startswith(home) else 0
    ext_bonus = 1 if p.suffix.lower() in _DOC_EXTS else 0
    return (-in_home_docs, -ext_bonus, -in_home, len(p.name), low)


def _search_everything(query: str, max_results: int) -> list[str] | None:
    """Use Voidtools Everything CLI if installed (`es.exe`)."""
    es = shutil.which("es.exe") or shutil.which("es")
    if not es:
        for candidate in (
            Path(r"C:\Program Files\Everything\es.exe"),
            Path(r"C:\Program Files (x86)\Everything\es.exe"),
        ):
            if candidate.is_file():
                es = str(candidate)
                break
    if not es:
        return None

    # Prefer files; query as substring unless user passed wildcards.
    args = [es, "-n", str(max_results), "-path"]
    q = query.strip()
    if not any(ch in q for ch in "*?"):
        q = f"*{q}*"
    args.append(q)
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode not in (0, 1):  # 1 = no matches for some builds
        return None
    lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    files = [ln for ln in lines if Path(ln).is_file()]
    return files[:max_results]


def _walk_search(
    roots: Iterable[Path],
    needle: str,
    is_glob: bool,
    *,
    max_results: int,
    timeout_sec: float,
    extension: str | None,
) -> list[str]:
    deadline = time.monotonic() + timeout_sec
    hits: list[str] = []
    seen: set[str] = set()
    ext = extension.lower().lstrip(".") if extension else None
    collect_cap = max(max_results * 8, max_results)

    for root in roots:
        if time.monotonic() >= deadline or len(hits) >= collect_cap:
            break
        if not root.is_dir():
            continue
        stack = [root]
        while stack and len(hits) < collect_cap and time.monotonic() < deadline:
            current = stack.pop()
            try:
                with os.scandir(current) as it:
                    for entry in it:
                        if len(hits) >= collect_cap or time.monotonic() >= deadline:
                            break
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                if _should_skip_dir(entry.name):
                                    continue
                                stack.append(Path(entry.path))
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                continue
                            name = entry.name
                            if ext and not name.lower().endswith("." + ext):
                                continue
                            if _name_matches(name, needle, is_glob):
                                resolved = str(Path(entry.path).resolve())
                                if resolved not in seen:
                                    seen.add(resolved)
                                    hits.append(resolved)
                        except OSError:
                            continue
            except OSError:
                continue
    hits.sort(key=_rank_hit)
    return hits[:max_results]


@ToolRegistry.register("file_search")
class FileSearchTool(BaseTool):
    """Search the PC for files by name fragment or glob."""

    tool_id = "file_search"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="file_search",
            description=(
                "Find files anywhere on this computer by name. "
                "Use when the user asks to find/locate a file (CV, pdf, docx, etc.). "
                "Pass query as a name fragment (e.g. 'CV', 'резюме', 'invoice') "
                "or a glob ('*.pdf'). "
                "Do NOT use shell_exec or list_dir for whole-disk finds. "
                "If exactly one good match: you may open_path it. "
                "If several matches: list the full paths for the user and ASK which "
                "to open — do NOT call open_path until they choose."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Filename fragment or glob, e.g. 'CV', 'выписка', '*.docx'."
                        ),
                    },
                    "root": {
                        "type": "string",
                        "description": (
                            "Optional folder to search under "
                            "(default: Desktop/Documents/Downloads + user drives)."
                        ),
                    },
                    "extension": {
                        "type": "string",
                        "description": "Optional extension filter without dot, e.g. pdf.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": f"Max paths to return (default {_MAX_RESULTS}).",
                    },
                },
                "required": ["query"],
            },
            category="filesystem",
            required_capabilities=["file:read"],
        )

    def execute(self, **params: Any) -> ToolResult:
        query = str(params.get("query") or "").strip()
        if not query:
            return ToolResult(
                tool_name="file_search",
                content="No query provided.",
                success=False,
            )

        max_results = int(params.get("max_results") or _MAX_RESULTS)
        max_results = max(1, min(max_results, 100))
        extension = params.get("extension")
        if extension is not None:
            extension = str(extension).strip() or None

        root_raw = str(params.get("root") or "").strip()
        if root_raw:
            root = Path(root_raw)
            if not root.is_dir():
                return ToolResult(
                    tool_name="file_search",
                    content=f"Root is not a directory: {root_raw}",
                    success=False,
                )
            roots = [root]
        else:
            roots = _default_roots()

        # Prefer Everything when available (instant index).
        if not root_raw:
            es_hits = _search_everything(query, max_results)
            if es_hits is not None:
                if extension:
                    ext = extension.lower().lstrip(".")
                    es_hits = [
                        h for h in es_hits if h.lower().endswith("." + ext)
                    ]
                body = self._format(es_hits, backend="everything", query=query)
                return ToolResult(
                    tool_name="file_search",
                    content=body,
                    success=True,
                    metadata={"count": len(es_hits), "backend": "everything"},
                )

        needle, is_glob = _normalize_query(query)
        started = time.monotonic()
        hits = _walk_search(
            roots,
            needle,
            is_glob,
            max_results=max_results,
            timeout_sec=_DEFAULT_TIMEOUT_SEC,
            extension=extension,
        )
        elapsed = time.monotonic() - started
        body = self._format(
            hits,
            backend="walk",
            query=query,
            elapsed=elapsed,
            timed_out=elapsed >= _DEFAULT_TIMEOUT_SEC - 0.05
            and len(hits) < max_results,
        )
        return ToolResult(
            tool_name="file_search",
            content=body,
            success=True,
            metadata={
                "count": len(hits),
                "backend": "walk",
                "elapsed_sec": round(elapsed, 2),
            },
        )

    @staticmethod
    def _format(
        hits: list[str],
        *,
        backend: str,
        query: str,
        elapsed: float | None = None,
        timed_out: bool = False,
    ) -> str:
        lines = [
            f"file_search query={query!r} backend={backend} matches={len(hits)}"
        ]
        if elapsed is not None:
            lines[0] += f" elapsed={elapsed:.1f}s"
        lines.append("")
        if not hits:
            lines.append("No files found.")
            if timed_out:
                lines.append(
                    "(Search time budget reached — try a more specific query "
                    "or set root= to a folder.)"
                )
            return "\n".join(lines)
        for path in hits:
            lines.append(path)
        if timed_out:
            lines.append("")
            lines.append(
                "(Stopped early due to time budget — narrow query or set root=.)"
            )
        return "\n".join(lines)


__all__ = ["FileSearchTool"]
