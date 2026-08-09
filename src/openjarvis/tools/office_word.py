"""Office Word tool — create/edit Word documents via COM (Windows).

clone is document-agnostic: copy any .doc/.docx template, then apply exact
old→new string replacements (longest first). No per-form name/date heuristics.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_MAX_READ_CHARS = 20_000
_DATE_KEY_RE = re.compile(r"^(\d{2}\.\d{2}\.)(\d{4})$")
# Single-token keys shorter than this are dropped when a longer key contains them.
_SHORT_KEY_MAX = 16


_PS_UTF8_PREAMBLE = r"""
$ErrorActionPreference = 'Stop'
$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
"""


def _run_powershell(script: str, timeout: int = 90) -> tuple[int, str, str]:
    """Run a PowerShell script file; return (code, stdout, stderr).

    Scripts that need Cyrillic should write results to a UTF-8 file and print
    only ASCII status lines — console code pages corrupt Word COM strings.
    """
    fd, path = tempfile.mkstemp(suffix=".ps1", prefix="oj_word_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig") as fh:
            fh.write(_PS_UTF8_PREAMBLE)
            fh.write("\n")
            fh.write(script)
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
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
            env=env,
            creationflags=creationflags,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _write_text_temp(text: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".txt", prefix="oj_word_body_")
    with os.fdopen(fd, "w", encoding="utf-8-sig") as fh:
        fh.write(text)
    return path


def _read_utf8_file(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _looks_like_mojibake(text: str) -> bool:
    """True when text looks like broken encoding (not valid Cyrillic Word text)."""
    if not text:
        return False
    sample = text[:4000]
    # Replacement chars or heavy CJK/private-use from wrong console code page
    bad = sum(1 for ch in sample if ch == "\ufffd" or (0x3000 <= ord(ch) <= 0x9FFF))
    if sample and bad / max(len(sample), 1) > 0.08:
        return True
    # Valid Word read/create dumps often include Cyrillic; require some letters
    # if the payload is long and has almost no Cyrillic/Latin letters.
    letters = sum(1 for ch in sample if ch.isalpha())
    cyr = sum(1 for ch in sample if "\u0400" <= ch <= "\u04FF")
    if len(sample) > 400 and letters > 50 and cyr < 5 and bad > 0:
        return True
    return False

def _looks_like_read_dump(text: str) -> bool:
    """True when create was fed a pasted office_word read payload."""
    t = (text or "").lstrip()
    return t.startswith("NAME=") and "---TEXT---" in t


def _default_save_path() -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if Path("D:/").exists():
        return str(Path("D:/") / f"document_{stamp}.docx")
    docs = Path.home() / "Documents"
    docs.mkdir(parents=True, exist_ok=True)
    return str(docs / f"document_{stamp}.docx")


@ToolRegistry.register("office_word")
class OfficeWordTool(BaseTool):
    """Create and edit Microsoft Word documents via COM."""

    tool_id = "office_word"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="office_word",
            description=(
                "Word на Windows через COM. "
                "Когда использовать: выписки/формы по шаблону → action=clone "
                "(template_path, path, replacements с ТОЧНЫМИ фразами из шаблона); "
                "читать активный doc → read; править текст → replace_text; "
                "закрыть Word-doc → close (или close_path). "
                "Когда НЕ использовать: просто открыть файл → open_path; "
                "PDF → close_path/open_path; create для форм (ломает таблицы). "
                "Не вставляй dump read в create."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "clone",
                            "create",
                            "status",
                            "read",
                            "replace",
                            "replace_text",
                            "append",
                            "insert",
                            "save",
                            "open",
                            "close",
                        ],
                        "description": (
                            "clone=copy any template + exact replacements; "
                            "create=blank plain-text doc; "
                            "replace_text=Find/Replace in active doc; "
                            "replace=overwrite whole body (destroys tables!); "
                            "close=close active Word doc (or path=); "
                            "open/save/read/status/append/insert."
                        ),
                    },
                    "text": {
                        "type": "string",
                        "description": "Document body for create/replace/append/insert.",
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "Destination path for create/clone, path for open, "
                            "or document to close (optional for close=active)."
                        ),
                    },
                    "save": {
                        "type": "boolean",
                        "description": (
                            "For action=close: save before closing (default false)."
                        ),
                    },
                    "template_path": {
                        "type": "string",
                        "description": "Source template file for action=clone.",
                    },
                    "replacements": {
                        "type": "string",
                        "description": (
                            "For clone/replace_text: JSON object "
                            '{"exact text from template":"new text",...} '
                            "OR lines old=>new. Keys must be full phrases."
                        ),
                    },
                },
                "required": ["action"],
            },
            category="office",
            requires_confirmation=False,
            timeout_seconds=120.0,
            required_capabilities=["file:write"],
        )

    def execute(self, **params: Any) -> ToolResult:
        if sys.platform != "win32":
            return ToolResult(
                tool_name="office_word",
                content="office_word is only available on Windows.",
                success=False,
            )

        action = (params.get("action") or "").strip().lower()
        text = params.get("text") or ""
        path = params.get("path") or ""
        template_path = params.get("template_path") or ""
        replacements = params.get("replacements") or ""

        if action == "clone":
            return self._clone(template_path, path, replacements)
        if action == "create":
            if _looks_like_read_dump(text) or _looks_like_mojibake(text):
                return ToolResult(
                    tool_name="office_word",
                    content=(
                        "Refused action=create: text looks like a read dump "
                        "or broken encoding. For forms use action=clone with "
                        "template_path + exact replacements JSON. "
                        "Do not paste read output into create."
                    ),
                    success=False,
                )
            return self._create(text, path)
        if action == "status":
            return self._status()
        if action == "read":
            return self._read()
        if action == "replace_text":
            return self._replace_text(replacements)
        if action == "replace":
            return self._write_body(text, mode="replace")
        if action == "append":
            return self._write_body(text, mode="append")
        if action == "insert":
            return self._write_body(text, mode="insert")
        if action == "save":
            return self._save()
        if action == "open":
            return self._open(path)
        if action == "close":
            save = params.get("save")
            save_flag = bool(save) if save is not None else False
            return self._close(path, save=save_flag)
        return ToolResult(
            tool_name="office_word",
            content=(
                f"Unknown action: {action}. "
                "Use clone|create|status|read|replace_text|replace|"
                "append|insert|save|open|close."
            ),
            success=False,
        )

    # ------------------------------------------------------------------
    # Replacements helpers (generic)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_replacements(raw: str) -> dict[str, str]:
        raw = (raw or "").strip()
        if not raw:
            return {}
        out: dict[str, str] = {}
        if raw.startswith("{"):
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    out = {str(k): str(v) for k, v in data.items() if str(k)}
            except json.JSONDecodeError:
                out = {}
        if not out:
            for line in raw.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=>" in line:
                    old, new = line.split("=>", 1)
                elif "|" in line:
                    old, new = line.split("|", 1)
                else:
                    continue
                old, new = old.strip(), new.strip()
                if old:
                    out[old] = new
        return out

    @classmethod
    def _prepare_replacements(cls, raw: str) -> tuple[dict[str, str], set[str]]:
        """Return (expanded pairs for Word, primary keys for MISSING report)."""
        base = cls._sanitize_replacements(cls._parse_replacements(raw))
        primary = set(base.keys())
        expanded = cls._sanitize_replacements(cls._expand_date_splits(base))
        return expanded, primary

    @staticmethod
    def _expand_date_splits(pairs: dict[str, str]) -> dict[str, str]:
        """If a key is dd.mm.yyyy, also try Word soft-break / « г.» variants.

        Only expands keys the caller already provided — does not guess which
        dates in the document should change.
        """
        expanded = dict(pairs)
        breaks = ("\r", "\n", "\r\n", "\v", "\x0b")
        for old, new in list(pairs.items()):
            om = _DATE_KEY_RE.match(old.strip())
            nm = _DATE_KEY_RE.match(new.strip())
            if not om or not nm:
                continue
            o_pref, o_year = om.group(1), om.group(2)
            n_pref, n_year = nm.group(1), nm.group(2)
            for br in breaks:
                expanded[o_pref + br + o_year] = n_pref + br + n_year
                expanded[o_pref + br + o_year + " г."] = n_pref + br + n_year + " г."
            for suffix in (" г.", " г", "г."):
                expanded[old + suffix] = new + suffix
        return expanded

    @staticmethod
    def _sanitize_replacements(pairs: dict[str, str]) -> dict[str, str]:
        """Drop short single-token keys that are substrings of longer keys.

        Prevents «Surname»→«New» from leaving leftover name/position fragments
        when a full phrase key is also present.
        """
        cleaned: dict[str, str] = {}
        for old, new in pairs.items():
            o = (old or "").strip()
            n = (new or "").strip()
            if not o or o == n:
                continue
            cleaned[o] = n

        keys = list(cleaned.keys())
        drop: set[str] = set()
        for short in keys:
            is_short = (
                len(short) < _SHORT_KEY_MAX
                and (" " not in short)
                and ("," not in short)
            )
            if not is_short:
                continue
            for longer in keys:
                if longer != short and short in longer and len(longer) > len(short) + 3:
                    drop.add(short)
                    break
        for k in drop:
            cleaned.pop(k, None)

        return dict(sorted(cleaned.items(), key=lambda kv: len(kv[0]), reverse=True))

    # ------------------------------------------------------------------
    # clone
    # ------------------------------------------------------------------

    def _clone(
        self,
        template_path: str,
        dest_path: str,
        replacements_raw: str,
    ) -> ToolResult:
        if not template_path.strip():
            # Allow cloning the active Word document when template_path omitted.
            status = self._status()
            if status.success:
                for line in (status.content or "").splitlines():
                    if line.startswith("ACTIVE_PATH="):
                        template_path = line.split("=", 1)[1].strip()
                        break
        if not template_path.strip():
            return ToolResult(
                tool_name="office_word",
                content=(
                    "clone requires template_path (existing Word file), "
                    "or an active document open in Word."
                ),
                success=False,
            )
        src = Path(template_path)
        if not src.exists():
            return ToolResult(
                tool_name="office_word",
                content=f"Template not found: {template_path}",
                success=False,
            )
        if not dest_path.strip():
            dest_path = str(src.with_name(src.stem + "_copy" + src.suffix))
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        replacements, primary_keys = self._prepare_replacements(replacements_raw)
        if not replacements:
            return ToolResult(
                tool_name="office_word",
                content=(
                    "clone requires replacements JSON with exact old→new pairs "
                    'taken from the template text, e.g. {"old phrase":"new phrase"}.'
                ),
                success=False,
            )

        fd, repl_path = tempfile.mkstemp(suffix=".json", prefix="oj_word_repl_")
        out_fd, out_path = tempfile.mkstemp(suffix=".txt", prefix="oj_word_clone_out_")
        os.close(out_fd)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(replacements, fh, ensure_ascii=False)
            src_ps = str(src.resolve()).replace("'", "''")
            dest_ps = str(dest.resolve()).replace("'", "''")
            repl_ps = repl_path.replace("'", "''")
            out_ps = out_path.replace("'", "''")
            # Copy via Word SaveAs (works when template is open / .doc locked).
            # Result log written to UTF-8 file so Cyrillic paths survive.
            script = f"""
try {{
  $w = [Runtime.InteropServices.Marshal]::GetActiveObject('Word.Application')
}} catch {{
  $w = New-Object -ComObject Word.Application
}}
$w.Visible = $true
$src = '{src_ps}'
$dst = '{dest_ps}'
$outFile = '{out_ps}'
$dstName = [IO.Path]::GetFileName($dst)
foreach ($doc in @($w.Documents)) {{
  try {{
    $samePath = $doc.FullName -and ($doc.FullName -ieq $dst)
    $sameName = $doc.Name -and ($doc.Name -ieq $dstName)
    if ($samePath -or $sameName) {{ $doc.Close([ref]0) }}
  }} catch {{ }}
}}
# Prefer file copy; if locked, Word SaveAs from a read-only open.
$copied = $false
try {{
  Copy-Item -LiteralPath $src -Destination $dst -Force -ErrorAction Stop
  $copied = $true
}} catch {{ }}
if ($copied) {{
  $d = $w.Documents.Open($dst, $false, $false, $false)
}} else {{
  $ro = $w.Documents.Open($src, $false, $true, $false)
  $fmt = 0
  if ($dst -match '\\.docx$') {{ $fmt = 16 }}
  $null = $ro.SaveAs([ref]$dst, [ref]$fmt)
  $ro.Close([ref]0)
  $d = $w.Documents.Open($dst, $false, $false, $false)
}}
$replJson = [IO.File]::ReadAllText('{repl_ps}', [Text.Encoding]::UTF8)
$repl = $replJson | ConvertFrom-Json
$count = 0
$applied = New-Object System.Collections.Generic.List[string]
$missing = New-Object System.Collections.Generic.List[string]

function Apply-FindReplace([object]$range, [string]$findText, [string]$replaceText) {{
  try {{
    $find = $range.Find
    $find.ClearFormatting()
    $find.Replacement.ClearFormatting()
    return [bool]$find.Execute(
      $findText, $false, $false, $false, $false, $false, $true, 1, $false,
      $replaceText, 2
    )
  }} catch {{ return $false }}
}}

function Apply-CellStringReplace([object]$cell, [string]$findText, [string]$replaceText) {{
  try {{
    $rng = $cell.Range
    if (($rng.End - $rng.Start) -le 2) {{ return $false }}
    $textRng = $d.Range($rng.Start, $rng.End - 1)
    $t = [string]$textRng.Text
    if ([string]::IsNullOrEmpty($t)) {{ return $false }}
    $soft = [char]11
    $findSoft = $findText.Replace(' ', [string]$soft)
    $replSoft = $replaceText.Replace(' ', [string]$soft)
    if ($t.Contains($findText)) {{
      $textRng.Text = $t.Replace($findText, $replaceText)
      return $true
    }}
    if ($t.Contains($findSoft)) {{
      $textRng.Text = $t.Replace($findSoft, $replSoft)
      return $true
    }}
    $norm = ($t -replace [char]11, ' ' -replace [char]13, ' ' -replace [char]7, '')
    $findN = ($findText -replace [char]11, ' ' -replace [char]13, ' ')
    if ($norm.Contains($findN)) {{
      $textRng.Text = $norm.Replace($findN, $replaceText)
      return $true
    }}
  }} catch {{ }}
  return $false
}}

if ($null -ne $repl) {{
  $props = @($repl.PSObject.Properties) | Sort-Object {{ $_.Name.Length }} -Descending
  foreach ($prop in $props) {{
    $findText = [string]$prop.Name
    $replaceText = [string]$prop.Value
    if ([string]::IsNullOrEmpty($findText)) {{ continue }}
    $hit = $false
    if (Apply-FindReplace $d.Content $findText $replaceText) {{ $hit = $true; $count++ }}
    foreach ($table in @($d.Tables)) {{
      for ($i = 1; $i -le $table.Range.Cells.Count; $i++) {{
        try {{
          $cell = $table.Range.Cells.Item($i)
          if (Apply-FindReplace $cell.Range $findText $replaceText) {{
            $hit = $true; $count++
          }} elseif (Apply-CellStringReplace $cell $findText $replaceText) {{
            $hit = $true; $count++
          }}
        }} catch {{ }}
      }}
    }}
    $after = [string]$d.Content.Text
    if ($hit -and -not $after.Contains($findText)) {{
      [void]$applied.Add($findText)
    }} elseif ($after.Contains($findText) -or -not $hit) {{
      [void]$missing.Add($findText)
    }}
  }}
}}
$d.Save()
$w.Activate()
try {{ $w.WindowState = 0 }} catch {{ }}
$lines = @(
  ('CLONED=' + $d.FullName),
  ('REPLACEMENTS_APPLIED=' + $count),
  ('APPLIED=' + ($applied -join ' || ')),
  ('CHARS=' + $d.Content.Text.Length)
)
if ($missing.Count -gt 0) {{
  $lines += ('MISSING_OLD=' + ($missing -join ' || '))
}}
[IO.File]::WriteAllText($outFile, ($lines -join "`n"), [Text.UTF8Encoding]::new($false))
Write-Output 'CLONE_OK'
"""
            code, out, err = _run_powershell(script, timeout=180)
            report = ""
            if os.path.isfile(out_path):
                report = _read_utf8_file(out_path).strip()
        finally:
            try:
                os.unlink(repl_path)
            except OSError:
                pass
            try:
                os.unlink(out_path)
            except OSError:
                pass

        if code != 0 or "CLONE_OK" not in out or "CLONED=" not in report:
            return ToolResult(
                tool_name="office_word",
                content=f"clone failed: {err or out or report}",
                success=False,
            )
        # Only report MISSING for user-supplied keys (ignore date-split variants).
        lines_out: list[str] = []
        missing_primary: list[str] = []
        applied_items: list[str] = []
        for line in report.splitlines():
            if line.startswith("APPLIED="):
                applied_items = [
                    p.strip() for p in line.split("=", 1)[1].split(" || ") if p.strip()
                ]
                lines_out.append(line)
                continue
            if line.startswith("MISSING_OLD="):
                raw_miss = line.split("=", 1)[1]
                for part in raw_miss.split(" || "):
                    p = part.strip()
                    if p in primary_keys:
                        missing_primary.append(p)
                continue
            lines_out.append(line)

        def _norm_key(s: str) -> str:
            return (
                s.replace("\x0b", " ")
                .replace("\r", " ")
                .replace("\n", " ")
                .replace("  ", " ")
                .strip()
            )

        # If a soft-break variant of a primary key was applied, don't warn.
        applied_norm = [_norm_key(a) for a in applied_items]
        missing_primary = [
            p
            for p in missing_primary
            if not any(
                _norm_key(p) in a or a.startswith(_norm_key(p)[:12])
                for a in applied_norm
            )
        ]
        if missing_primary:
            lines_out.append("MISSING_OLD=" + " || ".join(missing_primary))
        report = "\n".join(lines_out)
        note = ""
        if missing_primary:
            note = (
                "\nWarning: some old strings were not found in the template. "
                "Use office_word action=read, copy EXACT phrases into replacements, retry."
            )
        return ToolResult(
            tool_name="office_word",
            content=(
                "Document cloned from template (formatting/tables kept) "
                "and opened in Word.\n" + report + note
            ),
            success=True,
        )

    def _replace_text(self, replacements_raw: str) -> ToolResult:
        replacements, _primary = self._prepare_replacements(replacements_raw)
        if not replacements:
            return ToolResult(
                tool_name="office_word",
                content="replace_text requires replacements (JSON or old=>new lines).",
                success=False,
            )
        fd, repl_path = tempfile.mkstemp(suffix=".json", prefix="oj_word_repl_")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(replacements, fh, ensure_ascii=False)
            repl_ps = repl_path.replace("'", "''")
            script = f"""
$ErrorActionPreference = 'Stop'
try {{
  $w = [Runtime.InteropServices.Marshal]::GetActiveObject('Word.Application')
}} catch {{
  Write-Output 'WORD_NOT_RUNNING'
  exit 2
}}
$d = $w.ActiveDocument
if ($null -eq $d) {{ Write-Output 'NO_ACTIVE_DOCUMENT'; exit 3 }}
$replJson = [IO.File]::ReadAllText('{repl_ps}', [Text.Encoding]::UTF8)
$repl = $replJson | ConvertFrom-Json
$count = 0
$missing = New-Object System.Collections.Generic.List[string]
$props = @($repl.PSObject.Properties) | Sort-Object {{ $_.Name.Length }} -Descending
foreach ($prop in $props) {{
  $findText = [string]$prop.Name
  $replaceText = [string]$prop.Value
  if ([string]::IsNullOrEmpty($findText)) {{ continue }}
  $find = $d.Content.Find
  $find.ClearFormatting()
  $find.Replacement.ClearFormatting()
  $ok = $find.Execute(
    $findText, $false, $false, $false, $false, $false, $true, 1, $false,
    $replaceText, 2
  )
  if ($ok) {{ $count++ }}
  if (([string]$d.Content.Text).Contains($findText)) {{ [void]$missing.Add($findText) }}
}}
$d.Save()
Write-Output ('OK path=' + $d.FullName)
Write-Output ('REPLACEMENTS_APPLIED=' + $count)
if ($missing.Count -gt 0) {{
  Write-Output ('MISSING_OLD=' + ($missing -join ' || '))
}}
"""
            code, out, err = _run_powershell(script)
        finally:
            try:
                os.unlink(repl_path)
            except OSError:
                pass
        if code != 0:
            return ToolResult(
                tool_name="office_word",
                content=f"replace_text failed: {err or out}",
                success=False,
            )
        return ToolResult(
            tool_name="office_word",
            content=out.strip(),
            success=True,
        )

    def _create(self, text: str, path: str) -> ToolResult:
        if not text.strip():
            return ToolResult(
                tool_name="office_word",
                content="create requires non-empty text (the letter body).",
                success=False,
            )
        save_path = path.strip() or _default_save_path()
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        body_path = _write_text_temp(text)
        body_ps = body_path.replace("'", "''")
        save_ps = str(Path(save_path).resolve()).replace("'", "''")
        script = f"""
$ErrorActionPreference = 'Stop'
try {{
  $w = [Runtime.InteropServices.Marshal]::GetActiveObject('Word.Application')
}} catch {{
  $w = New-Object -ComObject Word.Application
}}
$w.Visible = $true
$d = $w.Documents.Add()
$body = [IO.File]::ReadAllText('{body_ps}', [Text.Encoding]::UTF8)
$d.Content.Text = $body
$null = $d.SaveAs([ref] '{save_ps}')
$w.Activate()
try {{ $w.WindowState = 0 }} catch {{ }}
Write-Output ('CREATED=' + $d.FullName)
Write-Output ('CHARS=' + $d.Content.Text.Length)
"""
        try:
            code, out, err = _run_powershell(script, timeout=120)
        finally:
            try:
                os.unlink(body_path)
            except OSError:
                pass

        if code != 0 or "CREATED=" not in out:
            return ToolResult(
                tool_name="office_word",
                content=f"create failed: {err or out}",
                success=False,
            )
        return ToolResult(
            tool_name="office_word",
            content=f"Document created and opened in Word.\n{out.strip()}",
            success=True,
        )

    def _status(self) -> ToolResult:
        out_fd, out_path = tempfile.mkstemp(suffix=".txt", prefix="oj_word_status_")
        os.close(out_fd)
        out_ps = out_path.replace("'", "''")
        script = f"""
$outFile = '{out_ps}'
try {{
  $w = [Runtime.InteropServices.Marshal]::GetActiveObject('Word.Application')
}} catch {{
  [IO.File]::WriteAllText($outFile, "WORD_NOT_RUNNING`nHINT=Use action=create or open a document.", [Text.UTF8Encoding]::new($false))
  Write-Output 'STATUS_FAIL'
  exit 2
}}
$lines = New-Object System.Collections.Generic.List[string]
[void]$lines.Add('DOCS=' + $w.Documents.Count)
$active = $w.ActiveDocument
if ($null -eq $active) {{
  [void]$lines.Add('ACTIVE=none')
}} else {{
  [void]$lines.Add('ACTIVE_NAME=' + $active.Name)
  [void]$lines.Add('ACTIVE_PATH=' + $active.FullName)
  [void]$lines.Add('SAVED=' + $active.Saved)
  $i = 0
  foreach ($d in $w.Documents) {{
    $i++
    $mark = if ($d.FullName -eq $active.FullName) {{ '*' }} else {{ ' ' }}
    [void]$lines.Add(('DOC{{0}}{{1}}={{2}}|{{3}}' -f $mark, $i, $d.Name, $d.FullName))
  }}
}}
[IO.File]::WriteAllText($outFile, ($lines -join "`n"), [Text.UTF8Encoding]::new($false))
Write-Output 'STATUS_OK'
"""
        try:
            code, out, err = _run_powershell(script)
            body = _read_utf8_file(out_path) if os.path.isfile(out_path) else ""
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass
        if code == 2 or "WORD_NOT_RUNNING" in body:
            return ToolResult(
                tool_name="office_word",
                content=body.strip()
                or (
                    "Microsoft Word is not running. "
                    "Use action=create with the letter text."
                ),
                success=False,
            )
        if code != 0 or "STATUS_OK" not in out:
            return ToolResult(
                tool_name="office_word",
                content=f"status failed: {err or out or body}",
                success=False,
            )
        return ToolResult(tool_name="office_word", content=body.strip(), success=True)

    def _read(self) -> ToolResult:
        out_fd, out_path = tempfile.mkstemp(suffix=".txt", prefix="oj_word_read_")
        os.close(out_fd)
        out_ps = out_path.replace("'", "''")
        script = f"""
$outFile = '{out_ps}'
try {{
  $w = [Runtime.InteropServices.Marshal]::GetActiveObject('Word.Application')
}} catch {{
  [IO.File]::WriteAllText($outFile, 'WORD_NOT_RUNNING', [Text.UTF8Encoding]::new($false))
  Write-Output 'READ_FAIL'
  exit 2
}}
$d = $w.ActiveDocument
if ($null -eq $d) {{
  [IO.File]::WriteAllText($outFile, 'NO_ACTIVE_DOCUMENT', [Text.UTF8Encoding]::new($false))
  Write-Output 'READ_FAIL'
  exit 3
}}
# Build text via StringBuilder; write UTF-8 file (not console).
$sb = New-Object Text.StringBuilder
[void]$sb.AppendLine('NAME=' + $d.Name)
[void]$sb.AppendLine('PATH=' + $d.FullName)
[void]$sb.AppendLine('---TEXT---')
[void]$sb.Append([string]$d.Content.Text)
[IO.File]::WriteAllText($outFile, $sb.ToString(), [Text.UTF8Encoding]::new($false))
Write-Output 'READ_OK'
"""
        try:
            code, out, err = _run_powershell(script)
            body = _read_utf8_file(out_path) if os.path.isfile(out_path) else ""
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass
        if code == 2 or body.strip() == "WORD_NOT_RUNNING":
            return ToolResult(
                tool_name="office_word",
                content="Word is not running. Use action=create.",
                success=False,
            )
        if code == 3 or body.strip() == "NO_ACTIVE_DOCUMENT":
            return ToolResult(
                tool_name="office_word",
                content="Word is open but has no active document.",
                success=False,
            )
        if code != 0 or "READ_OK" not in out:
            return ToolResult(
                tool_name="office_word",
                content=f"read failed: {err or out or body}",
                success=False,
            )
        if "---TEXT---" in body:
            header, text = body.split("---TEXT---", 1)
            text = text.strip()
            if len(text) > _MAX_READ_CHARS:
                text = text[:_MAX_READ_CHARS] + "\n…(truncated)"
            content = header.strip() + "\n---TEXT---\n" + text
        else:
            content = body.strip()
        if _looks_like_mojibake(content):
            return ToolResult(
                tool_name="office_word",
                content=(
                    "read returned unreadable encoding. "
                    "Keep the template open in Word and retry, or pass "
                    "template_path explicitly to action=clone."
                ),
                success=False,
            )
        return ToolResult(tool_name="office_word", content=content, success=True)

    def _write_body(self, text: str, mode: str) -> ToolResult:
        if not text:
            return ToolResult(
                tool_name="office_word",
                content="No text provided.",
                success=False,
            )
        body_path = _write_text_temp(text)
        body_ps = body_path.replace("'", "''")
        if mode == "replace":
            mutate = (
                f"$body = [IO.File]::ReadAllText('{body_ps}', "
                "[Text.Encoding]::UTF8); $d.Content.Text = $body"
            )
        elif mode == "append":
            mutate = (
                f"$body = [IO.File]::ReadAllText('{body_ps}', "
                "[Text.Encoding]::UTF8); "
                "$end = $d.Content.End - 1; "
                "$rng = $d.Range($end, $end); $rng.Text = $body"
            )
        else:
            mutate = (
                f"$body = [IO.File]::ReadAllText('{body_ps}', "
                "[Text.Encoding]::UTF8); $w.Selection.TypeText($body)"
            )
        script = f"""
$ErrorActionPreference = 'Stop'
try {{
  $w = [Runtime.InteropServices.Marshal]::GetActiveObject('Word.Application')
}} catch {{
  Write-Output 'WORD_NOT_RUNNING'
  exit 2
}}
$d = $w.ActiveDocument
if ($null -eq $d) {{ Write-Output 'NO_ACTIVE_DOCUMENT'; exit 3 }}
{mutate}
Write-Output ('OK name=' + $d.Name)
Write-Output ('PATH=' + $d.FullName)
Write-Output ('CHARS=' + $d.Content.Text.Length)
"""
        try:
            code, out, err = _run_powershell(script)
        finally:
            try:
                os.unlink(body_path)
            except OSError:
                pass

        if code == 2 or "WORD_NOT_RUNNING" in out:
            return ToolResult(
                tool_name="office_word",
                content=(
                    "Word is not running. Use action=create with the full letter text "
                    "instead of replace."
                ),
                success=False,
            )
        if code == 3 or "NO_ACTIVE_DOCUMENT" in out:
            return ToolResult(
                tool_name="office_word",
                content="Word is open but has no active document. Use action=create.",
                success=False,
            )
        if code != 0 or not out.strip().startswith("OK"):
            return ToolResult(
                tool_name="office_word",
                content=f"{mode} failed: {err or out}",
                success=False,
            )
        return ToolResult(
            tool_name="office_word",
            content=f"{mode} done.\n{out.strip()}",
            success=True,
        )

    def _save(self) -> ToolResult:
        script = r"""
$ErrorActionPreference = 'Stop'
try {
  $w = [Runtime.InteropServices.Marshal]::GetActiveObject('Word.Application')
} catch {
  Write-Output 'WORD_NOT_RUNNING'
  exit 2
}
$d = $w.ActiveDocument
if ($null -eq $d) { Write-Output 'NO_ACTIVE_DOCUMENT'; exit 3 }
$d.Save()
Write-Output ('SAVED=' + $d.FullName)
"""
        code, out, err = _run_powershell(script)
        if code != 0:
            return ToolResult(
                tool_name="office_word",
                content=f"save failed: {err or out}",
                success=False,
            )
        return ToolResult(tool_name="office_word", content=out.strip(), success=True)

    def _open(self, path: str) -> ToolResult:
        if not path:
            return ToolResult(
                tool_name="office_word",
                content="path is required for action=open. Or use action=create.",
                success=False,
            )
        p = Path(path)
        if not p.exists():
            return ToolResult(
                tool_name="office_word",
                content=(
                    f"File not found: {path}. "
                    "Use action=create with text to make a new document."
                ),
                success=False,
            )
        path_ps = str(p.resolve()).replace("'", "''")
        script = f"""
$ErrorActionPreference = 'Stop'
try {{
  $w = [Runtime.InteropServices.Marshal]::GetActiveObject('Word.Application')
}} catch {{
  $w = New-Object -ComObject Word.Application
}}
$w.Visible = $true
$d = $w.Documents.Open('{path_ps}')
$w.Activate()
Write-Output ('OPENED=' + $d.FullName)
"""
        code, out, err = _run_powershell(script)
        if code != 0:
            return ToolResult(
                tool_name="office_word",
                content=f"open failed: {err or out}",
                success=False,
            )
        if code == 0 and "OPENED=" in out:
            try:
                from openjarvis.tools.session_context import note_opened

                for line in out.splitlines():
                    if line.startswith("OPENED="):
                        note_opened(line.split("=", 1)[1].strip())
                        break
            except Exception:
                pass
        return ToolResult(tool_name="office_word", content=out.strip(), success=True)

    def _close(self, path: str = "", *, save: bool = False) -> ToolResult:
        """Close active Word document, or the document matching path."""
        # wdSaveChanges=-1, wdDoNotSaveChanges=0
        save_const = -1 if save else 0
        path_ps = str(Path(path).resolve()).replace("'", "''") if path else ""
        if path_ps:
            find_doc = f"""
$target = '{path_ps}'
$d = $null
foreach ($doc in @($w.Documents)) {{
  if ($doc.FullName -and ($doc.FullName.ToLower() -eq $target.ToLower())) {{
    $d = $doc; break
  }}
}}
if ($null -eq $d) {{
  foreach ($doc in @($w.Documents)) {{
    if ($doc.Name -and ($doc.Name.ToLower() -eq ([IO.Path]::GetFileName($target).ToLower()))) {{
      $d = $doc; break
    }}
  }}
}}
if ($null -eq $d) {{ Write-Output 'DOCUMENT_NOT_OPEN'; exit 4 }}
"""
        else:
            find_doc = """
$d = $w.ActiveDocument
if ($null -eq $d) { Write-Output 'NO_ACTIVE_DOCUMENT'; exit 3 }
"""
        script = f"""
$ErrorActionPreference = 'Stop'
try {{
  $w = [Runtime.InteropServices.Marshal]::GetActiveObject('Word.Application')
}} catch {{
  Write-Output 'WORD_NOT_RUNNING'
  exit 2
}}
{find_doc}
$closed = $d.FullName
if (-not $closed) {{ $closed = $d.Name }}
$d.Close({save_const})
if ($w.Documents.Count -eq 0) {{
  $w.Quit()
  Write-Output ('CLOSED=' + $closed)
  Write-Output 'WORD_QUIT'
}} else {{
  Write-Output ('CLOSED=' + $closed)
  Write-Output ('REMAINING=' + $w.Documents.Count)
}}
"""
        code, out, err = _run_powershell(script)
        if code == 2 or "WORD_NOT_RUNNING" in out:
            return ToolResult(
                tool_name="office_word",
                content="Word is not running — nothing to close.",
                success=False,
            )
        if code == 3 or "NO_ACTIVE_DOCUMENT" in out:
            return ToolResult(
                tool_name="office_word",
                content="Word is open but has no active document.",
                success=False,
            )
        if code == 4 or "DOCUMENT_NOT_OPEN" in out:
            return ToolResult(
                tool_name="office_word",
                content=(
                    f"Document is not open in Word: {path}. "
                    "Use action=close without path to close the active document."
                ),
                success=False,
            )
        if code != 0:
            return ToolResult(
                tool_name="office_word",
                content=f"close failed: {err or out}",
                success=False,
            )
        return ToolResult(
            tool_name="office_word",
            content=out.strip(),
            success=True,
        )


__all__ = ["OfficeWordTool"]
