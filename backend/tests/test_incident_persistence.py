"""Incident SQLite 持久化测试。

历史缺陷:IncidentService 只有进程内 dict(源码 TODO 自述"使用真实数据库"),
重启后所有故障(含挂起待审批的)全部丢失。
本文件锁定修复后的行为:save 落盘 SQLite、load_all 启动恢复、
同 ID 覆盖写、损坏行跳过、落盘失败不影响内存主副本。
"""

from __future__ import annotations

import sqlite3

import pytest

from app.models.events import AlertEvent, SeverityLevel
from app.models.incident import Incident, IncidentState
from app.services.incident_service import IncidentService


def _make_incident() -> Incident:
    alert = AlertEvent(
        source="test",
        service="order-service",
        metric="cpu_usage_percent",
        value=95.0,
        threshold=80.0,
        operator=">",
        severity=SeverityLevel.HIGH,
        labels={"environment": "test"},
        annotations={"description": "CPU usage is high"},
    )
    return Incident.from_alert(alert)


@pytest.mark.asyncio
async def test_save_load_roundtrip(tmp_path) -> None:
    """save 后用新实例 load_all,状态/告警/上下文/时间线完整恢复。"""
    db = str(tmp_path / "incidents.db")
    service = IncidentService(db_path=db)
    incident = _make_incident()
    incident.transition_to(IncidentState.AWAITING_APPROVAL, actor="change_agent")
    incident.context["rca_root_cause"] = "traffic_spike"
    await service.save(incident)

    restarted = IncidentService(db_path=db)
    assert await restarted.load_all() == 1
    restored = await restarted.get(incident.incident_id)
    assert restored is not None
    assert restored.state == IncidentState.AWAITING_APPROVAL
    assert restored.service == "order-service"
    assert restored.alert_event.metric == "cpu_usage_percent"
    assert restored.context["rca_root_cause"] == "traffic_spike"
    assert len(restored.timeline) == len(incident.timeline)
    assert restored.created_at == incident.created_at


@pytest.mark.asyncio
async def test_repeated_save_overwrites_not_duplicates(tmp_path) -> None:
    """同一 incident 多次 save 是覆盖写,恢复后是最新状态且只有一行。"""
    db = str(tmp_path / "incidents.db")
    service = IncidentService(db_path=db)
    incident = _make_incident()
    await service.save(incident)
    incident.transition_to(IncidentState.RESOLVED, actor="test")
    await service.save(incident)

    restarted = IncidentService(db_path=db)
    assert await restarted.load_all() == 1
    restored = await restarted.get(incident.incident_id)
    assert restored is not None
    assert restored.state == IncidentState.RESOLVED


@pytest.mark.asyncio
async def test_load_all_skips_corrupt_rows(tmp_path) -> None:
    """损坏行只跳过并告警,不阻塞其余数据恢复。"""
    db = str(tmp_path / "incidents.db")
    service = IncidentService(db_path=db)
    good = _make_incident()
    await service.save(good)

    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO incidents (incident_id, data, state, updated_at) "
        "VALUES (?, ?, ?, ?)",
        ("INC-corrupt", "{not valid json", "new", ""),
    )
    conn.commit()
    conn.close()

    restarted = IncidentService(db_path=db)
    assert await restarted.load_all() == 1
    assert await restarted.get(good.incident_id) is not None
    assert await restarted.get("INC-corrupt") is None


@pytest.mark.asyncio
async def test_persist_failure_is_non_fatal(tmp_path) -> None:
    """落盘失败(如路径不可写)不抛异常,内存主副本仍可用。"""
    service = IncidentService(db_path=str(tmp_path))  # 目录路径,sqlite 打不开
    incident = _make_incident()
    await service.save(incident)
    assert await service.get(incident.incident_id) is incident


@pytest.mark.asyncio
async def test_load_all_missing_db_returns_zero(tmp_path) -> None:
    """全新环境(库文件不存在)load_all 返回 0 且不报错。"""
    service = IncidentService(db_path=str(tmp_path / "fresh.db"))
    assert await service.load_all() == 0
    assert service._incidents == {}
