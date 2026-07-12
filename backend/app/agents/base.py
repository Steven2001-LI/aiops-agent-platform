"""
AIOps Agent Platform - Agent Abstract Base Class

所有 Agent 的抽象基类，定义统一接口和通用功能。
使用泛型和 Pydantic 模型确保类型安全。
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Generic, TypeVar
from uuid import uuid4

from pydantic import BaseModel, Field

from app.config import AgentConfig, get_config
from app.models.agent import AgentExecutionContext, AgentState, AgentStatus
from app.utils.logging import LogContext, get_logger

logger = get_logger(__name__)

# 泛型类型变量
TInput = TypeVar("TInput", bound=BaseModel)
TOutput = TypeVar("TOutput", bound=BaseModel)


class AgentResult(BaseModel):
    """
    Agent 执行结果

    统一的 Agent 输出包装，包含执行元数据和输出数据。
    """
    result_id: str = Field(default_factory=lambda: str(uuid4()))
    success: bool = Field(default=False, description="是否成功")
    agent_name: str = Field(default="", description="Agent 名称")
    execution_time_ms: int = Field(default=0, description="执行耗时(ms)")
    iteration_count: int = Field(default=0, description="迭代次数")
    output_data: dict[str, Any] = Field(
        default_factory=dict, description="输出数据"
    )
    error_message: str = Field(default="", description="错误信息")
    metadata: dict[str, Any] = Field(default_factory=dict, description="元数据")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    @classmethod
    def success_result(
        cls,
        agent_name: str,
        output_data: dict[str, Any],
        execution_time_ms: int = 0,
        **kwargs: Any,
    ) -> "AgentResult":
        """创建成功结果"""
        return cls(
            success=True,
            agent_name=agent_name,
            output_data=output_data,
            execution_time_ms=execution_time_ms,
            **kwargs,
        )

    @classmethod
    def failure_result(
        cls,
        agent_name: str,
        error_message: str,
        execution_time_ms: int = 0,
        **kwargs: Any,
    ) -> "AgentResult":
        """创建失败结果"""
        return cls(
            success=False,
            agent_name=agent_name,
            error_message=error_message,
            execution_time_ms=execution_time_ms,
            **kwargs,
        )


class BaseAgent(ABC, Generic[TInput, TOutput]):
    """
    Agent 抽象基类

    所有 Agent 必须继承此类并实现抽象方法。
    提供通用功能：日志记录、超时控制、错误处理、状态管理。

    Example:
        class MyAgent(BaseAgent[MyInput, MyOutput]):
            async def process(self, input_data: MyInput, context: AgentExecutionContext) -> AgentResult:
                # 实现处理逻辑
                pass

            def get_name(self) -> str:
                return "my_agent"

            def get_description(self) -> str:
                return "My agent description"
    """

    def __init__(self) -> None:
        self._state = self._init_state()
        self._config: AgentConfig = get_config().agent
        self._logger = get_logger(f"agent.{self.get_name()}")

    def _init_state(self) -> AgentState:
        """初始化 Agent 状态"""
        return AgentState(
            name=self.get_name(),
            description=self.get_description(),
        )

    @property
    def state(self) -> AgentState:
        """Agent 当前状态"""
        return self._state

    @abstractmethod
    async def process(
        self,
        input_data: TInput,
        context: AgentExecutionContext,
    ) -> AgentResult:
        """
        Agent 核心处理逻辑

        Args:
            input_data: 输入数据
            context: 执行上下文

        Returns:
            AgentResult: 执行结果
        """
        ...

    @abstractmethod
    def get_name(self) -> str:
        """获取 Agent 名称"""
        ...

    @abstractmethod
    def get_description(self) -> str:
        """获取 Agent 描述"""
        ...

    def get_version(self) -> str:
        """获取 Agent 版本"""
        return "1.0.0"

    async def execute(
        self,
        input_data: TInput,
        context: AgentExecutionContext | None = None,
    ) -> AgentResult:
        """
        执行 Agent（带超时控制和错误处理）

        Args:
            input_data: 输入数据
            context: 执行上下文，为 None 时自动创建

        Returns:
            AgentResult: 执行结果
        """
        if context is None:
            context = AgentExecutionContext(
                incident_id="default",
                input_data=input_data.model_dump() if hasattr(input_data, "model_dump") else {},
            )

        ctx = context.metadata.copy()
        ctx["correlation_id"] = context.context_id
        ctx["agent_name"] = self.get_name()

        with LogContext(**ctx):
            self._logger.info(
                "Agent execution started",
                agent=self.get_name(),
                context_id=context.context_id,
            )

            self._state.status = AgentStatus.BUSY
            self._state.current_task = f"Processing {context.incident_id}"
            self._state.progress_percent = 0

            start_time = datetime.now(timezone.utc)

            try:
                # 使用超时控制
                result = await asyncio.wait_for(
                    self._do_process(input_data, context),
                    timeout=self._config.execution_timeout_seconds,
                )

                execution_time = int(
                    (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
                )
                result.execution_time_ms = execution_time

                # 更新状态
                self._state.record_execution(
                    success=result.success,
                    execution_time_ms=execution_time,
                )
                self._state.status = AgentStatus.IDLE
                self._state.current_task = ""
                self._state.progress_percent = 100

                self._logger.info(
                    "Agent execution completed",
                    success=result.success,
                    execution_time_ms=execution_time,
                )

                return result

            except asyncio.TimeoutError:
                execution_time = int(
                    (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
                )
                self._state.record_execution(
                    success=False,
                    execution_time_ms=execution_time,
                    timed_out=True,
                )
                self._state.status = AgentStatus.DEGRADED

                self._logger.error(
                    "Agent execution timed out",
                    timeout_seconds=self._config.execution_timeout_seconds,
                )

                return AgentResult.failure_result(
                    agent_name=self.get_name(),
                    error_message=f"Execution timed out after {self._config.execution_timeout_seconds}s",
                    execution_time_ms=execution_time,
                )

            except Exception as e:
                execution_time = int(
                    (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
                )
                self._state.record_error(str(e))
                self._state.status = AgentStatus.ERROR

                self._logger.error(
                    "Agent execution failed",
                    error=str(e),
                    exc_info=True,
                )

                return AgentResult.failure_result(
                    agent_name=self.get_name(),
                    error_message=str(e),
                    execution_time_ms=execution_time,
                )

            finally:
                self._state.current_task = ""
                self._state.progress_percent = 0

    async def _do_process(
        self,
        input_data: TInput,
        context: AgentExecutionContext,
    ) -> AgentResult:
        """
        内部处理包装

        子类可重写此方法以添加前置/后置处理逻辑。
        """
        return await self.process(input_data, context)

    async def health_check(self) -> dict[str, Any]:
        """
        Agent 健康检查

        Returns:
            dict: 健康状态信息
        """
        return {
            "agent_name": self.get_name(),
            "status": self._state.status.value,
            "is_healthy": self._state.is_healthy,
            "total_executions": self._state.total_executions,
            "success_rate": self._state.success_rate,
        }

    def reset_state(self) -> None:
        """重置 Agent 状态"""
        self._state = self._init_state()
        self._logger.info("Agent state reset", agent=self.get_name())
