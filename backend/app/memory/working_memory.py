"""
AIOps Agent Platform - Working Memory

工作记忆实现，有限的临时存储，TTL 过期机制。
融合 MemGPT 工作记忆设计理念：
- 与特定incident绑定
- 所有Agent可读写（Agent间共享的任务上下文）
- 临时计算结果缓存
- 处理完成后归档到长期记忆
- 存储结构: Dict[str, Any] 的键值对，支持嵌套
"""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from typing import Any

from app.models.memory import MemoryEntry, MemoryLevel, MemoryType, WorkingMemorySlot
from app.utils.logging import get_logger

logger = get_logger(__name__)


class IncidentWorkingMemory:
    """
    故障工作记忆

    与特定incident绑定的工作记忆空间。
    所有Agent可在此读写，实现Agent间上下文共享。
    """

    def __init__(self, incident_id: str, max_slots: int = 50) -> None:
        self.incident_id = incident_id
        self._max_slots = max_slots
        # 核心存储: name -> WorkingMemorySlot
        self._slots: dict[str, WorkingMemorySlot] = {}
        # 嵌套数据存储: 支持Dict[str, Any]的复杂结构
        self._data_store: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._created_at = datetime.now(timezone.utc)
        self._updated_at = datetime.now(timezone.utc)

    # ---- 核心CRUD ----

    async def set(
        self,
        key: str,
        value: Any,
        ttl_seconds: int = 3600,
        priority: int = 5,
    ) -> WorkingMemorySlot:
        """
        设置键值对

        Args:
            key: 键名
            value: 值（任意类型，支持嵌套dict/list）
            ttl_seconds: 生存时间
            priority: 优先级（1-10，10最高）

        Returns:
            WorkingMemorySlot: 创建的槽位
        """
        async with self._lock:
            self._cleanup_expired()

            # 如果已满，淘汰优先级最低的
            if len(self._slots) >= self._max_slots and key not in self._slots:
                self._evict_lowest_priority()

            slot = WorkingMemorySlot(
                name=key,
                content=value,
                priority=priority,
                ttl_seconds=ttl_seconds,
            )
            self._slots[key] = slot
            self._data_store[key] = value
            self._updated_at = datetime.now(timezone.utc)

        logger.debug(
            "Working memory set",
            incident_id=self.incident_id,
            key=key,
            priority=priority,
        )
        return slot

    async def get(self, key: str, default: Any = None) -> Any:
        """
        获取值

        Args:
            key: 键名
            default: 默认值

        Returns:
            值或默认值
        """
        async with self._lock:
            self._cleanup_expired()
            slot = self._slots.get(key)
            if slot and not slot.is_expired:
                return slot.content
            return self._data_store.get(key, default)

    async def get_slot(self, key: str) -> WorkingMemorySlot | None:
        """获取槽位对象（包含元数据）"""
        async with self._lock:
            self._cleanup_expired()
            slot = self._slots.get(key)
            if slot and not slot.is_expired:
                return slot
            return None

    async def update(self, key: str, value: Any) -> bool:
        """
        更新值

        Args:
            key: 键名
            value: 新值

        Returns:
            bool: 是否成功
        """
        async with self._lock:
            self._cleanup_expired()
            slot = self._slots.get(key)
            if slot and not slot.is_expired:
                slot.content = value
                self._data_store[key] = value
                self._updated_at = datetime.now(timezone.utc)
                return True
            # 如果槽位不存在或已过期，创建新的
            if key in self._data_store:
                self._data_store[key] = value
                return True
            return False

    async def delete(self, key: str) -> bool:
        """删除键"""
        async with self._lock:
            removed = False
            if key in self._slots:
                del self._slots[key]
                removed = True
            if key in self._data_store:
                del self._data_store[key]
                removed = True
            return removed

    async def get_all(self) -> dict[str, Any]:
        """
        获取所有有效数据

        Returns:
            dict: 所有键值对的深拷贝
        """
        async with self._lock:
            self._cleanup_expired()
            return copy.deepcopy(self._data_store)

    async def clear(self) -> None:
        """清空所有数据"""
        async with self._lock:
            self._slots.clear()
            self._data_store.clear()
            self._updated_at = datetime.now(timezone.utc)
        logger.info(f"Working memory cleared for incident {self.incident_id}")

    # ---- 嵌套操作 ----

    async def get_nested(self, path: str, default: Any = None) -> Any:
        """
        通过路径获取嵌套值

        Args:
            path: 点分隔路径，如 "root_cause.analysis.result"
            default: 默认值

        Returns:
            值或默认值
        """
        keys = path.split(".")
        current = await self.get(keys[0])

        for key in keys[1:]:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default
        return current

    async def set_nested(self, path: str, value: Any) -> None:
        """
        通过路径设置嵌套值

        Args:
            path: 点分隔路径
            value: 值
        """
        keys = path.split(".")
        if len(keys) == 1:
            await self.set(keys[0], value)
            return

        root = await self.get(keys[0])
        if not isinstance(root, dict):
            root = {}

        current = root
        for key in keys[1:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]

        current[keys[-1]] = value
        await self.set(keys[0], root)

    # ---- 归档 ----

    async def archive(self, long_term_store: Any) -> list[MemoryEntry]:
        """
        归档到长期记忆

        将所有有效数据转换为MemoryEntry存入长期记忆。

        Args:
            long_term_store: 长期记忆存储（有store方法）

        Returns:
            list[MemoryEntry]: 归档的记忆条目
        """
        async with self._lock:
            self._cleanup_expired()
            entries: list[MemoryEntry] = []

            for key, value in self._data_store.items():
                content = f"[Incident {self.incident_id}] {key}: {str(value)[:1000]}"

                entry = MemoryEntry(
                    content=content,
                    memory_type=MemoryType.EPISODIC,
                    memory_level=MemoryLevel.LONG_TERM,
                    source_incident_id=self.incident_id,
                    importance_score=0.7,  # incident数据默认较重要
                    tags=["working_memory", "archived", key],
                    metadata={
                        "original_key": key,
                        "archived_at": datetime.now(timezone.utc).isoformat(),
                        "incident_id": self.incident_id,
                    },
                )

                try:
                    await long_term_store.store(entry)
                    entries.append(entry)
                except Exception as e:
                    logger.error(f"Failed to archive working memory key {key}: {e}")

        logger.info(
            f"Archived {len(entries)} working memory entries for incident {self.incident_id}"
        )

        # 清空
        await self.clear()
        return entries

    # ---- 内部管理 ----

    def _cleanup_expired(self) -> int:
        """清理过期槽位"""
        expired = [
            name for name, slot in self._slots.items() if slot.is_expired
        ]
        for name in expired:
            del self._slots[name]
            if name in self._data_store:
                del self._data_store[name]
        return len(expired)

    def _evict_lowest_priority(self) -> None:
        """淘汰优先级最低的槽位"""
        if not self._slots:
            return
        lowest = min(self._slots.values(), key=lambda s: s.priority)
        del self._slots[lowest.name]
        if lowest.name in self._data_store:
            del self._data_store[lowest.name]
        logger.debug(
            "Evicted lowest priority slot",
            incident_id=self.incident_id,
            name=lowest.name,
        )

    def get_stats(self) -> dict[str, Any]:
        """获取统计"""
        self._cleanup_expired()
        return {
            "incident_id": self.incident_id,
            "total_slots": len(self._slots),
            "max_slots": self._max_slots,
            "created_at": self._created_at.isoformat(),
            "updated_at": self._updated_at.isoformat(),
            "slot_names": list(self._slots.keys()),
        }


class WorkingMemory:
    """
    工作记忆管理器

    管理所有incident的工作记忆空间。
    每个incident有独立的工作记忆，Agent间可共享。
    """

    def __init__(self, max_slots_per_incident: int = 50) -> None:
        self._max_slots = max_slots_per_incident
        # incident_id -> IncidentWorkingMemory
        self._incidents: dict[str, IncidentWorkingMemory] = {}
        self._lock = asyncio.Lock()

    # ---- 故障空间管理 ----

    def _get_or_create(self, incident_id: str) -> IncidentWorkingMemory:
        """获取或创建故障工作记忆空间"""
        if incident_id not in self._incidents:
            self._incidents[incident_id] = IncidentWorkingMemory(
                incident_id=incident_id,
                max_slots=self._max_slots,
            )
        return self._incidents[incident_id]

    async def get_incidence_wm(self, incident_id: str) -> IncidentWorkingMemory:
        """获取故障工作记忆空间"""
        return self._get_or_create(incident_id)

    async def remove_incident(self, incident_id: str) -> bool:
        """移除故障工作记忆空间"""
        if incident_id in self._incidents:
            del self._incidents[incident_id]
            return True
        return False

    # ---- 便捷操作（自动处理incident_id） ----

    async def set(
        self,
        key: str,
        value: Any,
        incident_id: str = "default",
        ttl_seconds: int = 3600,
        priority: int = 5,
    ) -> WorkingMemorySlot:
        """设置键值对"""
        wm = self._get_or_create(incident_id)
        return await wm.set(key, value, ttl_seconds, priority)

    async def get(
        self, key: str, incident_id: str = "default", default: Any = None
    ) -> Any:
        """获取值"""
        wm = self._get_or_create(incident_id)
        return await wm.get(key, default)

    async def update(
        self, key: str, value: Any, incident_id: str = "default"
    ) -> bool:
        """更新值"""
        wm = self._get_or_create(incident_id)
        return await wm.update(key, value)

    async def delete(self, key: str, incident_id: str = "default") -> bool:
        """删除键"""
        wm = self._get_or_create(incident_id)
        return await wm.delete(key)

    async def get_all(self, incident_id: str = "default") -> dict[str, Any]:
        """获取所有数据"""
        wm = self._get_or_create(incident_id)
        return await wm.get_all()

    async def clear(self, incident_id: str = "") -> None:
        """
        清空工作记忆

        Args:
            incident_id: 指定故障（为空时清空全部）
        """
        if incident_id:
            wm = self._get_or_create(incident_id)
            await wm.clear()
        else:
            for wm in self._incidents.values():
                await wm.clear()
            self._incidents.clear()
            logger.info("All working memory cleared")

    async def get_nested(
        self, path: str, incident_id: str = "default", default: Any = None
    ) -> Any:
        """获取嵌套值"""
        wm = self._get_or_create(incident_id)
        return await wm.get_nested(path, default)

    async def set_nested(
        self, path: str, value: Any, incident_id: str = "default"
    ) -> None:
        """设置嵌套值"""
        wm = self._get_or_create(incident_id)
        await wm.set_nested(path, value)

    # ---- 归档 ----

    async def archive(
        self, incident_id: str, long_term_store: Any
    ) -> list[MemoryEntry]:
        """
        归档故障工作记忆到长期记忆

        Args:
            incident_id: 故障ID
            long_term_store: 长期记忆存储

        Returns:
            list[MemoryEntry]: 归档的记忆条目
        """
        wm = self._incidents.get(incident_id)
        if wm is None:
            return []

        entries = await wm.archive(long_term_store)
        self._incidents.pop(incident_id, None)
        return entries

    # ---- 兼容旧接口 ----

    def create_slot(
        self,
        name: str,
        content: Any,
        ttl_seconds: int = 300,
        priority: int = 5,
    ) -> WorkingMemorySlot:
        """
        创建工作记忆槽位（同步兼容接口）

        使用default incident空间。
        """
        wm = self._get_or_create("default")
        # 同步执行
        import asyncio

        try:
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(
                wm.set(name, content, ttl_seconds, priority)
            )
        except RuntimeError:
            # 无事件循环时创建新的事件循环
            return asyncio.run(wm.set(name, content, ttl_seconds, priority))

    def get_slot(self, name: str) -> WorkingMemorySlot | None:
        """获取槽位（同步兼容接口）"""
        wm = self._incidents.get("default")
        if wm is None:
            return None

        import asyncio

        try:
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(wm.get_slot(name))
        except RuntimeError:
            return asyncio.run(wm.get_slot(name))

    def update_slot(self, name: str, content: Any) -> WorkingMemorySlot | None:
        """更新槽位（同步兼容接口）"""
        wm = self._incidents.get("default")
        if wm is None:
            return None

        import asyncio

        try:
            loop = asyncio.get_event_loop()
            updated = loop.run_until_complete(wm.update(name, content))
            if updated:
                return loop.run_until_complete(wm.get_slot(name))
            return None
        except RuntimeError:
            updated = asyncio.run(wm.update(name, content))
            if updated:
                return asyncio.run(wm.get_slot(name))
            return None

    def remove_slot(self, name: str) -> bool:
        """移除槽位（同步兼容接口）"""
        wm = self._incidents.get("default")
        if wm is None:
            return False

        import asyncio

        try:
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(wm.delete(name))
        except RuntimeError:
            return asyncio.run(wm.delete(name))

    def list_slots(self) -> list[str]:
        """列出所有槽位"""
        wm = self._incidents.get("default")
        if wm is None:
            return []
        wm._cleanup_expired()
        return list(wm._slots.keys())

    # ---- 统计 ----

    def get_stats(self) -> dict[str, Any]:
        """获取统计"""
        return {
            "total_incidents": len(self._incidents),
            "max_slots_per_incident": self._max_slots,
            "incident_ids": list(self._incidents.keys()),
            "incident_details": {
                iid: wm.get_stats() for iid, wm in self._incidents.items()
            },
        }
