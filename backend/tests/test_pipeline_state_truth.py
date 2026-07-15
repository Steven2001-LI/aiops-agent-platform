"""验证 REST/webhook 管道的状态真实性与共享故障存储。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.agents.change_agent as change_agent_module
import app.agents.heal_agent as heal_agent_module
import app.agents.monitor_agent as monitor_agent_module
import app.agents.rca_agent as rca_agent_module
import app.api.routes as routes
import app.api.webhooks as webhooks
from app.agents.base import AgentResult
from app.main import app
from app.models.events import (
    AlertEvent,
    ApprovalStatus,
    ChangeEvent,
    HealEvent,
    RCAEvent,
    SeverityLevel,
)
from app.models.incident import Incident, IncidentState
from app.services.incident_service import get_incident_service


@pytest.fixture(autouse=True)
def isolate_shared_incident_store():
    """共享单例是业务要求，但各测试不应泄漏内存故障数据。"""
    service = get_incident_service()
    service._incidents.clear()
    yield
    service._incidents.clear()


class StubAgent:
    """在 execute 边界返回真实 AgentResult 的最小 Agent 桩。"""

    def __init__(
        self,
        name: str,
        result: AgentResult,
        calls: dict[str, int],
    ) -> None:
        self.name = name
        self.result = result
        self.calls = calls

    async def execute(self, input_data: Any, context: Any) -> AgentResult:
        self.calls[self.name] += 1
        return self.result


class ArchiveSpy:
    def __init__(self) -> None:
        self.calls = 0

    async def flow_wm_to_ltm(self, incident_id: str) -> int:
        self.calls += 1
        return 1


def _incident() -> Incident:
    alert = AlertEvent(
        source="test",
        service="order-service",
        metric="cpu_usage_percent",
        value=95.0,
        threshold=80.0,
        operator=">",
        severity=SeverityLevel.HIGH,
        labels={"environment": "test", "tier": "standard"},
        annotations={"description": "CPU usage is high"},
    )
    return Incident.from_alert(alert)


def _monitor_success() -> AgentResult:
    return AgentResult.success_result(
        agent_name="monitor_agent",
        output_data={
            "detection_result": {
                "is_anomaly": True,
                "score": 0.96,
                "confidence": 0.94,
                "algorithm_consensus": "strong",
                "algorithms_voted": ["3-sigma", "ewma"],
            },
        },
    )


def _rca_success(incident_id: str) -> AgentResult:
    rca_event = RCAEvent(
        incident_id=incident_id,
        root_cause="traffic_spike",
        confidence=0.91,
        impact_chain=["api-gateway", "order-service"],
        evidence={
            "alert_metric": "cpu_usage_percent",
            "alert_value": 95.0,
            "affected_services": ["order-service"],
        },
        recommended_actions=["scale_up_resources"],
    )
    return AgentResult.success_result(
        agent_name="rca_agent",
        output_data={
            "rca_event": rca_event.model_dump(),
            "root_cause": rca_event.root_cause,
            "confidence": rca_event.confidence,
            "impact_services_count": 1,
            "suggested_actions": rca_event.recommended_actions,
            "bayesian_results": [],
        },
    )


def _heal_success(incident_id: str, *, dry_run_passed: bool = True) -> AgentResult:
    heal_event = HealEvent(
        incident_id=incident_id,
        action="scale_up_resources",
        action_category="pb_high_cpu",
        level="L0",
        target_resource="order-service",
        dry_run=True,
        dry_run_result={
            "all_executable": dry_run_passed,
            "results": [],
            "playbook_matched": "pb_high_cpu",
            "blast_radius_info": {
                "blast_radius_ratio": 0.05,
                "affected_service_count": 1,
                "total_service_count": 8,
            },
        },
        status="pending",
        requires_approval=False,
    )
    return AgentResult.success_result(
        agent_name="heal_agent",
        output_data={
            "heal_event": heal_event.model_dump(),
            "heal_level": "L0",
            "blast_radius": {
                "blast_radius_ratio": 0.05,
                "affected_service_count": 1,
                "total_service_count": 8,
            },
            "playbook_matched": "pb_high_cpu",
            "actions_count": 1,
            "dry_run_passed": dry_run_passed,
        },
    )


def _change_success(incident_id: str, approval_status: str) -> AgentResult:
    change_event = ChangeEvent(
        incident_id=incident_id,
        change_id="CHG-test-001",
        change_type="auto_heal",
        approval_status=ApprovalStatus(approval_status),
        risk_score=0.29 if approval_status == "auto_approved" else 0.65,
        risk_level="low" if approval_status == "auto_approved" else "high",
        approvers=[] if approval_status == "auto_approved" else ["oncall"],
        change_details={"dry_run_passed": True},
    )
    return AgentResult.success_result(
        agent_name="change_agent",
        output_data={
            "change_event": change_event.model_dump(),
            "risk_score": change_event.risk_score,
            "risk_level": change_event.risk_level,
            "approval_status": approval_status,
            "approvers": change_event.approvers,
            "auto_decision": approval_status == "auto_approved",
        },
    )


def _happy_results(incident_id: str) -> dict[str, AgentResult]:
    return {
        "monitor": _monitor_success(),
        "rca": _rca_success(incident_id),
        "heal": _heal_success(incident_id),
        "change": _change_success(incident_id, "auto_approved"),
    }


async def _run_routes_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    incident: Incident,
    results: dict[str, AgentResult],
) -> tuple[dict[str, int], ArchiveSpy, list[dict[str, Any]], list[IncidentState]]:
    calls: dict[str, int] = defaultdict(int)
    archive = ArchiveSpy()
    memories: list[dict[str, Any]] = []
    broadcasts: list[IncidentState] = []

    monkeypatch.setattr(routes, "_get_monitor", lambda: StubAgent("monitor", results["monitor"], calls))
    monkeypatch.setattr(routes, "_get_rca", lambda: StubAgent("rca", results["rca"], calls))
    monkeypatch.setattr(routes, "_get_heal", lambda: StubAgent("heal", results["heal"], calls))
    monkeypatch.setattr(routes, "_get_change", lambda: StubAgent("change", results["change"], calls))
    monkeypatch.setattr(routes, "get_metric_data", lambda *args: [50.0, 51.0, 49.0])

    async def record_memory(**kwargs: Any) -> None:
        memories.append(kwargs)

    async def ignore_working_memory(**kwargs: Any) -> None:
        return None

    async def record_broadcast(current: Incident) -> None:
        broadcasts.append(current.state)

    async def get_archive() -> ArchiveSpy:
        return archive

    monkeypatch.setattr(routes, "_store_agent_memory", record_memory)
    monkeypatch.setattr(routes, "_store_working_memory", ignore_working_memory)
    monkeypatch.setattr(routes, "_broadcast_incident", record_broadcast)
    monkeypatch.setattr(routes, "_get_memory_system", get_archive)

    await routes._process_incident_pipeline(incident)
    return calls, archive, memories, broadcasts


def _transition_targets(incident: Incident) -> list[str]:
    return [
        entry.details["to_state"]
        for entry in incident.timeline
        if "to_state" in entry.details
    ]


@pytest.mark.asyncio
async def test_auto_approved_dry_run_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    incident = _incident()
    calls, archive, _, broadcasts = await _run_routes_pipeline(
        monkeypatch,
        incident,
        _happy_results(incident.incident_id),
    )

    assert incident.state == IncidentState.RESOLVED
    assert incident.context["resolution_mode"] == "simulated"
    assert calls == {"monitor": 1, "rca": 1, "heal": 1, "change": 1}
    assert archive.calls == 1
    assert broadcasts[-1] == IncidentState.RESOLVED
    assert _transition_targets(incident) == [
        "acknowledged",
        "rca_in_progress",
        "rca_completed",
        "healing",
        "awaiting_approval",
        "resolved",
    ]


@pytest.mark.asyncio
async def test_rca_failure_escalates_and_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    incident = _incident()
    results = _happy_results(incident.incident_id)
    results["rca"] = AgentResult.failure_result(
        agent_name="rca_agent",
        error_message="RCA unavailable",
    )

    calls, archive, _, _ = await _run_routes_pipeline(monkeypatch, incident, results)

    assert incident.state == IncidentState.ESCALATED
    assert incident.rca_event is None
    assert calls == {"monitor": 1, "rca": 1}
    assert archive.calls == 0
    assert _transition_targets(incident) == [
        "acknowledged",
        "rca_in_progress",
        "escalated",
    ]
    assert "rca_completed" not in _transition_targets(incident)


@pytest.mark.asyncio
async def test_change_pending_stops_awaiting_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    incident = _incident()
    results = _happy_results(incident.incident_id)
    results["change"] = _change_success(incident.incident_id, "pending")

    calls, archive, memories, broadcasts = await _run_routes_pipeline(monkeypatch, incident, results)

    assert incident.state == IncidentState.AWAITING_APPROVAL
    assert calls == {"monitor": 1, "rca": 1, "heal": 1, "change": 1}
    assert archive.calls == 0
    assert "resolution_mode" not in incident.context
    assert any(memory["agent_name"] == "change_agent" for memory in memories)
    assert broadcasts[-1] == IncidentState.AWAITING_APPROVAL
    assert _transition_targets(incident)[-1] == "awaiting_approval"
    assert "resolved" not in _transition_targets(incident)


@pytest.mark.asyncio
async def test_heal_failure_escalates_and_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    incident = _incident()
    results = _happy_results(incident.incident_id)
    results["heal"] = AgentResult.failure_result(
        agent_name="heal_agent",
        error_message="Heal unavailable",
    )

    calls, archive, _, _ = await _run_routes_pipeline(monkeypatch, incident, results)

    assert incident.state == IncidentState.ESCALATED
    assert calls == {"monitor": 1, "rca": 1, "heal": 1}
    assert archive.calls == 0
    assert _transition_targets(incident)[-2:] == ["healing", "escalated"]
    assert "resolved" not in _transition_targets(incident)


@pytest.mark.asyncio
async def test_monitor_failure_escalates_and_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    incident = _incident()
    results = _happy_results(incident.incident_id)
    results["monitor"] = AgentResult.failure_result(
        agent_name="monitor_agent",
        error_message="Monitor unavailable",
    )

    calls, archive, _, _ = await _run_routes_pipeline(monkeypatch, incident, results)

    assert incident.state == IncidentState.ESCALATED
    assert calls == {"monitor": 1}
    assert archive.calls == 0
    assert _transition_targets(incident) == ["acknowledged", "escalated"]


@pytest.mark.asyncio
async def test_change_failure_escalates_and_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    incident = _incident()
    results = _happy_results(incident.incident_id)
    results["change"] = AgentResult.failure_result(
        agent_name="change_agent",
        error_message="Change unavailable",
    )

    calls, archive, _, _ = await _run_routes_pipeline(monkeypatch, incident, results)

    assert incident.state == IncidentState.ESCALATED
    assert calls == {"monitor": 1, "rca": 1, "heal": 1, "change": 1}
    assert archive.calls == 0
    assert _transition_targets(incident)[-2:] == ["awaiting_approval", "escalated"]
    assert "resolved" not in _transition_targets(incident)


@pytest.mark.asyncio
async def test_failed_dry_run_escalates_and_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    incident = _incident()
    results = _happy_results(incident.incident_id)
    results["heal"] = _heal_success(incident.incident_id, dry_run_passed=False)

    calls, archive, _, _ = await _run_routes_pipeline(monkeypatch, incident, results)

    assert incident.state == IncidentState.ESCALATED
    assert calls == {"monitor": 1, "rca": 1, "heal": 1}
    assert archive.calls == 0
    assert incident.context["pipeline_failure"]["stage"] == "heal"
    assert _transition_targets(incident)[-1] == "escalated"


@pytest.mark.asyncio
async def test_rejected_change_escalates(monkeypatch: pytest.MonkeyPatch) -> None:
    incident = _incident()
    results = _happy_results(incident.incident_id)
    results["change"] = _change_success(incident.incident_id, "rejected")

    calls, archive, _, _ = await _run_routes_pipeline(monkeypatch, incident, results)

    assert incident.state == IncidentState.ESCALATED
    assert calls == {"monitor": 1, "rca": 1, "heal": 1, "change": 1}
    assert archive.calls == 0
    assert incident.context["approval_status"] == "rejected"
    assert _transition_targets(incident)[-1] == "escalated"
    assert "resolved" not in _transition_targets(incident)


def test_webhook_created_incident_is_visible_via_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    service = get_incident_service()
    original_incidents = dict(service._incidents)
    service._incidents.clear()

    async def no_op_pipeline(incident: Incident) -> None:
        return None

    monkeypatch.setattr(webhooks, "_run_agent_pipeline", no_op_pipeline)
    payload = {
        "version": "4",
        "groupKey": "test-shared-storage",
        "status": "firing",
        "receiver": "aiops-webhook",
        "alerts": [{
            "status": "firing",
            "labels": {
                "alertname": "HighCPUUsage",
                "severity": "warning",
                "instance": "order-service",
            },
            "annotations": {
                "service": "order-service",
                "metric": "cpu_usage_percent",
                "description": "Current value 95%",
            },
        }],
    }

    try:
        with TestClient(app) as client:
            webhook_response = client.post("/api/v1/webhooks/alertmanager", json=payload)
            assert webhook_response.status_code == 200
            incident_id = webhook_response.json()["incidents_created"][0]

            route_response = client.get(f"/api/v1/incidents/{incident_id}")

        assert route_response.status_code == 200
        assert route_response.json()["incident_id"] == incident_id
        stored = service._incidents[incident_id]
        assert stored.state == IncidentState.ACKNOWLEDGED
        assert _transition_targets(stored)[-1] == "acknowledged"
    finally:
        service._incidents.clear()
        service._incidents.update(original_incidents)


@pytest.mark.asyncio
async def test_webhook_pipeline_uses_execute_not_process(monkeypatch: pytest.MonkeyPatch) -> None:
    incident = _incident()
    results = _happy_results(incident.incident_id)
    execute_calls: dict[str, int] = defaultdict(int)
    process_calls: dict[str, int] = defaultdict(int)

    class BoundaryAgent:
        def __init__(self, name: str, result: AgentResult) -> None:
            self.name = name
            self.result = result

        async def execute(self, input_data: Any, context: Any) -> AgentResult:
            execute_calls[self.name] += 1
            return self.result

        async def process(self, input_data: Any, context: Any) -> AgentResult:
            process_calls[self.name] += 1
            raise AssertionError("webhook pipeline bypassed execute()")

    monkeypatch.setattr(
        monitor_agent_module,
        "MonitorAgent",
        lambda: BoundaryAgent("monitor", results["monitor"]),
    )
    monkeypatch.setattr(
        rca_agent_module,
        "RCAAgent",
        lambda: BoundaryAgent("rca", results["rca"]),
    )
    monkeypatch.setattr(
        heal_agent_module,
        "HealAgent",
        lambda: BoundaryAgent("heal", results["heal"]),
    )
    monkeypatch.setattr(
        change_agent_module,
        "ChangeAgent",
        lambda: BoundaryAgent("change", results["change"]),
    )

    async def no_op(*args: Any, **kwargs: Any) -> None:
        return None

    async def no_memory_system() -> None:
        return None

    monkeypatch.setattr(webhooks, "_store_memory", no_op)
    monkeypatch.setattr(webhooks, "_store_wm", no_op)
    monkeypatch.setattr(webhooks, "_broadcast_incident_state", no_op)
    monkeypatch.setattr(webhooks, "_get_ms", no_memory_system)

    await webhooks._run_agent_pipeline(incident)

    assert incident.state == IncidentState.RESOLVED
    assert incident.context["resolution_mode"] == "simulated"
    assert execute_calls == {"monitor": 1, "rca": 1, "heal": 1, "change": 1}
    assert process_calls == {}
    assert _transition_targets(incident) == [
        "analyzing",
        "rca_in_progress",
        "rca_completed",
        "healing",
        "awaiting_approval",
        "resolved",
    ]


# =============================================================================
# 候选根因 Top-K 经 API 摊平透出(_incident_to_dict 两条分支)
# =============================================================================


def test_incident_to_dict_exposes_candidate_causes() -> None:
    """rca_event 分支:candidate_causes 原样透出"""
    incident_id = "inc-cand-dict"
    incident = _incident()
    candidates = [
        {"cause": "traffic_spike", "score": 0.91},
        {"cause": "memory_leak", "score": 0.42},
    ]
    incident.rca_event = RCAEvent(
        incident_id=incident_id,
        root_cause="traffic_spike",
        confidence=0.91,
        candidate_causes=candidates,
    )

    data = routes._incident_to_dict(incident)
    assert data["rca"]["candidate_causes"] == candidates


def test_incident_to_dict_candidate_causes_context_fallback() -> None:
    """context 兜底分支:无 rca_event 时读 rca_candidate_causes,缺省空列表"""
    incident = _incident()
    incident.context["rca_root_cause"] = "traffic_spike"
    incident.context["rca_confidence"] = 0.8

    data = routes._incident_to_dict(incident)
    assert data["rca"]["candidate_causes"] == []

    incident.context["rca_candidate_causes"] = [{"cause": "traffic_spike", "score": 0.8}]
    data = routes._incident_to_dict(incident)
    assert data["rca"]["candidate_causes"] == [{"cause": "traffic_spike", "score": 0.8}]
