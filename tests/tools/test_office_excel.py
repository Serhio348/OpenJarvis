"""Tests for office_excel tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from openjarvis.tools.office_excel import OfficeExcelTool


@pytest.fixture
def tool() -> OfficeExcelTool:
    return OfficeExcelTool()


@pytest.fixture
def sample_xlsx(tmp_path: Path) -> Path:
    openpyxl = pytest.importorskip("openpyxl")
    path = tmp_path / "sample.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Data"
    ws["A1"] = "Name"
    ws["B1"] = "City"
    ws["A2"] = "Ivan"
    ws["B2"] = "Moscow"
    ws2 = wb.create_sheet("Other")
    ws2["A1"] = "hello"
    wb.save(path)
    wb.close()
    return path


class TestOfficeExcel:
    def test_spec(self, tool: OfficeExcelTool) -> None:
        assert tool.spec.name == "office_excel"
        assert "action" in tool.spec.parameters["required"]

    def test_unknown_action(self, tool: OfficeExcelTool) -> None:
        result = tool.execute(action="pivot")
        assert result.success is False

    def test_sheets_read_find_write(
        self, tool: OfficeExcelTool, sample_xlsx: Path, tmp_path: Path
    ) -> None:
        r = tool.execute(action="sheets", path=str(sample_xlsx))
        assert r.success is True
        assert "Data" in r.content
        assert "Other" in r.content

        r = tool.execute(action="read", path=str(sample_xlsx), sheet="Data")
        assert r.success is True
        assert "Ivan" in r.content
        assert "Moscow" in r.content

        r = tool.execute(
            action="find", path=str(sample_xlsx), query="moscow"
        )
        assert r.success is True
        assert "B2" in r.content

        out = tmp_path / "edited.xlsx"
        r = tool.execute(
            action="write",
            path=str(sample_xlsx),
            sheet="Data",
            values='{"B2":"SPb","C2":100}',
            output_path=str(out),
        )
        assert r.success is True, r.content
        assert out.exists()

        r = tool.execute(action="read", path=str(out), sheet="Data")
        assert "SPb" in r.content
        assert "100" in r.content

    def test_create(self, tool: OfficeExcelTool, tmp_path: Path) -> None:
        pytest.importorskip("openpyxl")
        out = tmp_path / "new.xlsx"
        r = tool.execute(
            action="create",
            path=str(out),
            sheet="Main",
            values='{"A1":"Title","B1":42}',
        )
        assert r.success is True, r.content
        assert out.exists()
        r = tool.execute(action="read", path=str(out))
        assert "Title" in r.content

    def test_refuse_overwrite_without_flag(
        self, tool: OfficeExcelTool, sample_xlsx: Path
    ) -> None:
        r = tool.execute(
            action="write",
            path=str(sample_xlsx),
            values='{"A1":"x"}',
            output_path=str(sample_xlsx),
        )
        assert r.success is False
        assert "overwrite" in r.content.lower()

    def test_xls_rejected(self, tool: OfficeExcelTool, tmp_path: Path) -> None:
        f = tmp_path / "old.xls"
        f.write_bytes(b"fake")
        r = tool.execute(action="read", path=str(f))
        assert r.success is False
        assert ".xls" in r.content
