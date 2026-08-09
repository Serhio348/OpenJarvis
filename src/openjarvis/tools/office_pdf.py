"""office_pdf.py — PDF read/search/OCR/AcroForm/merge/split for personal PC use.

Actions:
1. read — extract text layer (pdfplumber)
2. search — find query in extracted text with page hits
3. ocr — render pages (pymupdf) + tesseract rus+eng
4. list_fields — AcroForm field names/values (pypdf)
5. fill — write AcroForm values to a new PDF (never overwrite template)
6. merge — concatenate PDFs into output_path
7. split — export selected pages to a new PDF

Empty path → last opened .pdf from session_context when available.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_DEFAULT_MAX_CHARS = 50_000
_OCR_EMPTY_THRESHOLD = 40
_ACTIONS = (
    "read",
    "search",
    "ocr",
    "list_fields",
    "fill",
    "merge",
    "split",
)


def _parse_pages(pages_str: str, total_pages: int) -> List[int]:
    """Parse 1-indexed page spec into sorted unique 0-indexed indices."""
    result: list[int] = []
    for part in pages_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_str, end_str = part.split("-", 1)
            start = max(1, int(start_str.strip()))
            end = min(total_pages, int(end_str.strip()))
            result.extend(range(start - 1, end))
        else:
            page_num = int(part)
            if 1 <= page_num <= total_pages:
                result.append(page_num - 1)
    return sorted(set(result))


def _fail(msg: str) -> ToolResult:
    return ToolResult(tool_name="office_pdf", content=msg, success=False)


def _ok(msg: str, **metadata: Any) -> ToolResult:
    return ToolResult(
        tool_name="office_pdf",
        content=msg,
        success=True,
        metadata=metadata or None,
    )


def _note_pdf(path: Path) -> None:
    try:
        from openjarvis.tools.session_context import note_opened

        note_opened(str(path.resolve()))
    except Exception:
        pass


def _resolve_pdf_path(raw: str) -> tuple[Optional[Path], Optional[str]]:
    path_str = (raw or "").strip()
    if not path_str:
        try:
            from openjarvis.tools.session_context import get_last_opened

            last = get_last_opened()
        except Exception:
            last = None
        if last and str(last).lower().endswith(".pdf"):
            path_str = last
        else:
            return None, "No path provided and no last opened PDF in session."

    path = Path(path_str)
    if path.suffix.lower() != ".pdf":
        return None, f"Not a PDF file: {path_str}"

    from openjarvis.security.file_policy import is_sensitive_file

    if is_sensitive_file(path):
        return None, f"Access denied: {path_str} is a sensitive file."
    if not path.exists():
        return None, f"File not found: {path_str}"
    return path, None


def _default_output(stem: str, suffix: str = ".pdf") -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"{stem}_{stamp}{suffix}"
    if Path("D:/").exists():
        return Path("D:/") / name
    docs = Path.home() / "Documents"
    docs.mkdir(parents=True, exist_ok=True)
    return docs / name


def _parse_paths_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    text = str(raw).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
        except json.JSONDecodeError:
            pass
    return [p.strip() for p in re.split(r"[;|]", text) if p.strip()]


def _parse_values(raw: Any) -> tuple[Optional[dict[str, str]], Optional[str]]:
    if raw is None:
        return None, "values is required for fill (JSON object field→value)."
    if isinstance(raw, dict):
        return {str(k): "" if v is None else str(v) for k, v in raw.items()}, None
    text = str(raw).strip()
    if not text:
        return None, "values is empty."
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"values must be JSON object: {exc}"
    if not isinstance(data, dict):
        return None, "values must be a JSON object (field name → value)."
    return {str(k): "" if v is None else str(v) for k, v in data.items()}, None


def _extract_pages(
    path: Path, pages_param: Optional[str], max_chars: int
) -> tuple[Optional[str], Optional[dict[str, Any]], Optional[str]]:
    try:
        import pdfplumber
    except ImportError:
        return (
            None,
            None,
            "pdfplumber package not installed. Install with: uv sync --extra pdf",
        )

    try:
        with pdfplumber.open(str(path)) as pdf:
            total_pages = len(pdf.pages)
            if pages_param:
                page_indices = _parse_pages(str(pages_param), total_pages)
            else:
                page_indices = list(range(total_pages))

            parts: list[str] = []
            for idx in page_indices:
                if 0 <= idx < total_pages:
                    page_text = pdf.pages[idx].extract_text() or ""
                    parts.append(f"--- page {idx + 1} ---\n{page_text}")

            text = "\n\n".join(parts)
            truncated = False
            if len(text) > max_chars:
                text = text[:max_chars] + "\n\n[Content truncated]"
                truncated = True

            meta = {
                "file_path": str(path.resolve()),
                "total_pages": total_pages,
                "pages_extracted": len(page_indices),
                "truncated": truncated,
            }
            return text, meta, None
    except Exception as exc:
        return None, None, f"PDF extraction error: {exc}"


def _configure_tesseract() -> Optional[str]:
    """Return error message if tesseract binary missing; else None."""
    import os

    try:
        import pytesseract
    except ImportError:
        return (
            "pytesseract not installed. Install with: uv sync --extra pdf"
        )

    # Common Windows install path (UB Mannheim installer).
    if Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe").is_file():
        pytesseract.pytesseract.tesseract_cmd = (
            r"C:\Program Files\Tesseract-OCR\tesseract.exe"
        )

    # Prefer user tessdata (may include rus downloaded outside Program Files).
    user_tess = Path.home() / ".openjarvis" / "tesseract" / "tessdata"
    prog_tess = Path(r"C:\Program Files\Tesseract-OCR\tessdata")
    if (user_tess / "rus.traineddata").is_file():
        # Windows builds treat TESSDATA_PREFIX as the tessdata dir itself.
        os.environ["TESSDATA_PREFIX"] = str(user_tess) + os.sep
    elif (prog_tess / "rus.traineddata").is_file() or (
        prog_tess / "eng.traineddata"
    ).is_file():
        os.environ["TESSDATA_PREFIX"] = str(prog_tess) + os.sep

    try:
        pytesseract.get_tesseract_version()
    except Exception:
        return (
            "Tesseract OCR not found. Install from "
            "https://github.com/UB-Mannheim/tesseract/wiki "
            "and include language pack 'rus' (Russian). "
            "Default path: C:\\Program Files\\Tesseract-OCR\\tesseract.exe "
            "or place rus.traineddata in "
            "%USERPROFILE%\\.openjarvis\\tesseract\\tessdata\\"
        )
    return None


@ToolRegistry.register("office_pdf")
class OfficePdfTool(BaseTool):
    """Personal-PC PDF tool: read, search, OCR, AcroForm, merge, split."""

    tool_id = "office_pdf"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="office_pdf",
            description=(
                "Работа с PDF на этом ПК: чтение текста, поиск, OCR сканов, "
                "список/заполнение AcroForm-полей, склейка и нарезка страниц. "
                "Когда использовать: прочитать/найти текст в PDF; OCR если "
                "текста нет (скан); заполнить форму с полями; склеить/вырезать. "
                "Когда НЕ использовать: просто открыть PDF в просмотрщике → "
                "open_path; Word → office_word; XFA/LiveCycle формы не "
                "поддерживаются. path можно опустить, если PDF уже в "
                "КОНТЕКСТЕ ФАЙЛОВ (последний открытый .pdf)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": (
                            "read | search | ocr | list_fields | fill | "
                            "merge | split"
                        ),
                    },
                    "path": {
                        "type": "string",
                        "description": "Path to PDF (alias: file_path).",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Alias for path.",
                    },
                    "pages": {
                        "type": "string",
                        "description": "Page range, e.g. '1-5' or '1,3,5'.",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Max characters for read/search/ocr.",
                    },
                    "query": {
                        "type": "string",
                        "description": "Search string for action=search.",
                    },
                    "values": {
                        "type": "string",
                        "description": (
                            "JSON object field→value for action=fill."
                        ),
                    },
                    "paths": {
                        "type": "string",
                        "description": (
                            "JSON array or ';' separated paths for merge."
                        ),
                    },
                    "output_path": {
                        "type": "string",
                        "description": "Output PDF path for fill/merge/split.",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": (
                            "Optional dir for split (one file per page)."
                        ),
                    },
                },
                "required": ["action"],
            },
            category="media",
            required_capabilities=["file:read"],
        )

    def execute(self, **params: Any) -> ToolResult:
        action = str(params.get("action") or "").strip().lower()
        if action not in _ACTIONS:
            return _fail(
                f"Unknown action {action!r}. Use: {', '.join(_ACTIONS)}"
            )

        if action == "merge":
            return self._merge(params)
        if action == "fill":
            return self._fill(params)
        if action == "split":
            return self._split(params)
        if action == "list_fields":
            return self._list_fields(params)
        if action == "ocr":
            return self._ocr(params)
        if action == "search":
            return self._search(params)
        return self._read(params)

    def _read(self, params: Any) -> ToolResult:
        path, err = _resolve_pdf_path(
            str(params.get("path") or params.get("file_path") or "")
        )
        if err or path is None:
            return _fail(err or "No path.")
        max_chars = int(params.get("max_chars") or _DEFAULT_MAX_CHARS)
        text, meta, err = _extract_pages(path, params.get("pages"), max_chars)
        if err:
            return _fail(err)
        _note_pdf(path)
        body = text or "No text content found in PDF (try action=ocr for scans)."
        return _ok(body, **(meta or {}))

    def _search(self, params: Any) -> ToolResult:
        query = str(params.get("query") or "").strip()
        if not query:
            return _fail("query is required for action=search.")
        path, err = _resolve_pdf_path(
            str(params.get("path") or params.get("file_path") or "")
        )
        if err or path is None:
            return _fail(err or "No path.")

        try:
            import pdfplumber
        except ImportError:
            return _fail(
                "pdfplumber package not installed. Install with: uv sync --extra pdf"
            )

        max_chars = int(params.get("max_chars") or _DEFAULT_MAX_CHARS)
        q_lower = query.lower()
        hits: list[str] = []
        try:
            with pdfplumber.open(str(path)) as pdf:
                total = len(pdf.pages)
                pages_param = params.get("pages")
                if pages_param:
                    indices = _parse_pages(str(pages_param), total)
                else:
                    indices = list(range(total))
                for idx in indices:
                    page_text = pdf.pages[idx].extract_text() or ""
                    if q_lower not in page_text.lower():
                        continue
                    # Collect matching lines with context.
                    for line in page_text.splitlines():
                        if q_lower in line.lower():
                            hits.append(f"p.{idx + 1}: {line.strip()}")
        except Exception as exc:
            return _fail(f"PDF search error: {exc}")

        _note_pdf(path)
        if not hits:
            return _ok(
                f"No matches for {query!r} in {path}. "
                "If this is a scan, use action=ocr first.",
                file_path=str(path.resolve()),
                match_count=0,
            )
        body = f"Matches for {query!r} ({len(hits)}):\n" + "\n".join(hits)
        if len(body) > max_chars:
            body = body[:max_chars] + "\n\n[Content truncated]"
        return _ok(
            body,
            file_path=str(path.resolve()),
            match_count=len(hits),
        )

    def _ocr(self, params: Any) -> ToolResult:
        path, err = _resolve_pdf_path(
            str(params.get("path") or params.get("file_path") or "")
        )
        if err or path is None:
            return _fail(err or "No path.")

        tess_err = _configure_tesseract()
        if tess_err:
            return _fail(tess_err)

        try:
            import pymupdf
            import pytesseract
            from PIL import Image
            import io
        except ImportError as exc:
            return _fail(
                f"OCR dependencies missing ({exc}). "
                "Install with: uv sync --extra pdf"
            )

        max_chars = int(params.get("max_chars") or _DEFAULT_MAX_CHARS)
        try:
            doc = pymupdf.open(str(path))
        except Exception as exc:
            return _fail(f"Cannot open PDF for OCR: {exc}")

        try:
            total = doc.page_count
            pages_param = params.get("pages")
            if pages_param:
                indices = _parse_pages(str(pages_param), total)
            else:
                indices = list(range(total))

            parts: list[str] = []
            for idx in indices:
                page = doc[idx]
                embedded = (page.get_text("text") or "").strip()
                if len(embedded) >= _OCR_EMPTY_THRESHOLD:
                    parts.append(
                        f"--- page {idx + 1} (text layer) ---\n{embedded}"
                    )
                    continue
                pix = page.get_pixmap(
                    matrix=pymupdf.Matrix(2, 2), alpha=False
                )
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                try:
                    ocr_text = pytesseract.image_to_string(
                        img, lang="rus+eng"
                    )
                except pytesseract.TesseractError as exc:
                    # Retry eng-only if rus pack missing.
                    msg = str(exc).lower()
                    if "rus" in msg or "language" in msg:
                        try:
                            ocr_text = pytesseract.image_to_string(
                                img, lang="eng"
                            )
                            ocr_text = (
                                "[Warning: tesseract language 'rus' missing; "
                                "used eng only]\n"
                                + ocr_text
                            )
                        except Exception as exc2:
                            return _fail(f"Tesseract OCR error: {exc2}")
                    else:
                        return _fail(f"Tesseract OCR error: {exc}")
                parts.append(
                    f"--- page {idx + 1} (ocr) ---\n{(ocr_text or '').strip()}"
                )
        finally:
            doc.close()

        text = "\n\n".join(parts)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n\n[Content truncated]"
        _note_pdf(path)
        return _ok(
            text or "OCR produced no text.",
            file_path=str(path.resolve()),
            pages_ocr=len(indices),
            total_pages=total,
        )

    def _list_fields(self, params: Any) -> ToolResult:
        path, err = _resolve_pdf_path(
            str(params.get("path") or params.get("file_path") or "")
        )
        if err or path is None:
            return _fail(err or "No path.")

        try:
            from pypdf import PdfReader
        except ImportError:
            return _fail(
                "pypdf package not installed. Install with: uv sync --extra pdf"
            )

        try:
            reader = PdfReader(str(path))
            fields = reader.get_fields()
        except Exception as exc:
            return _fail(f"Failed to read form fields: {exc}")

        _note_pdf(path)
        if not fields:
            return _ok(
                "No AcroForm fields in this PDF "
                "(not fillable / XFA not supported).",
                file_path=str(path.resolve()),
                field_count=0,
            )

        lines = [f"AcroForm fields ({len(fields)}) in {path}:"]
        for name, info in fields.items():
            if not isinstance(info, dict):
                lines.append(f"- {name}")
                continue
            value = info.get("/V", info.get("value", ""))
            ftype = info.get("/FT", info.get("field_type", ""))
            lines.append(f"- {name}: value={value!r} type={ftype}")
        return _ok(
            "\n".join(lines),
            file_path=str(path.resolve()),
            field_count=len(fields),
        )

    def _fill(self, params: Any) -> ToolResult:
        path, err = _resolve_pdf_path(
            str(params.get("path") or params.get("file_path") or "")
        )
        if err or path is None:
            return _fail(err or "No path.")

        values, verr = _parse_values(params.get("values"))
        if verr or values is None:
            return _fail(verr or "Invalid values.")

        out_raw = str(params.get("output_path") or "").strip()
        if out_raw:
            out_path = Path(out_raw)
        else:
            out_path = _default_output(path.stem + "_filled")
        if out_path.resolve() == path.resolve():
            return _fail(
                "output_path must be a new file — refusing to overwrite template."
            )
        out_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            from pypdf import PdfReader, PdfWriter
        except ImportError:
            return _fail(
                "pypdf package not installed. Install with: uv sync --extra pdf"
            )

        try:
            reader = PdfReader(str(path))
            fields = reader.get_fields() or {}
            if not fields:
                return _fail(
                    "No AcroForm fields — cannot fill "
                    "(XFA/LiveCycle and flat scans not supported)."
                )
            unknown = [k for k in values if k not in fields]
            writer = PdfWriter()
            writer.append(reader)
            # Apply values on each page that may host widgets.
            for page in writer.pages:
                try:
                    writer.update_page_form_field_values(
                        page, values, auto_regenerate=False
                    )
                except Exception:
                    # Some pages have no widgets; ignore and continue.
                    pass
            with open(out_path, "wb") as fh:
                writer.write(fh)
        except Exception as exc:
            return _fail(f"Fill failed: {exc}")

        _note_pdf(out_path)
        warn = ""
        if unknown:
            warn = f"\nWarning: unknown field names ignored: {', '.join(unknown)}"
        return _ok(
            f"Filled form saved: {out_path.resolve()}{warn}",
            file_path=str(out_path.resolve()),
            source=str(path.resolve()),
            fields_set=len(values) - len(unknown),
        )

    def _merge(self, params: Any) -> ToolResult:
        paths = _parse_paths_list(params.get("paths"))
        if len(paths) < 2:
            # Also accept path + paths combo poorly — require >=2.
            return _fail(
                "merge needs paths with at least 2 PDFs "
                "(JSON array or ';' separated)."
            )

        resolved: list[Path] = []
        for p in paths:
            path, err = _resolve_pdf_path(p)
            if err or path is None:
                return _fail(err or f"Bad path: {p}")
            resolved.append(path)

        out_raw = str(params.get("output_path") or "").strip()
        out_path = Path(out_raw) if out_raw else _default_output("merged")
        out_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            from pypdf import PdfWriter
        except ImportError:
            return _fail(
                "pypdf package not installed. Install with: uv sync --extra pdf"
            )

        try:
            writer = PdfWriter()
            for path in resolved:
                writer.append(str(path))
            with open(out_path, "wb") as fh:
                writer.write(fh)
        except Exception as exc:
            return _fail(f"Merge failed: {exc}")

        _note_pdf(out_path)
        return _ok(
            f"Merged {len(resolved)} PDFs → {out_path.resolve()}",
            file_path=str(out_path.resolve()),
            sources=[str(p.resolve()) for p in resolved],
        )

    def _split(self, params: Any) -> ToolResult:
        path, err = _resolve_pdf_path(
            str(params.get("path") or params.get("file_path") or "")
        )
        if err or path is None:
            return _fail(err or "No path.")

        try:
            from pypdf import PdfReader, PdfWriter
        except ImportError:
            return _fail(
                "pypdf package not installed. Install with: uv sync --extra pdf"
            )

        try:
            reader = PdfReader(str(path))
            total = len(reader.pages)
        except Exception as exc:
            return _fail(f"Cannot open PDF: {exc}")

        pages_param = params.get("pages")
        output_dir_raw = str(params.get("output_dir") or "").strip()

        # Per-page export when output_dir set and pages omitted.
        if output_dir_raw and not pages_param:
            out_dir = Path(output_dir_raw)
            out_dir.mkdir(parents=True, exist_ok=True)
            written: list[str] = []
            try:
                for i in range(total):
                    writer = PdfWriter()
                    writer.add_page(reader.pages[i])
                    dest = out_dir / f"{path.stem}_p{i + 1}.pdf"
                    with open(dest, "wb") as fh:
                        writer.write(fh)
                    written.append(str(dest.resolve()))
            except Exception as exc:
                return _fail(f"Split failed: {exc}")
            if written:
                _note_pdf(Path(written[0]))
            return _ok(
                f"Split into {len(written)} files in {out_dir.resolve()}:\n"
                + "\n".join(written),
                files=written,
            )

        if not pages_param:
            return _fail(
                "split needs pages (e.g. '1-2') or output_dir for all pages."
            )
        try:
            indices = _parse_pages(str(pages_param), total)
        except ValueError as exc:
            return _fail(f"Invalid pages: {exc}")
        if not indices:
            return _fail("No valid pages selected.")

        out_raw = str(params.get("output_path") or "").strip()
        out_path = (
            Path(out_raw)
            if out_raw
            else _default_output(f"{path.stem}_pages")
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            writer = PdfWriter()
            for idx in indices:
                writer.add_page(reader.pages[idx])
            with open(out_path, "wb") as fh:
                writer.write(fh)
        except Exception as exc:
            return _fail(f"Split failed: {exc}")

        _note_pdf(out_path)
        return _ok(
            f"Wrote pages {[i + 1 for i in indices]} → {out_path.resolve()}",
            file_path=str(out_path.resolve()),
            source=str(path.resolve()),
            pages=[i + 1 for i in indices],
        )


__all__ = ["OfficePdfTool", "_parse_pages"]
