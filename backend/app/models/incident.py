"""
AIOps Agent Platform - Incident Models

故障事件模型，定义故障状态机和完整的故障实例结构。
一个 Incident 包含从触发到解决的全生命周期数据。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_serializer

from app.models.events import (
    AlertEvent,
    AuditEvent,
    ChangeEvent,
    EventStatus,
    HealEvent,
    RCAEvent,
    SeverityLevel,
)


class IncidentPhase(str, Enum):
    """故障处理阶段"""
    DETECTION = "detection"        # 检测阶段
    TRIAGE = "triage"              # 分类/分级
    RCA = "rca"                    # 根因分析
    MITIGATION = "mitigation"      # 缓解措施
    RESOLUTION = "resolution"      # 修复解决
    POSTMORTEM = "postmortem"      # 复盘阶段
    CLOSED = "closed"              # 已关闭


class IncidentState(str, Enum):
    """故障状态机状态"""
    # 初始状态
    NEW = "new"                           # 新创建
    ACKNOWLEDGED = "acknowledged"         # 已确认

    # 分析状态
    ANALYZING = "analyzing"               # 分析中
    RCA_IN_PROGRESS = "rca_in_progress"   # 根因分析中
    RCA_COMPLETED = "rca_completed"       # 根因分析完成

    # 处理状态
    MITIGATING = "mitigating"             # 缓解中
    HEALING = "healing"                   # 自愈执行中
    AWAITING_APPROVAL = "awaiting_approval"  # 等待审批

    # 终态
    RESOLVED = "resolved"                 # 已解决
    ESCALATED = "escalated"               # 已升级（人工介入）
    CLOSED = "closed"                     # 已关闭
    CANCELLED = "cancelled"               # 已取消（误报）


class TimelineEntry(BaseModel):
    """故障时间线条目"""
    entry_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    phase: IncidentPhase = Field(default=IncidentPhase.DETECTION)
    state: IncidentState = Field(default=IncidentState.NEW)
    actor: str = Field(default="", description="操作者")
    action: str = Field(default="", description="操作描述")
    details: dict[str, Any] = Field(default_factory=dict)

    @field_serializer("timestamp")
    def serialize_timestamp(self, value: datetime) -> str:
        return value.isoformat()


class AgentExecutionRecord(BaseModel):
    """Agent 执行记录"""
    record_id: str = Field(default_factory=lambda: str(uuid4()))
    agent_name: str = Field(default="")
    agent_type: str = Field(default="")
    started_at: datetime | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)
    status: str = Field(default="pending")  # pending/running/success/failed/timeout
    input_data: dict[str, Any] = Field(default_factory=dict)
    output_data: dict[str, Any] = Field(default_factory=dict)
    error_message: str = Field(default="")
    execution_time_ms: int = Field(default=0)

    @property
    def is_success(self) -> bool:
        return self.status == "success"

    @property
    def duration_seconds(self) -> float:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return 0.0


class IncidentMetrics(BaseModel):
    """故障处理指标"""
    # 时间指标
    time_to_detect_seconds: float = Field(default=0.0, description="检测耗时")
    time_to_acknowledge_seconds: float = Field(default=0.0, description="确认耗时")
    time_to_rca_seconds: float = Field(default=0.0, description="根因分析耗时")
    time_to_mitigate_seconds: float = Field(default=0.0, description="缓解耗时")
    time_to_resolve_seconds: float = Field(default=0.0, description="解决耗时")
    total_handling_time_seconds: float = Field(default=0.0, description="总处理耗时")

    # 质量指标
    rca_confidence: float = Field(default=0.0, description="根因分析置信度")
    heal_success_rate: float = Field(default=0.0, description="自愈成功率")
    human_interventions: int = Field(default=0, description="人工介入次数")
    false_positive: bool = Field(default=False, description="是否为误报")


class Incident(BaseModel):
    """
    故障实例

    完整的故障记录，包含触发告警、处理历史、Agent 执行记录、
    时间线和最终处理结果。
    """
    # 基础信息
    incident_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="故障唯一ID",
    )
    title: str = Field(default="", description="故障标题")
    description: str = Field(default="", description="故障描述")
    status: EventStatus = Field(default=EventStatus.PENDING)
    state: IncidentState = Field(default=IncidentState.NEW)
    severity: SeverityLevel = Field(default=SeverityLevel.MEDIUM)

    # 关联信息
    service: str = Field(default="", description="受影响服务")
    environment: str = Field(default="", description="环境(prod/staging/dev)")
    owner: str = Field(default="", description="当前负责人")

    # 时间戳
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    acknowledged_at: datetime | None = Field(default=None)
    resolved_at: datetime | None = Field(default=None)
    closed_at: datetime | None = Field(default=None)

    # 关联事件
    alert_event: AlertEvent | None = Field(default=None)
    rca_event: RCAEvent | None = Field(default=None)
    heal_events: list[HealEvent] = Field(default_factory=list)
    change_events: list[ChangeEvent] = Field(default_factory=list)
    audit_events: list[AuditEvent] = Field(default_factory=list)

    # 处理历史
    timeline: list[TimelineEntry] = Field(default_factory=list)
    agent_executions: list[AgentExecutionRecord] = Field(default_factory=list)

    # 指标
    metrics: IncidentMetrics = Field(default_factory=IncidentMetrics)

    # 扩展数据
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="故障上下文数据",
    )
    tags: list[str] = Field(default_factory=list, description="标签")

    @field_serializer("created_at", "updated_at", "acknowledged_at", "resolved_at", "closed_at")
    def serialize_datetimes(self, value: datetime | None) -> str | None:
        if value is not None:
            return value.isoformat()
        return None

    def add_timeline_entry(
        self,
        phase: IncidentPhase,
        state: IncidentState,
        actor: str,
        action: str,
        details: dict[str, Any] | None = None,
    ) -> TimelineEntry:
        """添加时间线条目"""
        entry = TimelineEntry(
            phase=phase,
            state=state,
            actor=actor,
            action=action,
            details=details or {},
        )
        self.timeline.append(entry)
        self.updated_at = datetime.now(timezone.utc)
        return entry

    def add_agent_execution(self, record: AgentExecutionRecord) -> None:
        """添加 Agent 执行记录"""
        self.agent_executions.append(record)
        self.updated_at = datetime.now(timezone.utc)

    def transition_to(self, new_state: IncidentState, actor: str = "system") -> None:
        """
        状态转换

        Args:
            new_state: 目标状态
            actor: 操作者
        """
        old_state = self.state
        self.state = new_state
        self.updated_at = datetime.now(timezone.utc)

        # 记录时间线
        phase = self._infer_phase_from_state(new_state)
        self.add_timeline_entry(
            phase=phase,
            state=new_state,
            actor=actor,
            action=f"State transition: {old_state.value} -> {new_state.value}",
            details={"from_state": old_state.value, "to_state": new_state.value},
        )

    def _infer_phase_from_state(self, state: IncidentState) -> IncidentPhase:
        """从状态推断阶段"""
        phase_mapping: dict[IncidentState, IncidentPhase] = {
            IncidentState.NEW: IncidentPhase.DETECTION,
            IncidentState.ACKNOWLEDGED: IncidentPhase.TRIAGE,
            IncidentState.ANALYZING: IncidentPhase.RCA,
            IncidentState.RCA_IN_PROGRESS: IncidentPhase.RCA,
            IncidentState.RCA_COMPLETED: IncidentPhase.RCA,
            IncidentState.MITIGATING: IncidentPhase.MITIGATION,
            IncidentState.HEALING: IncidentPhase.MITIGATION,
            IncidentState.AWAITING_APPROVAL: IncidentPhase.MITIGATION,
            IncidentState.RESOLVED: IncidentPhase.RESOLUTION,
            IncidentState.ESCALATED: IncidentPhase.MITIGATION,
            IncidentState.CLOSED: IncidentPhase.POSTMORTEM,
            IncidentState.CANCELLED: IncidentPhase.CLOSED,
        }
        return phase_mapping.get(state, IncidentPhase.DETECTION)

    @property
    def is_active(self) -> bool:
        """故障是否仍在处理中"""
        return self.state not in (
            IncidentState.RESOLVED,
            IncidentState.CLOSED,
            IncidentState.CANCELLED,
        )

    @property
    def is_resolved(self) -> bool:
        """故障是否已解决"""
        return self.state in (IncidentState.RESOLVED, IncidentState.CLOSED)

    @property
    def duration_seconds(self) -> float:
        """故障持续时间(秒)"""
        end_time = self.resolved_at or datetime.now(timezone.utc)
        return (end_time - self.created_at).total_seconds()

    @classmethod
    def from_alert(cls, alert: AlertEvent) -> "Incident":
        """
        从告警事件创建故障实例

        Args:
            alert: 告警事件

        Returns:
            Incident: 新创建的故障实例
        """
        incident = cls(
            title=f"[{alert.severity.value.upper()}] {alert.service} - {alert.metric}",
            description=alert.annotations.get(
                "description",
                f"{alert.metric} {alert.operator} {alert.threshold} (current: {alert.value})",
            ),
            severity=alert.severity,
            service=alert.service,
            environment=alert.labels.get("environment", "unknown"),
            alert_event=alert,
            tags=[alert.metric, alert.service, alert.severity.value],
        )
        incident.add_timeline_entry(
            phase=IncidentPhase.DETECTION,
            state=IncidentState.NEW,
            actor="monitor_agent",
            action="Incident created from alert",
            details={"alert_signature": alert.alert_signature},
        )
        return incident
