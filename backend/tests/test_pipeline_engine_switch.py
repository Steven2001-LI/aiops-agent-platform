"""管道引擎开关(APP_PIPELINE_ENGINE)与 LangGraph 接线测试。

历史缺陷:LangGraph 编排器启动时构建了图,但执行入口 process_alert
全仓库没有任何调用方——真实请求只走硬编码顺序流水线,"接线"缺失。
本文件锁定修复后的行为:
- config.pipeline_engine 默认 legacy,langgraph 时 trigger 走编排器
- 图路径复用已注册的 Incident(同一对象),终态与 legacy 语义一致
- 状态更新回调(WebSocket 广播注入点)逐阶段触发
- LangGraph 不可用时回落 legacy,不把告警一律升级
"""

from __future__ import annotations

import pytest

import app.api.routes as routes
from app.agents.change_agent import ChangeAgent
from app.agents.orchestrator import Orchestrator
from app.config import get_config
from app.models.incident import Incident, IncidentState
from app.services.incident_service import get_incident_service
from tests.test_pipeline_state_truth import _incident


@pytest.fixture(autouse=True)
def isolate_shared_incident_store():
    service = get_incident_service()
    service._incidents.clear()
    yield
    service._incidents.clear()


@pytest.fixture(autouse=True)
def disable_real_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """走 RCA 规则路径,不受本地 .env 开关影响。"""
    monkeypatch.setattr(get_config().llm, "enable_rca", False)


def test_pipeline_engine_defaults_to_legacy() -> None:
    assert get_config().pipeline_engine == "legacy"


@pytest.mark.asyncio
async def test_dispatch_routes_by_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """_dispatch_pipeline 按配置分派到对应引擎。"""
    dispatched: list[str] = []

    async def fake_legacy(incident: Incident) -> None:
        dispatched.append("legacy")

    async def fake_langgraph(incident: Incident) -> None:
        dispatched.append("langgraph")

    monkeypatch.setattr(routes, "_process_incident_pipeline", fake_legacy)
    monkeypatch.setattr(routes, "_run_langgraph_pipeline", fake_langgraph)

    import asyncio

    cfg = get_config()
    monkeypatch.setattr(cfg, "pipeline_engine", "legacy")
    routes._dispatch_pipeline(_incident(), cfg)
    monkeypatch.setattr(cfg, "pipeline_engine", "langgraph")
    routes._dispatch_pipeline(_incident(), cfg)
    await asyncio.sleep(0)  # 让 create_task 的两个任务执行

    assert dispatched == ["legacy", "langgraph"]


@pytest.mark.asyncio
async def test_langgraph_pipeline_resolves_registered_incident(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """图引擎处理的是已注册的同一个 Incident,终态与 legacy 语义一致。"""

    def low_time_risk(self: ChangeAgent) -> float:
        return 0.2

    monkeypatch.setattr(ChangeAgent, "_calculate_time_risk", low_time_risk)

    broadcasts: list[IncidentState] = []

    async def record_broadcast(current: Incident) -> None:
        broadcasts.append(current.state)

    monkeypatch.setattr(routes, "_broadcast_incident", record_broadcast)

    incident = _incident()
    incident.transition_to(IncidentState.ACKNOWLEDGED, actor="orchestrator")
    service = get_incident_service()
    service._incidents[incident.incident_id] = incident

    await routes._run_langgraph_pipeline(incident)

    # 故障存储里的对象就是图里跑完的对象
    stored = await service.get(incident.incident_id)
    assert stored is incident
    assert incident.state == IncidentState.RESOLVED
    assert incident.context["resolution_mode"] == "simulated"
    assert incident.context["approval_status"] == "auto_approved"
    assert incident.heal_events[-1].dry_run is True
    # 状态更新回调逐阶段触发,终态为 resolved
    assert len(broadcasts) >= 3
    assert broadcasts[-1] == IncidentState.RESOLVED
    # 已 ACK 的注入 incident 不产生重复 acknowledged 时间线
    ack_count = sum(
        1
        for entry in incident.timeline
        if entry.details.get("to_state") == "acknowledged"
    )
    assert ack_count == 1


@pytest.mark.asyncio
async def test_langgraph_unavailable_falls_back_to_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """图构建失败时回落 legacy 管道,而不是把告警一律升级。"""
    legacy_calls: list[str] = []

    async def fake_legacy(incident: Incident) -> None:
        legacy_calls.append(incident.incident_id)

    monkeypatch.setattr(routes, "_process_incident_pipeline", fake_legacy)
    monkeypatch.setattr(Orchestrator, "build_graph", lambda self: None)

    incident = _incident()
    await routes._run_langgraph_pipeline(incident)

    assert legacy_calls == [incident.incident_id]
    assert incident.state != IncidentState.ESCALATED


@pytest.mark.asyncio
async def test_process_alert_without_incident_keeps_old_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不传 incident 时 process_alert 自建对象,原有独立路径行为不变。"""

    def low_time_risk(self: ChangeAgent) -> float:
        return 0.2

    monkeypatch.setattr(ChangeAgent, "_calculate_time_risk", low_time_risk)
    orchestrator = Orchestrator()
    assert orchestrator.build_graph() is not None

    incident = await orchestrator.process_alert(_incident().alert_event)

    assert incident.state == IncidentState.RESOLVED
    targets = [
        e.details["to_state"] for e in incident.timeline if "to_state" in e.details
    ]
    assert targets[0] == "acknowledged"
