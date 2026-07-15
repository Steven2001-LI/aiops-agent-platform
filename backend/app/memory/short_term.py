"""
AIOps Agent Platform - Short Term Memory

短期记忆实现，基于内存的高效存储，容量有限。
融合 mem0 设计理念：
- 会话级上下文管理
- 滑动窗口机制（默认最近10条/会话）
- TTL过期机制（默认30分钟）
- 快速存取 O(1)
- 按session隔离
- asyncio.Task 定期清理过期记忆
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from datetime import datetime, timedelta, timezone
from typing import Any

from app.memory.storage import InMemoryStorage
from app.models.memory import MemoryEntry, MemoryLevel, MemoryQuery, MemoryQueryResult, MemoryType
from app.utils.logging import get_logger

logger = get_logger(__name__)


class SessionMemoryWindow:
    """
    会话记忆窗口

    每个会话独立的滑动窗口，管理该会话内的短期记忆。
    """

    def __init__(self, session_id: str, max_size: int = 10, ttl_minutes: float = 30.0) -> None:
        self.session_id = session_id
        self.max_size = max_size
        self.ttl = timedelta(minutes=ttl_minutes)
        # 使用OrderedDict实现滑动窗口: memory_id -> (entry, timestamp)
        self._entries: OrderedDict[str, tuple[MemoryEntry, datetime]] = OrderedDict()

    def add(self, entry: MemoryEntry) -> MemoryEntry | None:
        """
        添加记忆到窗口

        Returns:
            被淘汰的条目（如果窗口已满），否则None
        """
        now = datetime.now(timezone.utc)
        evicted = None

        # 如果窗口已满，淘汰最旧的
        if len(self._entries) >= self.max_size:
            oldest_id, (oldest_entry, _) = self._entries.popitem(last=False)
            evicted = oldest_entry
            logger.debug(
                "Sliding window evicted oldest entry",
                session_id=self.session_id,
                memory_id=oldest_id,
            )

        # 设置过期时间
        entry.expires_at = now + self.ttl
        self._entries[entry.memory_id] = (entry, now)

        return evicted

    def get_recent(self, n: int = 10) -> list[MemoryEntry]:
        """获取最近n条记忆（从旧到新排序）"""
        self._cleanup_expired()
        entries = [entry for entry, _ in self._entries.values()]
        return entries[-n:]

    def get_context(self) -> str:
        """
        获取会话上下文字符串

        将窗口内所有记忆按时间顺序拼接为上下文。
        """
        self._cleanup_expired()
        entries = [entry for entry, _ in self._entries.values()]
        if not entries:
            return ""

        parts = []
        for entry in entries:
            parts.append(f"[{entry.memory_type.value}] {entry.content}")
        return "\n".join(parts)

    def clear(self) -> list[MemoryEntry]:
        """清空窗口，返回所有条目"""
        all_entries = [entry for entry, _ in self._entries.values()]
        self._entries.clear()
        return all_entries

    def _cleanup_expired(self) -> int:
        """清理过期条目，返回清理数量"""
        now = datetime.now(timezone.utc)
        expired_ids = [
            mid
            for mid, (entry, _) in self._entries.items()
            if entry.expires_at and now > entry.expires_at
        ]
        for mid in expired_ids:
            del self._entries[mid]
        return len(expired_ids)

    def __len__(self) -> int:
        self._cleanup_expired()
        return len(self._entries)


class ShortTermMemory:
    """
    短期记忆管理器

    会话级上下文管理，使用滑动窗口 + TTL过期机制。
    所有Agent共享的短期记忆层，按会话隔离。

    特点:
    - 快速存取 O(1)
    - 滑动窗口淘汰（每会话独立）
    - TTL自动过期
    - asyncio.Task定期清理
    - 支持关键词检索

    Attributes:
        max_items: 全局最大条目数
        window_size: 每会话滑动窗口大小
        ttl_minutes: 记忆存活时间（分钟）
    """

    def __init__(
        self,
        max_items: int = 100,
        window_size: int = 10,
        ttl_minutes: float = 30.0,
        cleanup_interval_seconds: float = 60.0,
    ) -> None:
        self._max_items = max_items
        self._window_size = window_size
        self._ttl_minutes = ttl_minutes
        self._cleanup_interval = cleanup_interval_seconds

        # 存储层
        self._storage = InMemoryStorage()

        # 会话窗口: session_id -> SessionMemoryWindow
        self._sessions: dict[str, SessionMemoryWindow] = {}

        # 全局消息队列（按时间顺序，用于跨会话检索）
        self._message_queue: deque[MemoryEntry] = deque(maxlen=max_items)

        # 线程安全
        self._lock = asyncio.Lock()

        # 清理任务
        self._cleanup_task: asyncio.Task[None] | None = None
        self._running = False

    # ---- 生命周期 ----

    async def start(self) -> None:
        """启动定期清理任务"""
        self._running = True
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(
                self._cleanup_loop(), name="stm_cleanup"
            )
            logger.info("ShortTermMemory cleanup task started")

    async def stop(self) -> None:
        """停止清理任务"""
        self._running = False
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            logger.info("ShortTermMemory cleanup task stopped")

    async def _cleanup_loop(self) -> None:
        """定期清理过期记忆的后台任务"""
        while self._running:
            try:
                await asyncio.sleep(self._cleanup_interval)
                await self._cleanup_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")
                await asyncio.sleep(self._cleanup_interval)

    async def _cleanup_expired(self) -> int:
        """清理所有过期记忆"""
        removed = 0
        async with self._lock:
            # 清理存储层中的过期条目
            expired_ids = [
                mid
                for mid, entry in self._storage._entries.items()
                if entry.is_expired
            ]
            for mid in expired_ids:
                await self._storage.delete(mid)
                removed += 1

            # 清理空会话窗口
            empty_sessions = [
                sid for sid, window in self._sessions.items() if len(window) == 0
            ]
            for sid in empty_sessions:
                del self._sessions[sid]

            # 清理消息队列中的过期条目
            self._message_queue = deque(
                [m for m in self._message_queue if not m.is_expired],
                maxlen=self._max_items,
            )

        if removed > 0:
            logger.debug(f"Cleaned up {removed} expired short-term memories")
        return removed

    # ---- 核心操作 ----

    async def store(self, entry: MemoryEntry) -> None:
        """
        存储记忆到短期记忆

        同时存入全局存储和对应会话窗口。
        """
        entry.memory_level = MemoryLevel.SHORT_TERM

        # 设置过期时间
        if entry.expires_at is None:
            entry.expires_at = datetime.now(timezone.utc) + timedelta(
                minutes=self._ttl_minutes
            )

        async with self._lock:
            # 存入全局存储
            await self._storage.add(entry)

            # 存入会话窗口
            session_id = entry.metadata.get("session_id", "default")
            window = self._get_or_create_window(session_id)
            evicted = window.add(entry)

            # 如果被滑动窗口淘汰，从全局存储也删除
            if evicted is not None:
                await self._storage.delete(evicted.memory_id)

            # 加入全局消息队列
            self._message_queue.append(entry)

        logger.debug(
            "Stored in short-term memory",
            memory_id=entry.memory_id,
            session_id=session_id,
        )

    def _get_or_create_window(self, session_id: str) -> SessionMemoryWindow:
        """获取或创建会话窗口"""
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionMemoryWindow(
                session_id=session_id,
                max_size=self._window_size,
                ttl_minutes=self._ttl_minutes,
            )
        return self._sessions[session_id]

    # ---- 消息接口（更友好的添加方式） ----

    async def add_message(
        self,
        content: str,
        session_id: str = "default",
        agent_id: str = "",
        memory_type: MemoryType = MemoryType.OBSERVATION,
        importance: float = 0.5,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryEntry:
        """
        添加消息到短期记忆（便捷方法）

        Args:
            content: 消息内容
            session_id: 会话ID
            agent_id: Agent ID
            memory_type: 记忆类型
            importance: 重要性
            tags: 标签
            metadata: 元数据

        Returns:
            MemoryEntry: 创建的记忆条目
        """
        meta = metadata or {}
        meta["session_id"] = session_id

        entry = MemoryEntry(
            content=content,
            memory_type=memory_type,
            memory_level=MemoryLevel.SHORT_TERM,
            source_agent=agent_id,
            importance_score=importance,
            tags=tags or [],
            metadata=meta,
        )
        await self.store(entry)
        return entry

    async def get_context(self, session_id: str = "default") -> str:
        """
        获取会话上下文

        返回指定会话的滑动窗口内所有记忆拼接的上下文字符串。

        Args:
            session_id: 会话ID

        Returns:
            str: 上下文字符串
        """
        async with self._lock:
            window = self._sessions.get(session_id)
            if window is None:
                return ""
            return window.get_context()

    async def get_recent(
        self, session_id: str = "default", n: int = 10
    ) -> list[MemoryEntry]:
        """
        获取最近n条记忆

        Args:
            session_id: 会话ID（为空时返回全局最近）
            n: 数量

        Returns:
            list[MemoryEntry]: 记忆列表
        """
        async with self._lock:
            if session_id:
                window = self._sessions.get(session_id)
                if window is None:
                    return []
                return window.get_recent(n)
            else:
                # 返回全局最近
                return list(self._message_queue)[-n:]

    async def retrieve(self, query: MemoryQuery) -> MemoryQueryResult:
        """
        检索短期记忆

        基于关键词匹配和最近性排序。

        Args:
            query: 查询

        Returns:
            MemoryQueryResult: 结果
        """
        query_lower = query.query_text.lower()
        results: list[tuple[MemoryEntry, float]] = []

        async with self._lock:
            entries = list(self._storage._entries.values())

            for entry in entries:
                score = self._compute_relevance(entry, query_lower)
                if score > 0:
                    entry.touch()
                    results.append((entry, score))

            # 按分数排序
            results.sort(key=lambda x: x[1], reverse=True)

        return MemoryQueryResult(
            query_id=query.query_id,
            results=[r[0] for r in results[: query.top_k]],
            similarities=[r[1] for r in results[: query.top_k]],
            total_found=len(results),
        )

    def _compute_relevance(self, entry: MemoryEntry, query_lower: str) -> float:
        """
        计算相关性得分

        综合内容匹配、标签匹配、重要性、最近性。
        """
        score = 0.0
        content_lower = entry.content.lower()

        # 内容关键词匹配
        if query_lower in content_lower:
            score += 0.5
        # 分词匹配
        query_words = query_lower.split()
        match_count = sum(1 for w in query_words if w in content_lower)
        score += 0.2 * (match_count / max(len(query_words), 1))

        # 标签匹配
        for tag in entry.tags:
            if query_lower in tag.lower():
                score += 0.3
                break

        # 重要性加成
        score += entry.importance_score * 0.2

        # 最近性加成（越新越重要）
        if entry.created_at:
            hours_old = (datetime.now(timezone.utc) - entry.created_at).total_seconds() / 3600
            recency_boost = max(0, 0.15 * (1 - hours_old / 24))
            score += recency_boost

        return min(score, 1.0)

    async def clear(self, session_id: str = "") -> int:
        """
        清空短期记忆

        Args:
            session_id: 指定会话（为空时清空全部）

        Returns:
            int: 清理的条目数
        """
        async with self._lock:
            if session_id:
                window = self._sessions.pop(session_id, None)
                if window:
                    entries = window.clear()
                    for entry in entries:
                        await self._storage.delete(entry.memory_id)
                    return len(entries)
                return 0
            else:
                count = len(self._storage._entries)
                await self._storage.clear()
                self._sessions.clear()
                self._message_queue.clear()
                return count

    # ---- 维护操作 ----

    async def consolidate(self) -> dict[str, Any]:
        """
        整理短期记忆

        清理过期条目，返回统计。
        """
        expired_removed = await self._cleanup_expired()

        stats = await self.get_stats()
        stats["expired_removed"] = expired_removed

        return stats

    async def get_stats(self) -> dict[str, Any]:
        """获取统计"""
        async with self._lock:
            storage_stats = await self._storage.get_stats()
            return {
                "type": "short_term",
                "total_entries": len(self._storage._entries),
                "max_items": self._max_items,
                "window_size": self._window_size,
                "ttl_minutes": self._ttl_minutes,
                "active_sessions": len(self._sessions),
                "message_queue_size": len(self._message_queue),
                "usage_percent": (
                    len(self._storage._entries) / self._max_items * 100
                    if self._max_items > 0
                    else 0
                ),
                **storage_stats,
            }
