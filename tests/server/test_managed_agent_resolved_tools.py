"""SSE regression coverage for canonical managed-agent tool resolution."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")

from openjarvis.core.registry import ToolRegistry  # noqa: E402
from openjarvis.core.types import Role, ToolResult  # noqa: E402
from openjarvis.engine._stubs import StreamChunk  # noqa: E402
from openjarvis.tools._stubs import BaseTool, ToolSpec  # noqa: E402


class _StatefulConfiguredTool(BaseTool):
    """A tool whose result identifies the exact instance that executed."""

    tool_id = "stateful_probe"
    instances: list["_StatefulConfiguredTool"] = []

    def __init__(self) -> None:
        self.instance_id = len(self.instances) + 1
        self.calls = 0
        self.instances.append(self)

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="stateful_probe",
            description=f"configured-instance-{self.instance_id}",
            parameters={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        )

    def execute(self, **params) -> ToolResult:
        self.calls += 1
        return ToolResult(
            tool_name=self.spec.name,
            content=(
                f"instance={self.instance_id};calls={self.calls};"
                f"value={params['value']}"
            ),
        )


class _CollidingMCPTool(BaseTool):
    """An MCP-shaped collision that must lose to the configured native tool."""

    tool_id = "mcp_stateful_probe"

    def __init__(self) -> None:
        self.calls = 0

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="stateful_probe",
            description="mcp-collision",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params) -> ToolResult:
        self.calls += 1
        return ToolResult(tool_name=self.spec.name, content="wrong MCP instance")


class _ToolCallingEngine:
    """Advertise the toolkit, request one call, then observe its result."""

    def __init__(self) -> None:
        self.turns = 0
        self.advertised_specs: list[dict] = []
        self.observed_tool_result = ""

    async def stream_full(self, messages, *, model, **kwargs):
        self.turns += 1
        self.advertised_specs = list(kwargs.get("tools", []))

        if self.turns == 1:
            yield StreamChunk(
                tool_calls=[
                    {
                        "index": 0,
                        "id": "call-stateful-probe",
                        "type": "function",
                        "function": {
                            "name": "stateful_probe",
                            "arguments": json.dumps({"value": "sentinel"}),
                        },
                    }
                ],
                finish_reason="tool_calls",
            )
            return

        tool_messages = [message for message in messages if message.role is Role.TOOL]
        self.observed_tool_result = tool_messages[-1].content
        yield StreamChunk(content="complete")
        yield StreamChunk(finish_reason="stop")


class _FinalOnlyEngine:
    async def stream_full(self, messages, *, model, **kwargs):
        yield StreamChunk(content="complete")
        yield StreamChunk(finish_reason="stop")


@pytest.mark.asyncio
async def test_sse_advertises_and_executes_the_same_resolved_tool_instance() -> None:
    """The schema and dispatch map must come from one first-wins toolkit."""

    from openjarvis.server.agent_manager_routes import _stream_managed_agent

    _StatefulConfiguredTool.instances.clear()
    ToolRegistry.register_value("stateful_probe", _StatefulConfiguredTool)

    colliding_mcp = _CollidingMCPTool()
    mcp_spec = colliding_mcp.to_openai_function()
    app_state = SimpleNamespace(
        config=SimpleNamespace(memory_files=None, system_prompt=None),
        memory_backend=None,
        channel_backend=None,
        channel_bridge=None,
        _mcp_clients=[object()],
        _mcp_tools_cache=(
            [mcp_spec],
            {"stateful_probe": colliding_mcp},
        ),
    )
    manager = MagicMock()
    manager.list_messages.return_value = []
    engine = _ToolCallingEngine()
    custom_spec = {
        "type": "function",
        "function": {
            "name": "stateful_probe",
            "description": "custom configured schema",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        },
    }

    response = await _stream_managed_agent(
        manager=manager,
        agent_record={
            "id": "agent-stateful",
            "name": "Stateful Agent",
            "agent_type": "simple",
            "config": {
                "model": "test-model",
                "max_turns": 3,
                "tools": [custom_spec],
            },
        },
        user_content="Use the stateful probe",
        message_id="message-stateful",
        engine=engine,
        bus=None,
        app_state=app_state,
    )

    body_parts: list[str] = []
    async for part in response.body_iterator:
        body_parts.append(part.decode() if isinstance(part, bytes) else part)

    assert engine.turns == 2
    assert len(_StatefulConfiguredTool.instances) == 1
    configured_instance = _StatefulConfiguredTool.instances[0]
    assert configured_instance.calls == 1
    assert colliding_mcp.calls == 0

    advertised = [
        spec
        for spec in engine.advertised_specs
        if spec.get("function", {}).get("name") == "stateful_probe"
    ]
    assert len(advertised) == 1
    assert advertised[0] is custom_spec
    assert advertised[0]["function"]["description"] == "custom configured schema"
    assert engine.observed_tool_result == "instance=1;calls=1;value=sentinel"
    assert "data: [DONE]" in "".join(body_parts)


@pytest.mark.asyncio
async def test_sse_mcp_opt_out_skips_discovery(monkeypatch) -> None:
    """Opting out skips request-local discovery and hides MCP specs."""

    from openjarvis.server import agent_manager_routes as routes

    discovery = MagicMock(side_effect=AssertionError("MCP discovery must not run"))
    monkeypatch.setattr(routes, "_get_mcp_tools", discovery)
    manager = MagicMock()
    manager.list_messages.return_value = []
    app_state = SimpleNamespace(
        config=SimpleNamespace(memory_files=None, system_prompt=None),
        memory_backend=None,
        channel_backend=None,
        channel_bridge=None,
    )

    response = await routes._stream_managed_agent(
        manager=manager,
        agent_record={
            "id": "agent-no-mcp",
            "name": "No MCP",
            "agent_type": "simple",
            "config": {"model": "test-model", "mcp_tools": False},
        },
        user_content="Answer directly",
        message_id="message-no-mcp",
        engine=_FinalOnlyEngine(),
        bus=None,
        app_state=app_state,
    )

    async for _ in response.body_iterator:
        pass

    discovery.assert_not_called()
