"""
AIOps Agent Platform - Event Data Models

定义系统中所有事件的数据模型，用于 Agent 间通信和数据传递。
所有事件均继承自 BaseEvent，包含统一的元数据字段。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_serializer


class SeverityLevel(str, Enum):
    """告警严重级别"""
    CRITICAL = "critical"   # P0 - 核心业务中断
    HIGH = "high"          # P1 - 主要功能受损
    MEDIUM = "medium"      # P2 - 部分功能异常
    LOW = "low"            # P3 - 轻微影响
    INFO = "info"          # P4 - 提示信息


class EventStatus(str, Enum):
    """事件处理状态"""
    PENDING = "pending"           # 待处理
    PROCESSING = "processing"     # 处理中
    ESCALATED = "escalated"       # 已升级
    RESOLVED = "resolved"         # 已解决
    CLOSED = "closed"             # 已关闭
    TIMEOUT = "timeout"           # 超时


class ApprovalStatus(str, Enum):
    """审批状态"""
    PENDING = "pending"       # 待审批
    APPROVED = "approved"     # 已批准
    REJECTED = "rejected"     # 已拒绝
    AUTO_APPROVED = "auto_approved"  # 自动批准


class BaseEvent(BaseModel):
    """
    事件基础模型

    所有事件类型的基类，提供统一的标识、时间戳和版本控制。
    """
    event_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="全局唯一事件ID",
    )
    correlation_id: str = Field(
        default="",
        description="关联ID，用于追踪同一故障链路中的所有事件",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="事件生成时间(UTC)",
    )
    schema_version: str = Field(
        default="1.0.0",
        description="事件模式版本号",
    )
    event_type: str = Field(
        default="base",
        description="事件类型标识",
    )
    source: str = Field(
        default="",
        description="事件来源(服务/组件名称)",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="扩展元数据",
    )

    @field_serializer("timestamp")
    def serialize_timestamp(self, value: datetime) -> str:
        """将时间戳序列化为 ISO 8601 格式"""
        return value.isoformat()

    def model_post_init(self, __context: Any) -> None:
        """初始化后处理：若未设置 correlation_id，则使用 event_id"""
        if not self.correlation_id:
            self.correlation_id = self.event_id


class AlertEvent(BaseEvent):
    """
    监控告警事件

    由监控系统(如 Prometheus/Grafana)触发的告警事件，
    是整个 AIOps 处理流程的入口事件。
    """
    event_type: str = "alert"
    service: str = Field(
        default="",
        description="告警所属服务名称",
    )
    metric: str = Field(
        default="",
        description="触发告警的指标名称",
    )
    value: float = Field(
        default=0.0,
        description="指标当前值",
    )
    threshold: float = Field(
        default=0.0,
        description="告警阈值",
    )
    operator: str = Field(
        default=">",
        description="比较运算符(>, <, ==, >=, <=)",
    )
    severity: SeverityLevel = Field(
        default=SeverityLevel.MEDIUM,
        description="告警严重级别",
    )
    duration_seconds: int = Field(
        default=0,
        description="告警持续时间(秒)",
    )
    labels: dict[str, str] = Field(
        default_factory=dict,
        description="告警标签(如 {cluster: prod, namespace: default})",
    )
    annotations: dict[str, str] = Field(
        default_factory=dict,
        description="告警注释(如 {summary: ..., description: ...})",
    )

    @property
    def is_critical(self) -> bool:
        """是否为关键告警"""
        return self.severity == SeverityLevel.CRITICAL

    @property
    def alert_signature(self) -> str:
        """生成告警签名，用于去重和聚类"""
        return f"{self.service}:{self.metric}:{self.severity.value}"


class RCAEvent(BaseEvent):
    """
    根因分析事件

    由 RCA Agent 生成，包含对故障根因的分析结果。
    """
    event_type: str = "rca"
    incident_id: str = Field(
        default="",
        description="关联的故障ID",
    )
    root_cause: str = Field(
        default="",
        description="根因描述",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="根因置信度(0-1)",
    )
    impact_chain: list[str] = Field(
        default_factory=list,
        description="影响链路(从根因到表象的因果关系链)",
    )
    contributing_factors: list[str] = Field(
        default_factory=list,
        description=" contributing factors（ Contributing factors 列表）",
    )
    evidence: dict[str, Any] = Field(
        default_factory=dict,
        description="支撑证据(指标数据、日志片段等)",
    )
    recommended_actions: list[str] = Field(
        default_factory=list,
        description="推荐操作",
    )
    time_range_start: datetime | None = Field(
        default=None,
        description="分析时间范围起始",
    )
    time_range_end: datetime | None = Field(
        default=None,
        description="分析时间范围结束",
    )

    @property
    def is_confident(self) -> bool:
        """置信度是否达到可接受水平"""
        from app.config import get_config
        return self.confidence >= get_config().agent.rca_min_confidence


class HealEvent(BaseEvent):
    """
    故障自愈事件

    由 Heal Agent 生成，记录自愈操作的执行过程和结果。
    """
    event_type: str = "heal"
    incident_id: str = Field(
        default="",
        description="关联的故障ID",
    )
    action: str = Field(
        default="",
        description="执行的自愈操作",
    )
    action_category: str = Field(
        default="",
        description="操作类别(restart/rollback/scale/patch/etc)",
    )
    level: str = Field(
        default="",
        description="操作级别(l1_l2_automation / l3_collaborative / l4_intelligent)",
    )
    target_resource: str = Field(
        default="",
        description="操作目标资源",
    )
    dry_run: bool = Field(
        default=True,
        description="是否为模拟执行",
    )
    dry_run_result: dict[str, Any] = Field(
        default_factory=dict,
        description="模拟执行结果",
    )
    execution_result: dict[str, Any] = Field(
        default_factory=dict,
        description="实际执行结果",
    )
    status: Literal["pending", "executing", "success", "failed", "rolled_back"] = Field(
        default="pending",
        description="执行状态",
    )
    execution_logs: list[str] = Field(
        default_factory=list,
        description="执行日志",
    )
    requires_approval: bool = Field(
        default=True,
        description="是否需要人工审批",
    )


class ChangeEvent(BaseEvent):
    """
    变更审批事件

    由 Change Agent 生成，处理变更请求的审批流程。
    """
    event_type: str = "change"
    incident_id: str = Field(
        default="",
        description="关联的故障ID",
    )
    change_id: str = Field(
        default="",
        description="变更单ID",
    )
    change_type: str = Field(
        default="",
        description="变更类型(deployment/config/infra/etc)",
    )
    approval_status: ApprovalStatus = Field(
        default=ApprovalStatus.PENDING,
        description="审批状态",
    )
    risk_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="风险评估得分(0-1, 越高越危险)",
    )
    risk_level: Literal["low", "medium", "high", "critical"] = Field(
        default="low",
        description="风险等级",
    )
    approvers: list[str] = Field(
        default_factory=list,
        description="审批人列表",
    )
    change_details: dict[str, Any] = Field(
        default_factory=dict,
        description="变更详情",
    )
    automated_decision_reason: str = Field(
        default="",
        description="自动化决策理由",
    )


class AuditEvent(BaseEvent):
    """
    审计事件

    记录系统中的重要操作和决策，用于审计和追溯。
    """
    event_type: str = "audit"
    incident_id: str = Field(
        default="",
        description="关联的故障ID",
    )
    action: str = Field(
        default="",
        description="操作类型",
    )
    actor: str = Field(
        default="",
        description="操作者(agent/user/system)",
    )
    actor_id: str = Field(
        default="",
        description="操作者ID",
    )
    resource_type: str = Field(
        default="",
        description="被操作资源类型",
    )
    resource_id: str = Field(
        default="",
        description="被操作资源ID",
    )
    before_state: dict[str, Any] = Field(
        default_factory=dict,
        description="操作前状态",
    )
    after_state: dict[str, Any] = Field(
        default_factory=dict,
        description="操作后状态",
    )
    reason: str = Field(
        default="",
        description="操作理由",
    )
