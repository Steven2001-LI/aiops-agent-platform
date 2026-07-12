"""
AIOps Agent Platform - Memory Tools

记忆操作工具，用于 Agent 执行过程中存储和检索记忆。
集成 MemorySystem 提供完整的记忆管理能力。
"""

from __future__ import annotations

from typing import Any

from app.memory.core import MemorySystem
from app.models.memory import MemoryType
from app.tools.base import BaseTool, ToolParameter, ToolResult
from app.utils.logging import get_logger

logger = get_logger(__name__)


async def _get_memory_system() -> MemorySystem:
    """获取记忆系统实例"""
    return await MemorySystem.get_instance()


# ============================================================
# 基础记忆工具（保留用于 LLM function calling）
# ============================================================


class StoreMemoryTool(BaseTool):
    """
    存储记忆工具

    将信息存储到记忆系统，供后续检索和使用。
    重要记忆会自动同步到长期记忆。
    """

    @property
    def name(self) -> str:
        return "store_memory"

    @property
    def description(self) -> str:
        return (
            "存储一条记忆到记忆系统，供后续检索和使用。"
            "支持短期记忆（快速访问）和长期记忆（持久存储）。"
            "重要性 >= 0.5 的记忆会自动同步到长期记忆。"
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="content",
                description="记忆内容（要存储的文本信息）",
                type="string",
                required=True,
            ),
            ToolParameter(
                name="memory_type",
                description="记忆类型: episodic(事件)/semantic(知识)/procedural(步骤)/observation(观察)",
                type="string",
                required=False,
                default="observation",
            ),
            ToolParameter(
                name="incident_id",
                description="关联故障ID",
                type="string",
                required=False,
                default="",
            ),
            ToolParameter(
                name="importance",
                description="重要性(0-1)，>=0.5会自动存入长期记忆",
                type="number",
                required=False,
                default=0.5,
            ),
            ToolParameter(
                name="tags",
                description="标签列表，帮助分类和检索",
                type="array",
                required=False,
                default=[],
            ),
            ToolParameter(
                name="session_id",
                description="会话ID（用于短期记忆的会话隔离）",
                type="string",
                required=False,
                default="default",
            ),
            ToolParameter(
                name="agent_name",
                description="来源Agent名称",
                type="string",
                required=False,
                default="",
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        """存储记忆"""
        content = kwargs.get("content", "")
        if not content:
            return ToolResult.error(
                tool_name=self.name,
                error_message="content is required",
            )

        memory_type_str = kwargs.get("memory_type", "observation")
        try:
            memory_type = MemoryType(memory_type_str)
        except ValueError:
            memory_type = MemoryType.OBSERVATION

        incident_id = kwargs.get("incident_id", "")
        importance = float(kwargs.get("importance", 0.5))
        tags = kwargs.get("tags", [])
        session_id = kwargs.get("session_id", "default")
        agent_name = kwargs.get("agent_name", "")

        logger.info(
            "Storing memory via tool",
            memory_type=memory_type.value,
            incident_id=incident_id,
            content_length=len(content),
            importance=importance,
        )

        try:
            ms = await _get_memory_system()
            entry = await ms.store(
                content=content,
                memory_type=memory_type,
                source_agent=agent_name,
                source_incident_id=incident_id,
                importance=importance,
                tags=tags if isinstance(tags, list) else [str(tags)],
                session_id=session_id,
            )

            return ToolResult.ok(
                tool_name=self.name,
                data={
                    "memory_id": entry.memory_id,
                    "stored": True,
                    "memory_type": memory_type.value,
                    "memory_level": entry.memory_level.value,
                    "importance": entry.importance_score,
                    "summary": entry.summary,
                    "auto_tags": entry.tags,
                },
            )

        except Exception as e:
            logger.error(f"Failed to store memory: {e}")
            return ToolResult.error(
                tool_name=self.name,
                error_message=f"Failed to store memory: {e}",
            )


class RetrieveMemoryTool(BaseTool):
    """
    检索记忆工具

    从记忆系统中检索相关记忆，支持语义搜索和关键词搜索。
    同时搜索短期记忆和长期记忆，合并去重后返回。
    """

    @property
    def name(self) -> str:
        return "retrieve_memory"

    @property
    def description(self) -> str:
        return (
            "从记忆系统中检索相关记忆，支持语义搜索。"
            "同时搜索短期记忆（最近对话）和长期记忆（历史知识），"
            "使用RRF融合算法综合排序。"
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="query",
                description="查询内容（自然语言描述）",
                type="string",
                required=True,
            ),
            ToolParameter(
                name="top_k",
                description="返回数量(1-100)",
                type="integer",
                required=False,
                default=5,
            ),
            ToolParameter(
                name="incident_id",
                description="关联故障ID（限定搜索范围）",
                type="string",
                required=False,
                default="",
            ),
            ToolParameter(
                name="memory_type",
                description="限定记忆类型: episodic/semantic/procedural/observation",
                type="string",
                required=False,
                default="",
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        """检索记忆"""
        query = kwargs.get("query", "")
        if not query:
            return ToolResult.error(
                tool_name=self.name,
                error_message="query is required",
            )

        top_k = int(kwargs.get("top_k", 5))
        incident_id = kwargs.get("incident_id", "")
        memory_type_str = kwargs.get("memory_type", "")

        logger.info("Retrieving memory via tool", query=query, top_k=top_k)

        try:
            ms = await _get_memory_system()

            memory_type = None
            if memory_type_str:
                try:
                    memory_type = MemoryType(memory_type_str)
                except ValueError:
                    pass

            result = await ms.search(
                query_text=query,
                top_k=top_k,
                memory_type=memory_type,
                incident_id=incident_id,
            )

            return ToolResult.ok(
                tool_name=self.name,
                data={
                    "query": query,
                    "total_found": result.total_found,
                    "results": [
                        {
                            "memory_id": e.memory_id,
                            "content": e.content[:500],
                            "type": e.memory_type.value,
                            "source": e.source_agent,
                            "importance": e.importance_score,
                            "retrieval_score": e.retrieval_score,
                            "tags": e.tags,
                        }
                        for e in result.results
                    ],
                },
            )

        except Exception as e:
            logger.error(f"Failed to retrieve memory: {e}")
            return ToolResult.error(
                tool_name=self.name,
                error_message=f"Failed to retrieve memory: {e}",
            )


class SearchSimilarIncidentsTool(BaseTool):
    """
    搜索相似故障工具

    搜索历史记录中相似的故障案例，获取处理经验。
    """

    @property
    def name(self) -> str:
        return "search_similar_incidents"

    @property
    def description(self) -> str:
        return (
            "搜索历史记录中相似的故障案例，获取处理经验。"
            "基于语义相似度和关键词匹配综合排序。"
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="service",
                description="服务名称",
                type="string",
                required=True,
            ),
            ToolParameter(
                name="symptoms",
                description="故障症状描述",
                type="string",
                required=True,
            ),
            ToolParameter(
                name="top_k",
                description="返回数量",
                type="integer",
                required=False,
                default=5,
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        """搜索相似故障"""
        service = kwargs.get("service", "")
        symptoms = kwargs.get("symptoms", "")
        top_k = int(kwargs.get("top_k", 5))

        if not service or not symptoms:
            return ToolResult.error(
                tool_name=self.name,
                error_message="service and symptoms are required",
            )

        logger.info(
            "Searching similar incidents via tool",
            service=service,
            symptoms=symptoms[:100],
        )

        try:
            ms = await _get_memory_system()
            query_text = f"{service} {symptoms}"

            result = await ms.search(
                query_text=query_text,
                top_k=top_k,
                memory_type=MemoryType.EPISODIC,
            )

            similar_incidents = [
                {
                    "memory_id": e.memory_id,
                    "content": e.content[:500],
                    "source_incident": e.source_incident_id,
                    "importance": e.importance_score,
                    "tags": e.tags,
                }
                for e in result.results
            ]

            return ToolResult.ok(
                tool_name=self.name,
                data={
                    "service": service,
                    "symptoms": symptoms[:200],
                    "similar_incidents": similar_incidents,
                    "total": len(similar_incidents),
                },
            )

        except Exception as e:
            logger.error(f"Failed to search similar incidents: {e}")
            return ToolResult.error(
                tool_name=self.name,
                error_message=f"Failed to search: {e}",
            )


class GetMemoryContextTool(BaseTool):
    """
    获取记忆上下文工具

    为LLM调用组装相关记忆上下文，综合短期、长期、工作记忆。
    """

    @property
    def name(self) -> str:
        return "get_memory_context"

    @property
    def description(self) -> str:
        return (
            "获取记忆上下文用于LLM推理。"
            "综合工作记忆（当前任务）、短期记忆（最近对话）、"
            "长期记忆（相关知识），按优先级组装。"
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="query",
                description="查询（用于检索相关长期记忆）",
                type="string",
                required=True,
            ),
            ToolParameter(
                name="incident_id",
                description="关联故障ID",
                type="string",
                required=False,
                default="",
            ),
            ToolParameter(
                name="session_id",
                description="会话ID",
                type="string",
                required=False,
                default="default",
            ),
            ToolParameter(
                name="max_tokens",
                description="最大token预算",
                type="integer",
                required=False,
                default=4000,
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        """获取记忆上下文"""
        query = kwargs.get("query", "")
        incident_id = kwargs.get("incident_id", "")
        session_id = kwargs.get("session_id", "default")
        max_tokens = int(kwargs.get("max_tokens", 4000))

        logger.info(
            "Getting memory context via tool",
            query=query[:50],
            incident_id=incident_id,
        )

        try:
            ms = await _get_memory_system()
            context = await ms.get_context(
                query=query,
                session_id=session_id,
                incident_id=incident_id,
                max_tokens=max_tokens,
            )

            return ToolResult.ok(
                tool_name=self.name,
                data={
                    "context": context,
                    "context_length": len(context),
                    "incident_id": incident_id,
                },
            )

        except Exception as e:
            logger.error(f"Failed to get memory context: {e}")
            return ToolResult.error(
                tool_name=self.name,
                error_message=f"Failed to get context: {e}",
            )


class WorkingMemoryTool(BaseTool):
    """
    工作记忆操作工具

    读写与当前故障相关的工作记忆，实现Agent间上下文共享。
    """

    @property
    def name(self) -> str:
        return "working_memory"

    @property
    def description(self) -> str:
        return (
            "读写工作记忆（当前任务状态）。"
            "所有Agent可读写同一incident的工作记忆，实现共享上下文。"
            "支持键值对存储和嵌套路径访问。"
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="operation",
                description="操作: set/get/get_all/delete",
                type="string",
                required=True,
            ),
            ToolParameter(
                name="key",
                description="键名（支持点路径如 root_cause.result）",
                type="string",
                required=False,
                default="",
            ),
            ToolParameter(
                name="value",
                description="值（set操作时使用）",
                type="string",
                required=False,
                default="",
            ),
            ToolParameter(
                name="incident_id",
                description="故障ID",
                type="string",
                required=False,
                default="default",
            ),
            ToolParameter(
                name="ttl_seconds",
                description="生存时间(秒)",
                type="integer",
                required=False,
                default=3600,
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        """工作记忆操作"""
        operation = kwargs.get("operation", "get")
        key = kwargs.get("key", "")
        value = kwargs.get("value", "")
        incident_id = kwargs.get("incident_id", "default")
        ttl_seconds = int(kwargs.get("ttl_seconds", 3600))

        try:
            ms = await _get_memory_system()

            if operation == "set":
                if not key:
                    return ToolResult.error(
                        tool_name=self.name,
                        error_message="key is required for set operation",
                    )
                await ms.working_set(
                    key=key,
                    value=value,
                    incident_id=incident_id,
                    ttl_seconds=ttl_seconds,
                    priority=5,
                )
                return ToolResult.ok(
                    tool_name=self.name,
                    data={"operation": "set", "key": key, "incident_id": incident_id},
                )

            elif operation == "get":
                if not key:
                    return ToolResult.error(
                        tool_name=self.name,
                        error_message="key is required for get operation",
                    )
                result = await ms.working_get(key, incident_id)
                return ToolResult.ok(
                    tool_name=self.name,
                    data={"operation": "get", "key": key, "value": result},
                )

            elif operation == "get_all":
                all_data = await ms.working_get_all(incident_id)
                return ToolResult.ok(
                    tool_name=self.name,
                    data={"operation": "get_all", "data": all_data},
                )

            elif operation == "delete":
                if not key:
                    return ToolResult.error(
                        tool_name=self.name,
                        error_message="key is required for delete operation",
                    )
                deleted = await ms.working.delete(key, incident_id)
                return ToolResult.ok(
                    tool_name=self.name,
                    data={"operation": "delete", "key": key, "deleted": deleted},
                )

            else:
                return ToolResult.error(
                    tool_name=self.name,
                    error_message=f"Unknown operation: {operation}",
                )

        except Exception as e:
            logger.error(f"Working memory operation failed: {e}")
            return ToolResult.error(
                tool_name=self.name,
                error_message=f"Working memory operation failed: {e}",
            )


# ============================================================
# 高级记忆工具 - MemoryTool
# ============================================================


class MemoryTool(BaseTool):
    """
    高级记忆操作工具

    提供统一的记忆存储、检索和上下文获取接口。
    整合短期记忆、长期记忆和工作记忆的操作。
    """

    @property
    def name(self) -> str:
        return "memory"

    @property
    def description(self) -> str:
        return (
            "统一记忆操作接口：存储和检索记忆，获取会话上下文。"
            "支持按类型过滤和重要性评分。"
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="operation",
                description="操作类型(store/search/get_recent_context)",
                type="string",
                required=True,
            ),
            ToolParameter(
                name="key",
                description="存储键名",
                type="string",
                required=False,
                default="",
            ),
            ToolParameter(
                name="value",
                description="存储值",
                type="string",
                required=False,
                default="",
            ),
            ToolParameter(
                name="query",
                description="搜索查询",
                type="string",
                required=False,
                default="",
            ),
            ToolParameter(
                name="memory_type",
                description="记忆类型(episodic/semantic/procedural/observation)",
                type="string",
                required=False,
                default="observation",
            ),
            ToolParameter(
                name="top_k",
                description="返回数量",
                type="integer",
                required=False,
                default=5,
            ),
            ToolParameter(
                name="session_id",
                description="会话ID",
                type="string",
                required=False,
                default="default",
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        """
        执行记忆操作

        Args:
            operation: 操作类型
            key: 存储键名
            value: 存储值
            query: 搜索查询
            memory_type: 记忆类型
            top_k: 返回数量
            session_id: 会话ID

        Returns:
            ToolResult: 操作结果
        """
        operation = kwargs.get("operation", "search")

        logger.info("Memory tool operation", operation=operation)

        try:
            result: dict[str, Any] = {}

            if operation == "store":
                result = await self.store_memory(
                    key=kwargs.get("key", ""),
                    value=kwargs.get("value", ""),
                    memory_type=kwargs.get("memory_type", "observation"),
                )
            elif operation == "search":
                results = await self.search_memory(
                    query=kwargs.get("query", ""),
                    memory_type=kwargs.get("memory_type") or None,
                    top_k=int(kwargs.get("top_k", 5)),
                )
                result = {"results": results, "total": len(results)}
            elif operation == "get_recent_context":
                context = await self.get_recent_context(
                    session_id=kwargs.get("session_id", "default"),
                    limit=int(kwargs.get("top_k", 10)),
                )
                result = {"context_entries": context, "total": len(context)}
            else:
                return ToolResult.error(
                    tool_name=self.name,
                    error_message=f"Unknown operation: {operation}",
                )

            return ToolResult.ok(
                tool_name=self.name,
                data=result,
            )

        except Exception as e:
            logger.error(
                "Memory operation failed",
                operation=operation,
                error=str(e),
            )
            return ToolResult.error(
                tool_name=self.name,
                error_message=f"Memory operation failed: {e}",
            )

    async def store_memory(
        self,
        key: str,
        value: str,
        memory_type: str = "observation",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        存储记忆

        Args:
            key: 键名
            value: 值
            memory_type: 记忆类型
            **kwargs: 额外参数

        Returns:
            存储结果字典
        """
        try:
            mt = MemoryType(memory_type)
        except ValueError:
            mt = MemoryType.OBSERVATION

        ms = await _get_memory_system()
        entry = await ms.store(
            content=f"{key}: {value}",
            memory_type=mt,
            source_agent=kwargs.get("agent_name", ""),
            source_incident_id=kwargs.get("incident_id", ""),
            importance=kwargs.get("importance", 0.5),
            tags=kwargs.get("tags", []),
            session_id=kwargs.get("session_id", "default"),
        )

        logger.info(
            "Memory stored via MemoryTool",
            memory_id=entry.memory_id,
            memory_type=mt.value,
        )

        return {
            "memory_id": entry.memory_id,
            "stored": True,
            "memory_type": mt.value,
        }

    async def search_memory(
        self,
        query: str,
        memory_type: str | None = None,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """
        搜索记忆

        Args:
            query: 搜索查询
            memory_type: 记忆类型过滤
            top_k: 返回数量

        Returns:
            记忆条目列表
        """
        ms = await _get_memory_system()

        mt = None
        if memory_type:
            try:
                mt = MemoryType(memory_type)
            except ValueError:
                pass

        result = await ms.search(
            query_text=query,
            top_k=top_k,
            memory_type=mt,
        )

        return [
            {
                "memory_id": e.memory_id,
                "content": e.content[:500],
                "type": e.memory_type.value,
                "importance": e.importance_score,
                "retrieval_score": e.retrieval_score,
            }
            for e in result.results
        ]

    async def get_recent_context(
        self,
        session_id: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        获取最近的会话上下文

        Args:
            session_id: 会话ID
            limit: 返回条数

        Returns:
            最近记忆条目列表
        """
        ms = await _get_memory_system()

        # 从短期记忆获取最近条目
        try:
            result = await ms.search(
                query_text="",
                top_k=limit,
            )
            return [
                {
                    "memory_id": e.memory_id,
                    "content": e.content[:300],
                    "type": e.memory_type.value,
                    "created_at": str(e.created_at),
                }
                for e in result.results
            ]
        except Exception as e:
            logger.warning("Failed to get recent context", error=str(e))
            return []


# 工具注册函数

def register_memory_tools() -> list[BaseTool]:
    """
    注册所有记忆工具

    Returns:
        list[BaseTool]: 记忆工具列表
    """
    return [
        StoreMemoryTool(),
        RetrieveMemoryTool(),
        SearchSimilarIncidentsTool(),
        GetMemoryContextTool(),
        WorkingMemoryTool(),
        MemoryTool(),
    ]
