"""
AIOps Agent Platform - Incident Service

故障服务，管理故障的 CRUD 和状态流转。
"""

from __future__ import annotations

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
    """

    def __init__(self) -> None:
        # TODO: 使用真实数据库
        self._incidents: dict[str, Incident] = {}

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
        return True


_shared_incident_service = IncidentService()


def get_incident_service() -> IncidentService:
    """获取进程内共享的故障服务实例。"""
    return _shared_incident_service
