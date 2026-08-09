"""close_path.py — close an open file window (PDF, Word, etc.) on Windows."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_PS_UTF8_PREAMBLE = r"""
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
"""


def _run_powershell(script: str, timeout: int = 45) -> tuple[int, str, str]:
    fd, path = tempfile.mkstemp(suffix=".ps1", prefix="oj_close_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig") as fh:
            fh.write(_PS_UTF8_PREAMBLE)
            fh.write("\n")
            fh.write(script)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        proc = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                path,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=creationflags,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


@ToolRegistry.register("close_path")
class ClosePathTool(BaseTool):
    """Close a file that was opened in a viewer/editor window."""

    tool_id = "close_path"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="close_path",
            description=(
                "Close an already-open file window on Windows "
                "(PDF viewer, Word, Edge/Chrome tab title, Photos, etc.). "
                "Use when the user says close/закрой a file. "
                "Pass the full path if known (from the previous open), "
                "or omit path to close the last-mentioned / active Word doc. "
                "NEVER use open_path to close — that opens again."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Full path of the open file, e.g. E:\\CV.pdf. "
                            "Optional for Word active document."
                        ),
                    },
                    "save": {
                        "type": "boolean",
                        "description": "For Word: save before close (default false).",
                    },
                },
                "required": [],
            },
            category="filesystem",
            required_capabilities=["file:read"],
        )

    def execute(self, **params: Any) -> ToolResult:
        if sys.platform != "win32":
            return ToolResult(
                tool_name="close_path",
                content="close_path is only available on Windows.",
                success=False,
            )

        raw = (params.get("path") or "").strip()
        save = bool(params.get("save") or False)
        path = Path(raw) if raw else None
        suffix = path.suffix.lower() if path else ""

        # Word documents: use COM (reliable).
        if not path or suffix in {".doc", ".docx"}:
            word = self._close_word(str(path) if path else "", save=save)
            if word.success or not path:
                return word

        if not path:
            return ToolResult(
                tool_name="close_path",
                content="path is required to close a non-Word file.",
                success=False,
            )

        # Any other file (PDF, images, …): close windows by title match.
        win = self._close_by_window_title(path)
        if win.success:
            return win

        # If Word had the file open under another extension path attempt failed earlier.
        if suffix not in {".doc", ".docx"}:
            # Still try Word in case user opened via Word
            word2 = self._close_word(str(path), save=save)
            if word2.success:
                return word2

        return win

    def _close_word(self, path: str, *, save: bool) -> ToolResult:
        from openjarvis.tools.office_word import OfficeWordTool

        return OfficeWordTool().execute(action="close", path=path, save=save)

    def _close_by_window_title(self, path: Path) -> ToolResult:
        name = path.name
        stem = path.stem
        # Foxit/Edge often show "Siarhei Sidarovich" for "Siarhei Sidarovich_CV.pdf"
        variants = [
            name,
            stem,
            stem.replace("_", " "),
            name.replace("_", " "),
            str(path.resolve()),
        ]
        tokens = [t for t in re_split_tokens(stem) if len(t) >= 4]
        variants_ps = ", ".join(
            f"'{v.replace(chr(39), chr(39)+chr(39))}'" for v in variants if v
        )
        tokens_ps = ", ".join(
            f"'{t.replace(chr(39), chr(39)+chr(39))}'" for t in tokens
        )
        script = f"""
$ErrorActionPreference = 'SilentlyContinue'
$needles = @({variants_ps})
$tokens = @({tokens_ps})
$closed = @()
$procs = Get-Process | Where-Object {{ $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle }}
foreach ($p in $procs) {{
  $title = $p.MainWindowTitle
  $hit = $false
  foreach ($n in $needles) {{
    if ($n -and ($title -like ('*' + $n + '*'))) {{ $hit = $true; break }}
  }}
  if (-not $hit -and $tokens.Count -gt 0) {{
    $matched = 0
    foreach ($t in $tokens) {{
      if ($title -like ('*' + $t + '*')) {{ $matched++ }}
    }}
    $need = [Math]::Min(2, $tokens.Count)
    if ($matched -ge $need) {{ $hit = $true }}
  }}
  if (-not $hit) {{ continue }}
  $ok = $p.CloseMainWindow()
  if ($ok) {{
    $closed += ($p.ProcessName + ' :: ' + $title)
    Start-Sleep -Milliseconds 300
  }}
}}
if ($closed.Count -eq 0) {{
  Write-Output 'NO_WINDOW'
  exit 3
}}
Write-Output ('CLOSED_COUNT=' + $closed.Count)
foreach ($c in $closed) {{ Write-Output ('CLOSED=' + $c) }}
"""
        code, out, err = _run_powershell(script)
        if code == 3 or "NO_WINDOW" in out:
            return ToolResult(
                tool_name="close_path",
                content=(
                    f"No open window found for: {path}. "
                    "Open it first, or close the viewer manually."
                ),
                success=False,
            )
        if code != 0:
            return ToolResult(
                tool_name="close_path",
                content=f"close failed: {err or out}",
                success=False,
            )
        return ToolResult(
            tool_name="close_path",
            content=out.strip() or f"Closed window for {path.name}",
            success=True,
        )


def re_split_tokens(stem: str) -> list[str]:
    import re

    return [t for t in re.split(r"[\s_\-]+", stem or "") if t]


__all__ = ["ClosePathTool"]
