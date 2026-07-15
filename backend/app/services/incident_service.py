"""
AIOps Agent Platform - Incident Service

故障服务，管理故障的 CRUD 和状态流转。
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

from app.models.events import AlertEvent, SeverityLevel
from app.models.incident import (
    AgentExecutionRecord,
    Incident,
    IncidentMetrics,
    IncidentState,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)


class IncidentService:
    """
    故障服务

    提供故障的创建、查询、更新和状态管理。

    存储模型:内存 dict 是读路径的主副本(过滤/分页均在内存完成),
    SQLite(与 eval_tools 共用 SQLITE_PATH 指向的库,单表 JSON 文档列)
    作为落盘副本 — 写入即持久、启动时经 load_all() 恢复,重启不再丢数据。
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._incidents: dict[str, Incident] = {}
        self._db_path_override = db_path

    @property
    def _db_path(self) -> str:
        """每次访问时解析,与 eval_tools 一致地尊重运行期 SQLITE_PATH 变化。"""
        return self._db_path_override or os.getenv("SQLITE_PATH", "./data/aiops.db")

    # ---- 持久化 ----

    def _connect(self) -> sqlite3.Connection:
        directory = os.path.dirname(self._db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        conn = sqlite3.connect(self._db_path, timeout=5)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS incidents ("
            "incident_id TEXT PRIMARY KEY, "
            "data TEXT NOT NULL, "
            "state TEXT, "
            "updated_at TEXT)"
        )
        return conn

    def _persist(self, incident: Incident) -> None:
        """best-effort 落盘:失败只记告警,内存副本仍是可用数据源。"""
        try:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO incidents "
                    "(incident_id, data, state, updated_at) VALUES (?, ?, ?, ?)",
                    (
                        incident.incident_id,
                        incident.model_dump_json(),
                        incident.state.value,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.warning(
                "Failed to persist incident",
                incident_id=incident.incident_id,
                error=str(e),
            )

    async def save(self, incident: Incident) -> None:
        """写入内存主副本并落盘。

        管道对已注册的 Incident 对象做就地变异,因此每个状态推进点
        (创建/挂起/收尾/升级/人工审批)都应调用一次 save 刷新落盘快照。
        """
        self._incidents[incident.incident_id] = incident
        self._persist(incident)

    async def load_all(self) -> int:
        """启动时从 SQLite 恢复历史故障到内存;损坏行跳过不阻塞启动。"""
        try:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT incident_id, data FROM incidents"
                ).fetchall()
            finally:
                conn.close()
        except Exception as e:
            logger.warning("Failed to load persisted incidents", error=str(e))
            return 0

        loaded = 0
        for incident_id, data in rows:
            try:
                self._incidents[incident_id] = Incident.model_validate_json(data)
                loaded += 1
            except Exception as e:
                logger.warning(
                    "Skipping corrupt persisted incident",
                    incident_id=incident_id,
                    error=str(e),
                )
        if loaded:
            logger.info("Persisted incidents restored", count=loaded)
        return loaded

    async def create_from_alert(self, alert: AlertEvent) -> Incident:
        """
        从告警创建故障

        Args:
            alert: 告警事件

        Returns:
            Incident: 创建的故障
        """
        incident = Incident.from_alert(alert)
        self._incidents[incident.incident_id] = incident
        self._persist(incident)

        logger.info(
            "Incident created",
            incident_id=incident.incident_id,
            service=incident.service,
            severity=incident.severity.value,
        )

        return incident

    async def get(self, incident_id: str) -> Incident | None:
        """
        获取故障

        Args:
            incident_id: 故障ID

        Returns:
            Incident | None: 故障或 None
        """
        return self._incidents.get(incident_id)

    def list_change_events(
        self,
        service: str,
        since: datetime,
        exclude_incident_id: str = "",
    ) -> list[dict[str, Any]]:
        """按服务查询平台自产的变更事件史(RCA 近期变更证据源)。

        读路径全在内存主副本(SQLite 仅为落盘副本,经 load_all 恢复,
        跨重启有效),与 list() 一致,不要改成查库。
        exclude_incident_id 用于排除当前正在分析的事故自身。
        """
        records: list[dict[str, Any]] = []
        for incident in self._incidents.values():
            if incident.service != service:
                continue
            if exclude_incident_id and incident.incident_id == exclude_incident_id:
                continue
            for ce in incident.change_events:
                if ce.timestamp < since:
                    continue
                records.append({
                    "type": ce.change_type or "auto_heal",
                    "time": ce.timestamp.isoformat(),
                    "change_id": ce.change_id,
                    "status": ce.approval_status.value,
                    "risk_level": ce.risk_level,
                    "description": ce.automated_decision_reason
                    or str(ce.change_details.get("heal_action", "")),
                    "source": "incident_history",
                })
        records.sort(key=lambda r: r["time"], reverse=True)
        return records

    async def update_state(
        self,
        incident_id: str,
        new_state: IncidentState,
        actor: str = "system",
    ) -> Incident | None:
        """
        更新故障状态

        Args:
            incident_id: 故障ID
            new_state: 新状态
            actor: 操作者

        Returns:
            Incident | None: 更新后的故障
        """
        incident = self._incidents.get(incident_id)
        if incident is None:
            return None

        incident.transition_to(new_state, actor)
        self._persist(incident)

        logger.info(
            "Incident state updated",
            incident_id=incident_id,
            new_state=new_state.value,
            actor=actor,
        )

        return incident

    async def add_agent_execution(
        self,
        incident_id: str,
        record: AgentExecutionRecord,
    ) -> bool:
        """
        添加 Agent 执行记录

        Args:
            incident_id: 故障ID
            record: 执行记录

        Returns:
            bool: 是否成功
        """
        incident = self._incidents.get(incident_id)
        if incident is None:
            return False

        incident.add_agent_execution(record)
        self._persist(incident)
        return True

    async def list(
        self,
        state: IncidentState | None = None,
        severity: SeverityLevel | str | None = None,
        service: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        """
        列出故障

        Args:
            state: 状态过滤
            severity: 严重级别过滤
            service: 服务过滤
            page: 页码
            page_size: 每页数量

        Returns:
            dict: 故障列表和分页信息
        """
        incidents = list(self._incidents.values())
        severity_value = severity.value if isinstance(severity, SeverityLevel) else severity

        if state:
            incidents = [i for i in incidents if i.state == state]
        if severity_value:
            incidents = [i for i in incidents if i.severity.value == severity_value]
        if service:
            incidents = [i for i in incidents if i.service == service]

        # 按时间倒序
        incidents.sort(key=lambda i: i.created_at, reverse=True)

        total = len(incidents)
        offset = (page - 1) * page_size
        items = incidents[offset : offset + page_size]

        return {
            "items": [i.model_dump() for i in items],
            "total": total,
            "page": page,
            "page_size": page_size,
            "filters": {
                "state": state.value if state else None,
                "severity": severity_value,
                "service": service,
            },
        }

    async def update_metrics(
        self,
        incident_id: str,
        metrics: IncidentMetrics,
    ) -> bool:
        """
        更新故障指标

        Args:
            incident_id: 故障ID
            metrics: 指标

        Returns:
            bool: 是否成功
        """
        incident = self._incidents.get(incident_id)
        if incident is None:
            return False

        incident.metrics = metrics
        self._persist(incident)
        return True


_shared_incident_service = IncidentService()


def get_incident_service() -> IncidentService:
    """获取进程内共享的故障服务实例。"""
    return _shared_incident_service
