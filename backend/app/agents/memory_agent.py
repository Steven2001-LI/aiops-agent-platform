"""
AIOps Agent Platform - Memory Agent

记忆管理 Agent，负责记忆的存储、检索、整理和归档。
实现 BaseAgent 接口，提供工具调用风格的记忆操作。

职责：
- 存储 Agent 执行过程中的关键信息
- 检索相关历史记忆
- 整理和合并相似记忆
- 归档过期记忆
- 记忆流转管理
- 自动记忆记录（在处理过程中自动记录关键信息）
"""

from __future__ import annotations

from typing import Any

from app.agents.base import AgentResult, BaseAgent
from app.memory.core import MemorySystem
from app.models.agent import AgentExecutionContext
from app.models.memory import (
    MemoryEntry,
    MemoryQuery,
    MemoryType,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)


class MemoryInput:
    """
    Memory Agent 输入

    支持操作: store / retrieve / search / update / delete / consolidate / archive / flow / get_context
    """

    def __init__(
        self,
        operation: str = "store",
        content: str = "",
        memory_type: MemoryType = MemoryType.OBSERVATION,
        incident_id: str = "",
        agent_name: str = "",
        query: str = "",
        memory_id: str = "",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        importance: float = 0.5,
        top_k: int = 5,
        session_id: str = "default",
        flow_type: str = "",
        **kwargs: Any,
    ) -> None:
        self.operation = operation
        self.content = content
        self.memory_type = memory_type
        self.incident_id = incident_id
        self.agent_name = agent_name
        self.query = query
        self.memory_id = memory_id
        self.tags = tags or []
        self.metadata = metadata or {}
        self.importance = importance
        self.top_k = top_k
        self.session_id = session_id
        self.flow_type = flow_type
        self.extra = kwargs


class MemoryAgent(BaseAgent[MemoryInput, dict[str, Any]]):
    """
    记忆管理 Agent

    职责：
    - 存储 Agent 执行过程中的关键信息
    - 检索相关历史记忆
    - 整理和合并相似记忆
    - 归档过期记忆
    - 记忆流转管理（STM<->LTM<->WM）
    - 上下文组装
    """

    def __init__(self) -> None:
        super().__init__()
        self._memory_system: MemorySystem | None = None

    async def _get_memory_system(self) -> MemorySystem:
        """获取记忆系统（懒加载）"""
        if self._memory_system is None:
            self._memory_system = await MemorySystem.get_instance()
        return self._memory_system

    def get_name(self) -> str:
        return "memory_agent"

    def get_description(self) -> str:
        return "记忆管理 Agent - 存储、检索、整理、归档和流转运维记忆"

    # ---- 主处理逻辑 ----

    async def process(
        self,
        input_data: MemoryInput,
        context: AgentExecutionContext,
    ) -> AgentResult:
        """
        执行记忆操作

        Args:
            input_data: 记忆操作输入
            context: 执行上下文

        Returns:
            AgentResult: 操作结果
        """
        logger.info(
            "MemoryAgent processing",
            operation=input_data.operation,
            incident_id=input_data.incident_id,
        )

        operation_map = {
            "store": self._store_memory,
            "retrieve": self._retrieve_memory,
            "search": self._search_memory,
            "update": self._update_memory,
            "delete": self._delete_memory,
            "consolidate": self._consolidate_memory,
            "archive": self._archive_memory,
            "flow": self._flow_memory,
            "get_context": self._get_context,
        }

        handler = operation_map.get(input_data.operation)
        if handler is None:
            return AgentResult.failure_result(
                agent_name=self.get_name(),
                error_message=f"Unknown operation: {input_data.operation}. "
                f"Supported: {list(operation_map.keys())}",
            )

        try:
            return await handler(input_data, context)
        except Exception as e:
            logger.error(
                f"Memory operation failed: {input_data.operation}",
                error=str(e),
                exc_info=True,
            )
            return AgentResult.failure_result(
                agent_name=self.get_name(),
                error_message=f"Operation {input_data.operation} failed: {e}",
            )

    # ---- 存储 ----

    async def _store_memory(
        self,
        input_data: MemoryInput,
        context: AgentExecutionContext,
    ) -> AgentResult:
        """存储记忆到短期记忆和长期记忆（重要时）"""
        ms = await self._get_memory_system()

        entry = await ms.store(
            content=input_data.content,
            memory_type=input_data.memory_type,
            source_agent=input_data.agent_name or context.metadata.get("agent_name", ""),
            source_incident_id=input_data.incident_id or context.incident_id,
            importance=input_data.importance,
            tags=input_data.tags,
            metadata={
                **input_data.metadata,
                "session_id": input_data.session_id,
                "stored_by": self.get_name(),
            },
            session_id=input_data.session_id,
        )

        return AgentResult.success_result(
            agent_name=self.get_name(),
            output_data={
                "memory_id": entry.memory_id,
                "operation": "store",
                "memory_type": input_data.memory_type.value,
                "memory_level": entry.memory_level.value,
                "importance": entry.importance_score,
                "summary": entry.summary,
                "tags": entry.tags,
            },
        )

    # ---- 检索 ----

    async def _retrieve_memory(
        self,
        input_data: MemoryInput,
        context: AgentExecutionContext,
    ) -> AgentResult:
        """从两级记忆检索"""
        ms = await self._get_memory_system()

        query = MemoryQuery(
            query_text=input_data.query,
            top_k=input_data.top_k,
            source_incident_id=input_data.incident_id or context.incident_id,
            source_agent=input_data.agent_name,
            tags=input_data.tags,
        )

        result = await ms.retrieve(query)

        return AgentResult.success_result(
            agent_name=self.get_name(),
            output_data={
                "operation": "retrieve",
                "query": input_data.query,
                "query_id": result.query_id,
                "results": [
                    {
                        "memory_id": e.memory_id,
                        "content": e.content[:500],
                        "type": e.memory_type.value,
                        "importance": e.importance_score,
                        "retrieval_score": e.retrieval_score,
                        "created_at": e.created_at.isoformat() if e.created_at else None,
                    }
                    for e in result.results
                ],
                "total_found": result.total_found,
                "query_time_ms": result.query_time_ms,
            },
        )

    # ---- 搜索 ----

    async def _search_memory(
        self,
        input_data: MemoryInput,
        context: AgentExecutionContext,
    ) -> AgentResult:
        """快捷搜索"""
        ms = await self._get_memory_system()

        result = await ms.search(
            query_text=input_data.query,
            top_k=input_data.top_k,
            memory_type=input_data.memory_type,
            incident_id=input_data.incident_id or context.incident_id,
            agent_id=input_data.agent_name,
        )

        return AgentResult.success_result(
            agent_name=self.get_name(),
            output_data={
                "operation": "search",
                "query": input_data.query,
                "results": [
                    {
                        "memory_id": e.memory_id,
                        "content": e.content[:500],
                        "type": e.memory_type.value,
                        "source": e.source_agent,
                        "importance": e.importance_score,
                    }
                    for e in result.results
                ],
                "total_found": result.total_found,
            },
        )

    # ---- 更新 ----

    async def _update_memory(
        self,
        input_data: MemoryInput,
        context: AgentExecutionContext,
    ) -> AgentResult:
        """更新记忆"""
        ms = await self._get_memory_system()

        success = await ms.update(
            memory_id=input_data.memory_id,
            content=input_data.content if input_data.content else None,
            importance=input_data.importance,
            tags=input_data.tags if input_data.tags else None,
        )

        return AgentResult.success_result(
            agent_name=self.get_name(),
            output_data={
                "operation": "update",
                "memory_id": input_data.memory_id,
                "success": success,
            },
        )

    # ---- 删除 ----

    async def _delete_memory(
        self,
        input_data: MemoryInput,
        context: AgentExecutionContext,
    ) -> AgentResult:
        """删除记忆"""
        ms = await self._get_memory_system()

        success = await ms.delete(input_data.memory_id)

        return AgentResult.success_result(
            agent_name=self.get_name(),
            output_data={
                "operation": "delete",
                "memory_id": input_data.memory_id,
                "success": success,
            },
        )

    # ---- 整理 ----

    async def _consolidate_memory(
        self,
        input_data: MemoryInput,
        context: AgentExecutionContext,
    ) -> AgentResult:
        """整理记忆（合并相似，遗忘过期）"""
        ms = await self._get_memory_system()
        stats = await ms.consolidate()

        return AgentResult.success_result(
            agent_name=self.get_name(),
            output_data={
                "operation": "consolidate",
                "stats": stats,
            },
        )

    # ---- 归档 ----

    async def _archive_memory(
        self,
        input_data: MemoryInput,
        context: AgentExecutionContext,
    ) -> AgentResult:
        """归档记忆（遗忘过期 + 工作记忆归档 + STM流转）"""
        ms = await self._get_memory_system()

        archived_count = 0

        # 归档旧记忆
        forgotten = await ms.archive_old_memories()
        archived_count += forgotten

        # 归档工作记忆
        incident_id = input_data.incident_id or context.incident_id
        if incident_id:
            wm_archived = await ms.flow_wm_to_ltm(incident_id)
            archived_count += wm_archived

        # 重要短期记忆流转
        stm_archived = await ms.flow_stm_to_ltm(importance_threshold=0.6)
        archived_count += stm_archived

        return AgentResult.success_result(
            agent_name=self.get_name(),
            output_data={
                "operation": "archive",
                "archived_count": archived_count,
                "forgotten": forgotten,
                "stm_to_ltm": stm_archived,
            },
        )

    # ---- 记忆流转 ----

    async def _flow_memory(
        self,
        input_data: MemoryInput,
        context: AgentExecutionContext,
    ) -> AgentResult:
        """
        记忆流转

        - stm_to_ltm: 短期记忆 -> 长期记忆
        - wm_to_ltm: 工作记忆 -> 长期记忆
        - ltm_to_wm: 长期记忆 -> 工作记忆
        """
        ms = await self._get_memory_system()

        incident_id = input_data.incident_id or context.incident_id
        flow_type = input_data.flow_type or "stm_to_ltm"

        if flow_type == "stm_to_ltm":
            count = await ms.flow_stm_to_ltm()
            return AgentResult.success_result(
                agent_name=self.get_name(),
                output_data={
                    "operation": "flow",
                    "flow_type": "stm_to_ltm",
                    "count": count,
                },
            )

        elif flow_type == "wm_to_ltm":
            if not incident_id:
                return AgentResult.failure_result(
                    agent_name=self.get_name(),
                    error_message="incident_id required for wm_to_ltm flow",
                )
            count = await ms.flow_wm_to_ltm(incident_id)
            return AgentResult.success_result(
                agent_name=self.get_name(),
                output_data={
                    "operation": "flow",
                    "flow_type": "wm_to_ltm",
                    "incident_id": incident_id,
                    "count": count,
                },
            )

        elif flow_type == "ltm_to_wm":
            if not input_data.query:
                return AgentResult.failure_result(
                    agent_name=self.get_name(),
                    error_message="query required for ltm_to_wm flow",
                )
            entries = await ms.flow_ltm_to_wm(
                query=input_data.query,
                incident_id=incident_id or "default",
                top_k=input_data.top_k,
            )
            return AgentResult.success_result(
                agent_name=self.get_name(),
                output_data={
                    "operation": "flow",
                    "flow_type": "ltm_to_wm",
                    "incident_id": incident_id,
                    "count": len(entries),
                    "entries": [
                        {"memory_id": e.memory_id, "content": e.content[:200]}
                        for e in entries
                    ],
                },
            )

        else:
            return AgentResult.failure_result(
                agent_name=self.get_name(),
                error_message=f"Unknown flow_type: {flow_type}",
            )

    # ---- 上下文 ----

    async def _get_context(
        self,
        input_data: MemoryInput,
        context: AgentExecutionContext,
    ) -> AgentResult:
        """组装记忆上下文"""
        ms = await self._get_memory_system()

        context_str = await ms.get_context(
            query=input_data.query,
            session_id=input_data.session_id,
            incident_id=input_data.incident_id or context.incident_id,
            max_tokens=input_data.extra.get("max_tokens", 4000),
        )

        return AgentResult.success_result(
            agent_name=self.get_name(),
            output_data={
                "operation": "get_context",
                "query": input_data.query,
                "session_id": input_data.session_id,
                "incident_id": input_data.incident_id or context.incident_id,
                "context": context_str,
                "context_length": len(context_str),
            },
        )

    # ---- 便捷方法 ----

    async def auto_record(
        self,
        content: str,
        incident_id: str,
        agent_name: str,
        memory_type: MemoryType = MemoryType.OBSERVATION,
        importance: float = 0.5,
        tags: list[str] | None = None,
    ) -> MemoryEntry:
        """
        自动记录关键信息

        在处理过程中自动记录重要信息到记忆系统。
        """
        ms = await self._get_memory_system()

        entry = await ms.store(
            content=content,
            memory_type=memory_type,
            source_agent=agent_name,
            source_incident_id=incident_id,
            importance=importance,
            tags=tags or ["auto_recorded"],
        )

        logger.debug(
            "Auto-recorded memory",
            memory_id=entry.memory_id,
            incident_id=incident_id,
            agent=agent_name,
        )
        return entry

    async def get_similar_incidents(
        self,
        service: str,
        symptoms: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """
        搜索相似故障

        基于长期记忆搜索历史相似故障。
        """
        ms = await self._get_memory_system()

        query_text = f"{service} {symptoms}"
        results = await ms.search(
            query_text=query_text,
            top_k=top_k,
            memory_type=MemoryType.EPISODIC,
        )

        return [
            {
                "memory_id": e.memory_id,
                "content": e.content[:500],
                "importance": e.importance_score,
                "source_incident": e.source_incident_id,
                "tags": e.tags,
            }
            for e in results.results
        ]

    # ---- 健康检查 ----

    async def health_check(self) -> dict[str, Any]:
        """Agent 健康检查"""
        base_health = {
            "agent_name": self.get_name(),
            "status": "ok",
            "is_healthy": True,
        }

        try:
            ms = await self._get_memory_system()
            stats = await ms.get_stats()
            base_health["memory_stats"] = stats
            base_health["memory_system_ready"] = True
        except Exception as e:
            base_health["memory_system_ready"] = False
            base_health["memory_error"] = str(e)

        return base_health
