"""
AIOps Agent Platform - Agent State Models

定义 Agent 的状态模型，用于描述各 Agent 的运行时状态、
输入输出模式和执行上下文。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_serializer


class AgentStatus(str, Enum):
    """Agent 运行状态"""
    IDLE = "idle"              # 空闲
    BUSY = "busy"              # 执行中
    ERROR = "error"            # 错误
    OFFLINE = "offline"        # 离线
    DEGRADED = "degraded"      # 降级运行


class AgentType(str, Enum):
    """Agent 类型"""
    ORCHESTRATOR = "orchestrator"    # 编排器
    MONITOR = "monitor"              # 监控告警
    RCA = "rca"                      # 根因分析
    HEAL = "heal"                    # 故障自愈
    CHANGE = "change"                # 变更审批
    MEMORY = "memory"                # 记忆管理
    EVAL = "eval"                    # 评估


class AgentCapability(BaseModel):
    """Agent 能力描述"""
    name: str = Field(default="", description="能力名称")
    description: str = Field(default="", description="能力描述")
    input_schema: dict[str, Any] = Field(
        default_factory=dict, description="输入参数 JSON Schema"
    )
    output_schema: dict[str, Any] = Field(
        default_factory=dict, description="输出参数 JSON Schema"
    )
    requires_approval: bool = Field(
        default=False, description="是否需要人工审批"
    )
    timeout_seconds: int = Field(default=60, description="超时时间(秒)")


class AgentState(BaseModel):
    """
    Agent 状态模型

    描述 Agent 的当前运行状态和统计信息。
    """
    agent_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Agent 实例ID",
    )
    agent_type: AgentType = Field(default=AgentType.ORCHESTRATOR)
    name: str = Field(default="", description="Agent 名称")
    description: str = Field(default="", description="Agent 描述")
    version: str = Field(default="1.0.0", description="Agent 版本")

    # 运行时状态
    status: AgentStatus = Field(default=AgentStatus.IDLE)
    current_task: str = Field(default="", description="当前任务描述")
    progress_percent: int = Field(
        default=0, ge=0, le=100, description="进度百分比"
    )

    # 统计数据
    total_executions: int = Field(default=0, description="总执行次数")
    successful_executions: int = Field(default=0, description="成功次数")
    failed_executions: int = Field(default=0, description="失败次数")
    timeout_count: int = Field(default=0, description="超时次数")
    average_execution_time_ms: float = Field(default=0.0, description="平均执行时间(ms)")

    # 时间戳
    registered_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    last_active_at: datetime | None = Field(default=None)
    last_error_at: datetime | None = Field(default=None)
    last_error_message: str = Field(default="")

    # 能力列表
    capabilities: list[AgentCapability] = Field(default_factory=list)

    # 元数据
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_serializer("registered_at", "last_active_at", "last_error_at")
    def serialize_datetimes(self, value: datetime | None) -> str | None:
        if value is not None:
            return value.isoformat()
        return None

    @property
    def success_rate(self) -> float:
        """成功率"""
        if self.total_executions == 0:
            return 0.0
        return self.successful_executions / self.total_executions

    @property
    def is_available(self) -> bool:
        """Agent 是否可用"""
        return self.status in (AgentStatus.IDLE, AgentStatus.BUSY)

    @property
    def is_healthy(self) -> bool:
        """Agent 是否健康"""
        return self.status not in (AgentStatus.ERROR, AgentStatus.OFFLINE)

    def record_execution(
        self,
        success: bool,
        execution_time_ms: float,
        timed_out: bool = False,
    ) -> None:
        """
        记录一次执行结果

        Args:
            success: 是否成功
            execution_time_ms: 执行耗时(ms)
            timed_out: 是否超时
        """
        self.total_executions += 1
        if success:
            self.successful_executions += 1
        else:
            self.failed_executions += 1
        if timed_out:
            self.timeout_count += 1

        # 更新平均执行时间
        self.average_execution_time_ms = (
            (self.average_execution_time_ms * (self.total_executions - 1) + execution_time_ms)
            / self.total_executions
        )
        self.last_active_at = datetime.now(timezone.utc)

    def record_error(self, error_message: str) -> None:
        """记录错误"""
        self.status = AgentStatus.ERROR
        self.last_error_at = datetime.now(timezone.utc)
        self.last_error_message = error_message
        self.failed_executions += 1
        self.total_executions += 1


class AgentMessage(BaseModel):
    """
    Agent 间消息

    用于 Agent 之间的通信和协调。
    """
    message_id: str = Field(default_factory=lambda: str(uuid4()))
    correlation_id: str = Field(default="", description="关联ID")
    from_agent: str = Field(default="", description="发送方 Agent ID")
    to_agent: str = Field(default="", description="接收方 Agent ID")
    message_type: Literal[
        "request", "response", "notification", "command", "event"
    ] = Field(default="request")
    payload: dict[str, Any] = Field(default_factory=dict, description="消息载荷")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    priority: int = Field(default=5, ge=1, le=10, description="优先级(1最高)")

    @field_serializer("timestamp")
    def serialize_timestamp(self, value: datetime) -> str:
        return value.isoformat()


class AgentExecutionContext(BaseModel):
    """
    Agent 执行上下文

    包含 Agent 执行时需要的完整上下文信息。
    """
    context_id: str = Field(default_factory=lambda: str(uuid4()))
    incident_id: str = Field(default="", description="关联故障ID")
    parent_context_id: str | None = Field(
        default=None, description="父上下文ID(用于子任务)"
    )

    # 输入数据
    input_data: dict[str, Any] = Field(default_factory=dict)

    # 记忆引用
    memory_refs: list[str] = Field(
        default_factory=list, description="关联记忆ID列表"
    )

    # 执行约束
    max_iterations: int = Field(default=10, description="最大迭代次数")
    timeout_seconds: int = Field(default=120, description="超时时间(秒)")
    dry_run: bool = Field(default=False, description="是否仅模拟")

    # 运行时数据
    iteration_count: int = Field(default=0, description="当前迭代次数")
    intermediate_results: list[dict[str, Any]] = Field(
        default_factory=list, description="中间结果"
    )

    # 元数据
    metadata: dict[str, Any] = Field(default_factory=dict)
