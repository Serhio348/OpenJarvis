"""Tests for office_pdf tool (read/search/forms/merge/split)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openjarvis.tools.office_pdf import OfficePdfTool, _parse_pages


@pytest.fixture
def tool() -> OfficePdfTool:
    return OfficePdfTool()


class TestOfficePdfSpec:
    def test_spec(self, tool: OfficePdfTool) -> None:
        assert tool.spec.name == "office_pdf"
        assert "action" in tool.spec.parameters["required"]


class TestParsePages:
    def test_range(self) -> None:
        assert _parse_pages("1-3", 10) == [0, 1, 2]


class TestOfficePdfReadSearch:
    def test_unknown_action(self, tool: OfficePdfTool) -> None:
        result = tool.execute(action="explode")
        assert result.success is False
        assert "Unknown action" in result.content

    def test_read_not_found(self, tool: OfficePdfTool) -> None:
        result = tool.execute(action="read", path="C:/no/such/file.pdf")
        assert result.success is False
        assert "File not found" in result.content

    def test_search_requires_query(
        self, tool: OfficePdfTool, tmp_path: Path
    ) -> None:
        f = tmp_path / "a.pdf"
        f.write_bytes(b"%PDF-1.4")
        result = tool.execute(action="search", path=str(f))
        assert result.success is False
        assert "query" in result.content

    def test_read_success(
        self, tool: OfficePdfTool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4 fake")

        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Hello PDF"
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdfplumber = MagicMock()
        mock_pdfplumber.open.return_value = mock_pdf
        monkeypatch.setitem(__import__("sys").modules, "pdfplumber", mock_pdfplumber)

        result = tool.execute(action="read", path=str(f))
        assert result.success is True
        assert "Hello PDF" in result.content


class TestOfficePdfMergeSplit:
    def test_merge_needs_two(self, tool: OfficePdfTool) -> None:
        result = tool.execute(action="merge", paths='["a.pdf"]')
        assert result.success is False
        assert "at least 2" in result.content

    def test_merge_and_split_real(
        self, tool: OfficePdfTool, tmp_path: Path
    ) -> None:
        pytest.importorskip("pypdf")
        from pypdf import PdfReader, PdfWriter

        a = tmp_path / "a.pdf"
        b = tmp_path / "b.pdf"
        w = PdfWriter()
        w.add_blank_page(width=200, height=200)
        with open(a, "wb") as fh:
            w.write(fh)
        w2 = PdfWriter()
        w2.add_blank_page(width=200, height=200)
        w2.add_blank_page(width=200, height=200)
        with open(b, "wb") as fh:
            w2.write(fh)

        merged = tmp_path / "merged.pdf"
        paths_json = f'["{a.as_posix()}", "{b.as_posix()}"]'
        result = tool.execute(
            action="merge",
            paths=paths_json,
            output_path=str(merged),
        )
        assert result.success is True, result.content
        assert merged.exists()
        assert len(PdfReader(str(merged)).pages) == 3

        out = tmp_path / "part.pdf"
        result2 = tool.execute(
            action="split",
            path=str(merged),
            pages="1-2",
            output_path=str(out),
        )
        assert result2.success is True, result2.content
        assert len(PdfReader(str(out)).pages) == 2


class TestOfficePdfForms:
    def test_list_fields_empty(
        self, tool: OfficePdfTool, tmp_path: Path
    ) -> None:
        pytest.importorskip("pypdf")
        from pypdf import PdfWriter

        f = tmp_path / "blank.pdf"
        w = PdfWriter()
        w.add_blank_page(width=200, height=200)
        with open(f, "wb") as fh:
            w.write(fh)

        result = tool.execute(action="list_fields", path=str(f))
        assert result.success is True
        assert "No AcroForm" in result.content


class TestOfficePdfStamp:
    def test_stamp_text_and_image(
        self, tool: OfficePdfTool, tmp_path: Path
    ) -> None:
        pymupdf = pytest.importorskip("pymupdf")
        from PIL import Image

        src = tmp_path / "blank.pdf"
        doc = pymupdf.open()
        doc.new_page(width=400, height=300)
        doc.save(src)
        doc.close()

        png = tmp_path / "mark.png"
        Image.new("RGB", (40, 20), color=(255, 0, 0)).save(png)

        out = tmp_path / "stamped.pdf"
        items = (
            '[{"page":1,"x":50,"y":250,"text":"Hello stamp","fontsize":14},'
            f'{{"page":1,"x":50,"y":40,"width":80,"height":30,'
            f'"image":"{png.as_posix()}"}}]'
        )
        result = tool.execute(
            action="stamp",
            path=str(src),
            items=items,
            output_path=str(out),
        )
        assert result.success is True, result.content
        assert out.exists()
        assert out.stat().st_size > src.stat().st_size

        # Refuse overwrite
        bad = tool.execute(
            action="stamp",
            path=str(src),
            text="x",
            x=10,
            y=10,
            output_path=str(src),
        )
        assert bad.success is False
