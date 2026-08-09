"""Tests for office_pptx tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from openjarvis.tools.office_pptx import OfficePptxTool


@pytest.fixture
def tool() -> OfficePptxTool:
    return OfficePptxTool()


class TestOfficePptx:
    def test_spec(self, tool: OfficePptxTool) -> None:
        assert tool.spec.name == "office_pptx"

    def test_create_read_find_write(
        self, tool: OfficePptxTool, tmp_path: Path
    ) -> None:
        pytest.importorskip("pptx")
        src = tmp_path / "deck.pptx"
        r = tool.execute(
            action="create",
            path=str(src),
            title="Demo Deck",
            body='["Alpha item", "Beta Moscow"]',
        )
        assert r.success is True, r.content
        assert src.exists()

        r = tool.execute(action="read", path=str(src))
        assert r.success is True
        assert "Demo Deck" in r.content
        assert "Alpha item" in r.content

        r = tool.execute(action="find", path=str(src), query="moscow")
        assert r.success is True
        assert "Beta" in r.content

        out = tmp_path / "deck_edit.pptx"
        r = tool.execute(
            action="write",
            path=str(src),
            replacements='{"Beta Moscow":"Beta SPb"}',
            output_path=str(out),
        )
        assert r.success is True, r.content
        r = tool.execute(action="read", path=str(out))
        assert "Beta SPb" in r.content
        assert "Beta Moscow" not in r.content

    def test_refuse_overwrite(
        self, tool: OfficePptxTool, tmp_path: Path
    ) -> None:
        pytest.importorskip("pptx")
        src = tmp_path / "a.pptx"
        tool.execute(action="create", path=str(src), title="T", overwrite=True)
        r = tool.execute(
            action="write",
            path=str(src),
            replacements='{"T":"U"}',
            output_path=str(src),
        )
        assert r.success is False
        assert "overwrite" in r.content.lower()
