"""人工审批恢复闭环测试。

历史缺陷:ChangeAgent 给出 pending 后流水线停在 AWAITING_APPROVAL,
全系统没有任何「人批准后继续」的接口,审批闭环缺最后一环。
本文件锁定新增的 POST /incidents/{id}/approve 与 /reject:
- approve → 补跑 Step 5 收尾(simulated → RESOLVED → 记忆归档)
- reject → 与流水线 rejected 语义对齐(ESCALATED)
- 非 awaiting_approval 状态调用一律 409,防止重复审批/任意态改终态
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.api.routes as routes
from app.main import app
from app.models.events import ApprovalStatus
from app.models.incident import Incident, IncidentState
from app.services.incident_service import get_incident_service
from tests.test_pipeline_state_truth import (
    ArchiveSpy,
    _change_success,
    _happy_results,
    _incident,
    _run_routes_pipeline,
    _transition_targets,
)


@pytest.fixture(autouse=True)
def isolate_shared_incident_store():
    service = get_incident_service()
    service._incidents.clear()
    yield
    service._incidents.clear()


async def _run_to_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Incident, ArchiveSpy]:
    """跑到审批挂起态并注册进共享存储(与 trigger 端点行为一致)。"""
    incident = _incident()
    get_incident_service()._incidents[incident.incident_id] = incident
    results = _happy_results(incident.incident_id)
    results["change"] = _change_success(incident.incident_id, "pending")
    _, archive, _, _ = await _run_routes_pipeline(monkeypatch, incident, results)
    assert incident.state == IncidentState.AWAITING_APPROVAL
    assert archive.calls == 0
    return incident, archive


@pytest.mark.asyncio
async def test_approve_resumes_pipeline_to_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incident, archive = await _run_to_pending(monkeypatch)

    response = TestClient(app).post(
        f"/api/v1/incidents/{incident.incident_id}/approve",
        json={"approver": "sre-alice", "comment": "risk reviewed"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == IncidentState.RESOLVED.value

    assert incident.state == IncidentState.RESOLVED
    assert incident.context["approval_status"] == "approved"
    assert incident.context["resolution_mode"] == "simulated"
    change_event = incident.change_events[-1]
    assert change_event.approval_status == ApprovalStatus.APPROVED
    assert change_event.change_details["approved_by"] == "sre-alice"
    # 批准后才发生记忆归档,且状态轨迹为 awaiting_approval → resolved
    assert archive.calls == 1
    targets = _transition_targets(incident)
    assert targets[-2:] == ["awaiting_approval", "resolved"]


@pytest.mark.asyncio
async def test_reject_escalates(monkeypatch: pytest.MonkeyPatch) -> None:
    incident, archive = await _run_to_pending(monkeypatch)

    response = TestClient(app).post(
        f"/api/v1/incidents/{incident.incident_id}/reject",
        json={"approver": "sre-bob", "comment": "blast radius too large"},
    )

    assert response.status_code == 200
    assert incident.state == IncidentState.ESCALATED
    assert incident.context["approval_status"] == "rejected"
    assert incident.context["pipeline_failure"]["stage"] == "change"
    assert "sre-bob" in incident.context["pipeline_failure"]["error"]
    change_event = incident.change_events[-1]
    assert change_event.approval_status == ApprovalStatus.REJECTED
    assert change_event.change_details["rejected_by"] == "sre-bob"
    # 拒绝不归档、不 RESOLVED
    assert archive.calls == 0
    assert "resolved" not in _transition_targets(incident)


@pytest.mark.asyncio
async def test_approve_twice_returns_409(monkeypatch: pytest.MonkeyPatch) -> None:
    """已批准(RESOLVED)的 incident 不能再次审批。"""
    incident, _ = await _run_to_pending(monkeypatch)
    client = TestClient(app)

    first = client.post(f"/api/v1/incidents/{incident.incident_id}/approve")
    assert first.status_code == 200

    second = client.post(f"/api/v1/incidents/{incident.incident_id}/approve")
    assert second.status_code == 409
    assert incident.state == IncidentState.RESOLVED


@pytest.mark.asyncio
async def test_approve_non_awaiting_state_returns_409(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """auto_approved 直接走完的 incident(RESOLVED)不可被人工审批。"""
    incident = _incident()
    get_incident_service()._incidents[incident.incident_id] = incident
    await _run_routes_pipeline(
        monkeypatch, incident, _happy_results(incident.incident_id)
    )
    assert incident.state == IncidentState.RESOLVED

    response = TestClient(app).post(
        f"/api/v1/incidents/{incident.incident_id}/reject"
    )
    assert response.status_code == 409
    assert incident.state == IncidentState.RESOLVED


def test_approve_unknown_incident_returns_404() -> None:
    response = TestClient(app).post("/api/v1/incidents/INC-nonexistent/approve")
    assert response.status_code == 404
