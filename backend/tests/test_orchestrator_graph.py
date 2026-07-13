"""LangGraph Orchestrator 独立编排路径回归测试。"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from langgraph.graph import END, START, StateGraph

import app.agents.orchestrator as orchestrator_module
from app.agents.base import AgentResult
from app.agents.change_agent import ChangeAgent
from app.agents.orchestrator import GraphState, Orchestrator, OrchestratorState
from app.agents.rca_agent import RCAAgent
from app.config import get_config
from app.models.events import (
    AlertEvent,
    ApprovalStatus,
    ChangeEvent,
    SeverityLevel,
)
from app.models.incident import Incident, IncidentState


@pytest.fixture(autouse=True)
def disable_real_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """图测试始终使用 RCA 规则路径，不受本地 .env 开关影响。"""
    monkeypatch.setattr(get_config().llm, "enable_rca", False)


def _alert() -> AlertEvent:
    return AlertEvent(
        source="orchestrator-test",
        service="order-service",
        metric="cpu_usage_percent",
        value=95.0,
        threshold=80.0,
        operator=">",
        severity=SeverityLevel.HIGH,
        labels={"environment": "test", "tier": "critical"},
        annotations={"description": "CPU usage is high"},
    )


def _transition_targets(incident: Incident) -> list[str]:
    return [
        entry.details["to_state"]
        for entry in incident.timeline
        if "to_state" in entry.details
    ]


def test_build_graph_returns_compiled_graph() -> None:
    orchestrator = Orchestrator()

    graph = orchestrator.build_graph()

    assert graph is not None


@pytest.mark.asyncio
async def test_process_alert_graph_happy_path_resolves_simulated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def low_time_risk(self: ChangeAgent) -> float:
        return 0.2

    monkeypatch.setattr(ChangeAgent, "_calculate_time_risk", low_time_risk)
    orchestrator = Orchestrator()
    assert orchestrator.build_graph() is not None

    incident = await orchestrator.process_alert(_alert())

    assert incident.state == IncidentState.RESOLVED
    assert orchestrator.state == OrchestratorState.COMPLETED
    assert incident.context["resolution_mode"] == "simulated"
    assert incident.context["approval_status"] == "auto_approved"
    assert incident.heal_events[-1].dry_run is True
    assert "orchestrator.rca" in incident.context
    assert "orchestrator.heal" in incident.context
    assert "orchestrator.change" in incident.context
    assert "pipeline_failure" not in incident.context
    assert _transition_targets(incident) == [
        "acknowledged",
        "rca_in_progress",
        "rca_completed",
        "healing",
        "awaiting_approval",
        "resolved",
    ]


@pytest.mark.asyncio
async def test_process_alert_graph_high_time_risk_stops_awaiting_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def high_time_risk(self: ChangeAgent) -> float:
        return 0.9

    monkeypatch.setattr(ChangeAgent, "_calculate_time_risk", high_time_risk)
    orchestrator = Orchestrator()
    assert orchestrator.build_graph() is not None

    incident = await orchestrator.process_alert(_alert())

    assert incident.state == IncidentState.AWAITING_APPROVAL
    assert orchestrator.state == OrchestratorState.AWAITING_APPROVAL
    assert incident.context["approval_status"] == "pending"
    assert "resolution_mode" not in incident.context
    assert _transition_targets(incident)[-1] == "awaiting_approval"
    assert "resolved" not in _transition_targets(incident)


@pytest.mark.asyncio
async def test_process_alert_change_pending_stops_awaiting_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def pending_change(self: ChangeAgent, input_data: Any, context: Any) -> AgentResult:
        change_event = ChangeEvent(
            incident_id=input_data.incident_id,
            change_id="CHG-pending-001",
            change_type=input_data.change_type,
            approval_status=ApprovalStatus.PENDING,
            risk_score=0.65,
            risk_level="high",
            approvers=["oncall"],
            change_details={"dry_run_passed": True},
        )
        return AgentResult.success_result(
            agent_name="change_agent",
            output_data={
                "change_event": change_event.model_dump(),
                "risk_score": change_event.risk_score,
                "risk_level": change_event.risk_level,
                "approval_status": "pending",
                "approvers": change_event.approvers,
                "auto_decision": False,
            },
        )

    monkeypatch.setattr(ChangeAgent, "process", pending_change)
    orchestrator = Orchestrator()
    assert orchestrator.build_graph() is not None

    incident = await orchestrator.process_alert(_alert())

    # incident 是终态事实源；pending 不能被 process_alert 的收尾分支误升级。
    assert incident.state == IncidentState.AWAITING_APPROVAL
    assert orchestrator.state == OrchestratorState.AWAITING_APPROVAL
    assert incident.context["approval_status"] == "pending"
    assert "resolution_mode" not in incident.context
    assert "pipeline_failure" not in incident.context
    assert _transition_targets(incident)[-1] == "awaiting_approval"
    assert "resolved" not in _transition_targets(incident)
    assert "escalated" not in _transition_targets(incident)


@pytest.mark.asyncio
async def test_process_alert_agent_failure_escalates_with_pipeline_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failed_rca(self: RCAAgent, input_data: Any, context: Any) -> AgentResult:
        return AgentResult.failure_result(
            agent_name="rca_agent",
            error_message="RCA unavailable",
        )

    monkeypatch.setattr(RCAAgent, "process", failed_rca)
    orchestrator = Orchestrator()
    assert orchestrator.build_graph() is not None

    incident = await orchestrator.process_alert(_alert())

    assert incident.state == IncidentState.ESCALATED
    assert orchestrator.state == OrchestratorState.ESCALATED
    assert incident.context["pipeline_failure"] == {
        "stage": "rca",
        "error": "RCA unavailable",
    }
    assert incident.context["orchestrator.errors"] == ["rca: RCA unavailable"]
    assert "orchestrator.rca" in incident.context
    assert "orchestrator.heal" not in incident.context
    assert "orchestrator.change" not in incident.context
    assert _transition_targets(incident) == [
        "acknowledged",
        "rca_in_progress",
        "escalated",
    ]


@pytest.mark.asyncio
async def test_graph_state_typed_dict_merges_partial_node_updates() -> None:
    incident = Incident.from_alert(_alert())

    async def write_phase(state: GraphState) -> dict[str, Any]:
        return {"current_phase": "rca"}

    async def write_result(state: GraphState) -> dict[str, Any]:
        return {"agent_results": {"rca": {"root_cause": "traffic_spike"}}}

    async def write_approval(state: GraphState) -> dict[str, Any]:
        return {"approval_status": "pending"}

    workflow = StateGraph(GraphState)
    workflow.add_node("write_phase", write_phase)
    workflow.add_node("write_result", write_result)
    workflow.add_node("write_approval", write_approval)
    workflow.add_edge(START, "write_phase")
    workflow.add_edge("write_phase", "write_result")
    workflow.add_edge("write_result", "write_approval")
    workflow.add_edge("write_approval", END)
    graph = workflow.compile()
    initial_state: GraphState = {
        "incident": incident,
        "current_phase": "detection",
        "agent_results": {},
        "errors": [],
        "completed": False,
        "escalated": False,
        "approval_status": "",
    }

    result = await graph.ainvoke(initial_state)

    assert result["incident"] is incident
    assert result["current_phase"] == "rca"
    assert result["agent_results"] == {"rca": {"root_cause": "traffic_spike"}}
    assert result["approval_status"] == "pending"
    assert result["errors"] == []
    assert result["completed"] is False
    assert result["escalated"] is False


@pytest.mark.asyncio
async def test_process_alert_graph_exception_escalates_without_fallback() -> None:
    class ExplodingGraph:
        async def ainvoke(self, state: GraphState) -> GraphState:
            raise RuntimeError("controlled graph failure")

    orchestrator = Orchestrator()
    orchestrator._compiled_graph = ExplodingGraph()

    incident = await orchestrator.process_alert(_alert())

    assert incident.state == IncidentState.ESCALATED
    assert orchestrator.state == OrchestratorState.ESCALATED
    assert incident.context["pipeline_failure"] == {
        "stage": "graph",
        "error": "controlled graph failure",
    }
    assert _transition_targets(incident) == ["escalated"]


def test_orchestrator_has_no_invalid_healed_or_sequential_reference() -> None:
    source = inspect.getsource(orchestrator_module)

    assert "IncidentState.HEALED" not in source
    assert "_sequential_process" not in source
