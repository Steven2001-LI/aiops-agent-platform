"""
AIOps Agent Platform - Tool Base Class

所有工具的基类，定义统一接口和通用功能。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from app.utils.logging import get_logger

logger = get_logger(__name__)


class ToolResult(BaseModel):
    """
    工具执行结果

    统一的工具输出格式。
    """
    result_id: str = Field(default_factory=lambda: str(uuid4()))
    tool_name: str = Field(default="", description="工具名称")
    success: bool = Field(default=False, description="是否成功")
    data: dict[str, Any] = Field(
        default_factory=dict, description="输出数据"
    )
    error_message: str = Field(default="", description="错误信息")
    execution_time_ms: int = Field(default=0, description="执行耗时(ms)")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    @classmethod
    def ok(
        cls,
        tool_name: str,
        data: dict[str, Any],
        execution_time_ms: int = 0,
    ) -> "ToolResult":
        """创建成功结果"""
        return cls(
            tool_name=tool_name,
            success=True,
            data=data,
            execution_time_ms=execution_time_ms,
        )

    @classmethod
    def error(
        cls,
        tool_name: str,
        error_message: str,
        execution_time_ms: int = 0,
    ) -> "ToolResult":
        """创建失败结果"""
        return cls(
            tool_name=tool_name,
            success=False,
            error_message=error_message,
            execution_time_ms=execution_time_ms,
        )


class ToolParameter(BaseModel):
    """工具参数定义"""
    name: str = Field(default="", description="参数名")
    description: str = Field(default="", description="参数描述")
    type: str = Field(default="string", description="参数类型")
    required: bool = Field(default=True, description="是否必填")
    default: Any = Field(default=None, description="默认值")


class BaseTool(ABC):
    """
    工具抽象基类

    所有工具必须继承此类并实现 execute 方法。

    Example:
        class MyTool(BaseTool):
            @property
            def name(self) -> str:
                return "my_tool"

            @property
            def description(self) -> str:
                return "My tool description"

            @property
            def parameters(self) -> list[ToolParameter]:
                return [ToolParameter(name="param1", description="...")]

            async def execute(self, **kwargs) -> ToolResult:
                # 实现工具逻辑
                pass
    """

    def __init__(self) -> None:
        self._logger = get_logger(f"tool.{self.name}")

    @property
    @abstractmethod
    def name(self) -> str:
        """工具名称"""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """工具描述"""
        ...

    @property
    def parameters(self) -> list[ToolParameter]:
        """工具参数定义"""
        return []

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult:
        """
        执行工具

        Args:
            **kwargs: 工具参数

        Returns:
            ToolResult: 执行结果
        """
        ...

    def get_schema(self) -> dict[str, Any]:
        """
        获取工具 JSON Schema（用于 LLM function calling）

        Returns:
            dict: JSON Schema
        """
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    p.name: {
                        "type": p.type,
                        "description": p.description,
                    }
                    for p in self.parameters
                },
                "required": [
                    p.name for p in self.parameters if p.required
                ],
            },
        }


class ToolRegistry:
    """
    工具注册中心

    管理所有可用工具的注册和查找。
    """

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """
        注册工具

        Args:
            tool: 工具实例
        """
        self._tools[tool.name] = tool
        logger.info("Tool registered", tool_name=tool.name)

    def unregister(self, tool_name: str) -> None:
        """
        注销工具

        Args:
            tool_name: 工具名称
        """
        if tool_name in self._tools:
            del self._tools[tool_name]
            logger.info("Tool unregistered", tool_name=tool_name)

    def get(self, tool_name: str) -> BaseTool | None:
        """
        获取工具

        Args:
            tool_name: 工具名称

        Returns:
            BaseTool | None: 工具实例或 None
        """
        return self._tools.get(tool_name)

    def list_tools(self) -> list[BaseTool]:
        """
        列出所有工具

        Returns:
            list[BaseTool]: 工具列表
        """
        return list(self._tools.values())

    def get_all_schemas(self) -> list[dict[str, Any]]:
        """
        获取所有工具的 JSON Schema

        Returns:
            list[dict]: Schema 列表
        """
        return [tool.get_schema() for tool in self._tools.values()]


# 全局工具注册中心
tool_registry = ToolRegistry()
