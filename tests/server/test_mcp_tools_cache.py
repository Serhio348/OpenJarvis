"""Tests for _get_mcp_tools() caching in agent_manager_routes."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi", reason="fastapi required for server route tests")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeAppState:
    """Minimal app_state substitute with dynamic attributes."""

    pass


def _make_config(*, enabled: bool = True, servers_json: str = "[]") -> MagicMock:
    """Build a mock config with tools.mcp.enabled and tools.mcp.servers."""
    config = MagicMock()
    config.tools.mcp.enabled = enabled
    config.tools.mcp.servers = servers_json
    return config


def _make_tool_spec(name: str, description: str = "") -> MagicMock:
    spec = MagicMock()
    spec.name = name
    spec.description = description
    spec.parameters = {"type": "object", "properties": {}}
    return spec


def _make_adapter(name: str) -> MagicMock:
    adapter = MagicMock()
    adapter.spec = _make_tool_spec(name)
    return adapter


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@patch("openjarvis.core.config.load_config")
def test_returns_tools_from_mcp_server(mock_load_config: MagicMock):
    """With a mocked MCP server, discovered tools are returned."""
    from openjarvis.server.agent_manager_routes import _get_mcp_tools

    server_cfg = [{"name": "test-server", "url": "http://localhost:9999"}]
    mock_load_config.return_value = _make_config(
        servers_json=json.dumps(server_cfg),
    )

    mock_adapter = _make_adapter("get_weather")

    with (
        patch("openjarvis.mcp.transport.StreamableHTTPTransport"),
        patch("openjarvis.mcp.client.MCPClient"),
        patch("openjarvis.tools.mcp_adapter.MCPToolProvider") as MockProvider,
    ):
        MockProvider.return_value.discover.return_value = [mock_adapter]

        app_state = _FakeAppState()
        tools, adapters = _get_mcp_tools(app_state)

    assert len(tools) == 1
    assert tools[0]["function"]["name"] == "get_weather"
    assert "get_weather" in adapters


@patch("openjarvis.core.config.load_config")
def test_caches_successful_discovery(mock_load_config: MagicMock):
    """Second call returns cached result without re-discovering."""
    from openjarvis.server.agent_manager_routes import _get_mcp_tools

    server_cfg = [{"name": "test-server", "url": "http://localhost:9999"}]
    mock_load_config.return_value = _make_config(
        servers_json=json.dumps(server_cfg),
    )

    mock_adapter = _make_adapter("cached_tool")

    with (
        patch("openjarvis.mcp.transport.StreamableHTTPTransport"),
        patch("openjarvis.mcp.client.MCPClient"),
        patch("openjarvis.tools.mcp_adapter.MCPToolProvider") as MockProvider,
    ):
        MockProvider.return_value.discover.return_value = [mock_adapter]

        app_state = _FakeAppState()

        # First call discovers
        tools1, _ = _get_mcp_tools(app_state)
        assert len(tools1) == 1

        # Second call should use cache (discover not called again)
        discover_call_count = MockProvider.return_value.discover.call_count
        tools2, _ = _get_mcp_tools(app_state)
        assert len(tools2) == 1
        assert MockProvider.return_value.discover.call_count == discover_call_count


@patch("openjarvis.core.config.load_config")
def test_does_not_cache_empty_results(mock_load_config: MagicMock):
    """Failed/empty discovery is not cached so it can be retried."""
    from openjarvis.server.agent_manager_routes import _get_mcp_tools

    server_cfg = [{"name": "failing-server", "url": "http://localhost:9999"}]
    mock_load_config.return_value = _make_config(
        servers_json=json.dumps(server_cfg),
    )

    with (
        patch("openjarvis.mcp.transport.StreamableHTTPTransport"),
        patch("openjarvis.mcp.client.MCPClient") as MockClient,
        patch("openjarvis.tools.mcp_adapter.MCPToolProvider") as MockProvider,
    ):
        # First call: discovery returns empty
        MockProvider.return_value.discover.return_value = []
        app_state = _FakeAppState()

        tools1, _ = _get_mcp_tools(app_state)
        assert len(tools1) == 0
        MockClient.return_value.close.assert_called_once_with()
        assert getattr(app_state, "_mcp_clients", []) == []

        # Verify no cache was set (empty result)
        assert getattr(app_state, "_mcp_tools_cache", None) is None

        # Second call: discovery now returns something
        mock_adapter = _make_adapter("retry_tool")
        MockProvider.return_value.discover.return_value = [mock_adapter]

        tools2, _ = _get_mcp_tools(app_state)
        assert len(tools2) == 1
        assert tools2[0]["function"]["name"] == "retry_tool"


@patch("openjarvis.core.config.load_config")
def test_handles_config_load_failure(mock_load_config: MagicMock):
    """Config load failure returns empty, no crash."""
    from openjarvis.server.agent_manager_routes import _get_mcp_tools

    mock_load_config.side_effect = RuntimeError("config broken")

    app_state = _FakeAppState()
    tools, adapters = _get_mcp_tools(app_state)

    assert tools == []
    assert adapters == {}


@patch("openjarvis.core.config.load_config")
def test_uses_preloaded_full_system_pool(mock_load_config: MagicMock):
    """Server and scheduled paths reuse one unfiltered MCP discovery."""

    from openjarvis.server.agent_manager_routes import _get_mcp_tools

    adapter = _make_adapter("preloaded_tool")
    app_state = _FakeAppState()
    app_state.mcp_tools = [adapter]

    tools, adapters = _get_mcp_tools(app_state)

    mock_load_config.assert_not_called()
    assert tools[0]["function"]["name"] == "preloaded_tool"
    assert adapters == {"preloaded_tool": adapter}


@patch("openjarvis.core.config.load_config")
def test_preloaded_duplicate_names_are_first_wins(mock_load_config: MagicMock):
    """SSE and executor paths choose the same adapter on name collisions."""

    from openjarvis.server.agent_manager_routes import _get_mcp_tools

    first = _make_adapter("duplicate")
    second = _make_adapter("duplicate")
    app_state = _FakeAppState()
    app_state.mcp_tools = [first, second]

    tools, adapters = _get_mcp_tools(app_state)

    mock_load_config.assert_not_called()
    assert len(tools) == 1
    assert adapters == {"duplicate": first}
