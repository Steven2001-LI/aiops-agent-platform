"""
AIOps Agent Platform - Memory System Core

记忆系统核心，协调短期记忆、长期记忆和工作记忆。
融合 mem0 和 MemGPT 设计理念：
- 统一记忆管理接口
- 记忆流转机制（短期->长期，工作->长期，长期->工作）
- 记忆增强（自动提取关键信息，生成摘要）
- 记忆上下文组装（为LLM调用组装相关记忆）
- 单例模式支持
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import MemoryConfig, get_config
from app.memory.long_term import LongTermMemory
from app.memory.short_term import ShortTermMemory
from app.memory.storage import ChromaDBStorage, InMemoryStorage
from app.memory.working_memory import WorkingMemory
from app.models.memory import (
    MemoryEntry,
    MemoryLevel,
    MemoryQuery,
    MemoryQueryResult,
    MemoryType,
    WorkingMemorySlot,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Memory Enhancement
# ---------------------------------------------------------------------------


class MemoryEnhancer:
    """
    记忆增强器

    自动提取关键信息，生成摘要，提升记忆质量。
    """

    MAX_SUMMARY_LENGTH = 200

    @staticmethod
    def generate_summary(content: str) -> str:
        """
        生成内容摘要

        简单的提取式摘要（取前N个字符 + 关键句）。
        可替换为LLM-based摘要生成。
        """
        if len(content) <= MemoryEnhancer.MAX_SUMMARY_LENGTH:
            return content

        # 提取第一句和最后一句作为摘要
        sentences = content.split("。")
        if len(sentences) >= 2:
            summary = sentences[0] + "。" + sentences[-1][:100]
        else:
            summary = content[: MemoryEnhancer.MAX_SUMMARY_LENGTH] + "..."

        return summary[: MemoryEnhancer.MAX_SUMMARY_LENGTH]

    @staticmethod
    def extract_keywords(content: str, max_keywords: int = 5) -> list[str]:
        """
        提取关键词

        简单的TF-based关键词提取。
        """
        import re

        # 分词（简化版：按非字母数字字符分割）
        words = re.findall(r"\b[a-zA-Z]{3,}\b", content.lower())

        # 停用词
        stopwords = {
            "the", "and", "for", "are", "but", "not", "you", "all", "can",
            "had", "her", "was", "one", "our", "out", "day", "get", "has",
            "him", "his", "how", "its", "may", "new", "now", "old", "see",
            "two", "way", "who", "boy", "did", "she", "use", "her", "than",
            "them", "well", "were", "with", "have", "from", "they", "know",
            "want", "been", "good", "much", "some", "time", "very", "when",
            "come", "here", "just", "like", "long", "make", "many", "over",
            "such", "take", "that", "this", "will", "back", "call", "came",
            "each", "find", "into", "look", "made", "most", "only", "said",
            "sure", "upon", "what", "year", "your", "about", "after", "could",
            "first", "never", "other", "right", "think", "where", "being",
            "every", "great", "might", "shall", "still", "those", "while",
            "which", "would", "there", "their", "these", "should", "before",
            "people", "through", "during", "information", "error", "service",
        }

        # 统计词频
        word_counts: dict[str, int] = {}
        for w in words:
            if w not in stopwords and not w.isdigit():
                word_counts[w] = word_counts.get(w, 0) + 1

        # 返回高频词
        sorted_words = sorted(word_counts.items(), key=lambda x: x[1], reverse=True)
        return [w for w, _ in sorted_words[:max_keywords]]

    @staticmethod
    def auto_tag(entry: MemoryEntry) -> list[str]:
        """
        自动打标签

        基于内容自动推断标签。
        """
        tags = set(entry.tags)
        content_lower = entry.content.lower()

        # AIOps相关标签
        tag_rules = {
            "incident": ["incident", "故障", "incident", "outage", "down"],
            "alert": ["alert", "告警", "alarm", "warning", "trigger"],
            "metric": ["metric", "指标", "cpu", "memory", "disk", "latency", "throughput"],
            "log": ["log", "日志", "error log", "stack trace"],
            "deployment": ["deploy", "发布", "deployment", "rollout", "release"],
            "config": ["config", "配置", "configuration", "setting"],
            "network": ["network", "网络", "connection", "timeout", "dns"],
            "database": ["database", "数据库", "db", "sql", "query", "connection pool"],
            "kubernetes": ["k8s", "kubernetes", "pod", "container", "namespace", "deployment"],
            "performance": ["performance", "性能", "slow", "bottleneck", "optimize"],
            "security": ["security", "安全", "vulnerability", "attack", "breach"],
            "recovery": ["recovery", "恢复", "restore", "rollback", "fix", "resolve"],
            "root_cause": ["root cause", "根因", "caused by", "due to", "because"],
            "rca": ["rca", "根因分析", "root cause analysis"],
            "monitoring": ["monitor", "监控", "dashboard", "grafana", "prometheus"],
        }

        for tag, keywords in tag_rules.items():
            if any(kw in content_lower for kw in keywords):
                tags.add(tag)

        return list(tags)

    @staticmethod
    def enhance(entry: MemoryEntry) -> MemoryEntry:
        """
        增强记忆条目

        自动摘要、关键词提取、标签补全。
        """
        if not entry.summary:
            entry.summary = MemoryEnhancer.generate_summary(entry.content)

        keywords = MemoryEnhancer.extract_keywords(entry.content)
        auto_tags = MemoryEnhancer.auto_tag(entry)
        entry.tags = list(set(entry.tags + keywords + auto_tags))

        return entry


# ---------------------------------------------------------------------------
# Memory System
# ---------------------------------------------------------------------------


class MemorySystem:
    """
    记忆系统

    统一管理三种记忆层级：
    - 短期记忆：临时存储，容量有限，快速访问
    - 长期记忆：持久存储，大容量，向量索引
    - 工作记忆：当前处理中的信息，TTL过期

    记忆流转机制:
    - 短期记忆 -> 长期记忆：重要记忆自动归档
    - 工作记忆 -> 长期记忆：incident完成后归档
    - 长期记忆 -> 工作记忆：检索相关历史知识

    记忆增强:
    - 自动提取关键信息
    - 生成摘要
    - 自动标签

    上下文组装:
    - 为LLM调用组装相关记忆
    - 优先级排序
    - Token预算管理
    """

    # 单例实例
    _instance: MemorySystem | None = None
    _instance_lock = asyncio.Lock()

    def __init__(self, config: MemoryConfig | None = None) -> None:
        self._config = config or get_config().memory
        self._enhancer = MemoryEnhancer()

        # 初始化各层级记忆
        self._short_term = ShortTermMemory(
            max_items=self._config.short_term_max_items,
            window_size=10,
            ttl_minutes=30.0,
        )
        self._long_term = LongTermMemory(
            similarity_threshold=self._config.similarity_threshold,
            retention_days=self._config.retention_days,
        )
        self._working = WorkingMemory()

        # 记忆流转锁
        self._flow_lock = asyncio.Lock()

        logger.info(
            "MemorySystem initialized",
            short_term_max=self._config.short_term_max_items,
            similarity_threshold=self._config.similarity_threshold,
            retention_days=self._config.retention_days,
        )

    @classmethod
    async def get_instance(cls, config: MemoryConfig | None = None) -> MemorySystem:
        """获取单例实例（线程安全）"""
        if cls._instance is None:
            async with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls(config)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例（主要用于测试）"""
        cls._instance = None

    # ---- 核心CRUD ----

    async def store(
        self,
        content: str,
        memory_type: MemoryType = MemoryType.OBSERVATION,
        source_agent: str = "",
        source_incident_id: str = "",
        importance: float = 0.5,
        importance_score: float | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        session_id: str = "default",
    ) -> MemoryEntry:
        """
        存储记忆

        同时写入短期记忆和长期记忆（重要记忆）。
        自动增强（摘要、关键词、标签）。

        Args:
            content: 记忆内容
            memory_type: 记忆类型
            source_agent: 来源 Agent
            source_incident_id: 关联故障ID
            importance: 重要性 (0-1)
            tags: 标签
            metadata: 元数据
            session_id: 会话ID

        Returns:
            MemoryEntry: 创建的记忆条目
        """
        # 如果传入了 importance_score 参数，优先使用它
        if importance_score is not None:
            importance = importance_score

        meta = metadata or {}
        meta["session_id"] = session_id

        entry = MemoryEntry(
            memory_type=memory_type,
            memory_level=MemoryLevel.SHORT_TERM,
            content=content,
            source_agent=source_agent,
            source_incident_id=source_incident_id,
            importance_score=importance,
            tags=tags or [],
            metadata=meta,
        )

        # 记忆增强
        self._enhancer.enhance(entry)

        # 存入短期记忆
        await self._short_term.store(entry)

        # 重要记忆异步存入长期记忆
        if importance >= 0.5:
            await self._long_term.store(entry)
            logger.debug(
                "Important memory synced to long-term",
                memory_id=entry.memory_id,
                importance=importance,
            )

        logger.info(
            "Memory stored",
            memory_id=entry.memory_id,
            memory_type=memory_type.value,
            importance=importance,
        )

        return entry

    async def retrieve(self, query: MemoryQuery) -> MemoryQueryResult:
        """
        检索记忆

        同时从短期记忆和长期记忆检索，合并结果。

        Args:
            query: 查询

        Returns:
            MemoryQueryResult: 查询结果
        """
        # 从两级记忆并行检索
        st_task = asyncio.create_task(self._short_term.retrieve(query))
        lt_task = asyncio.create_task(self._long_term.retrieve(query))

        short_term_results = await st_task
        long_term_results = await lt_task

        # 合并并去重
        all_results: list[MemoryEntry] = []
        seen_ids: set[str] = set()

        for entry in short_term_results.results + long_term_results.results:
            if entry.memory_id not in seen_ids:
                all_results.append(entry)
                seen_ids.add(entry.memory_id)

        # 按检索得分排序
        all_results.sort(key=lambda e: e.retrieval_score, reverse=True)

        # 合并相似度得分
        all_similarities = (
            short_term_results.similarities + long_term_results.similarities
        )
        if len(all_similarities) >= len(all_results):
            top_similarities = all_similarities[: len(all_results)]
        else:
            top_similarities = all_similarities + [0.0] * (
                len(all_results) - len(all_similarities)
            )

        return MemoryQueryResult(
            query_id=query.query_id,
            results=all_results[: query.top_k],
            similarities=top_similarities[: query.top_k],
            total_found=len(all_results),
            query_time_ms=short_term_results.query_time_ms
            + long_term_results.query_time_ms,
        )

    async def search(
        self,
        query_text: str,
        top_k: int = 10,
        memory_type: MemoryType | None = None,
        incident_id: str = "",
        agent_id: str = "",
    ) -> MemoryQueryResult:
        """
        快捷搜索

        Args:
            query_text: 查询文本
            top_k: 返回数量
            memory_type: 记忆类型过滤
            incident_id: 关联故障ID
            agent_id: 来源Agent过滤

        Returns:
            MemoryQueryResult: 搜索结果
        """
        query = MemoryQuery(
            query_text=query_text,
            top_k=top_k,
            source_incident_id=incident_id,
            source_agent=agent_id,
        )
        if memory_type:
            query.memory_types = [memory_type]

        return await self.retrieve(query)

    async def update(
        self,
        memory_id: str,
        content: str | None = None,
        importance: float | None = None,
        tags: list[str] | None = None,
    ) -> bool:
        """
        更新记忆

        Args:
            memory_id: 记忆ID
            content: 新内容
            importance: 新重要性
            tags: 新标签

        Returns:
            bool: 是否成功
        """
        # 尝试从长期记忆获取
        entry = None
        try:
            # 注意：这里假设storage有get方法
            if hasattr(self._long_term._storage, "get"):
                entry = await self._long_term._storage.get(memory_id)
        except Exception:
            pass

        if entry is None:
            logger.warning(f"Memory not found for update: {memory_id}")
            return False

        if content is not None:
            entry.content = content
            entry.summary = self._enhancer.generate_summary(content)
        if importance is not None:
            entry.importance_score = importance
        if tags is not None:
            entry.tags = tags

        entry.updated_at = datetime.now(timezone.utc)
        return await self._long_term._storage.update(entry)

    async def delete(self, memory_id: str) -> bool:
        """
        删除记忆

        从所有层级删除。

        Args:
            memory_id: 记忆ID

        Returns:
            bool: 是否成功
        """
        results = await asyncio.gather(
            self._long_term._storage.delete(memory_id),
            self._short_term._storage.delete(memory_id),
            return_exceptions=True,
        )
        deleted = any(r is True for r in results if not isinstance(r, Exception))
        logger.info(f"Memory deleted: {memory_id}, success={deleted}")
        return deleted

    # ---- 上下文组装 ----

    async def get_context(
        self,
        query: str = "",
        session_id: str = "default",
        incident_id: str = "",
        max_tokens: int = 4000,
    ) -> str:
        """
        为LLM调用组装相关记忆上下文

        综合短期记忆、工作记忆、长期记忆，组装优先级排序的上下文。

        Args:
            query: 查询（用于检索相关长期记忆）
            session_id: 会话ID
            incident_id: 故障ID
            max_tokens: 最大token数（估算）

        Returns:
            str: 组装好的上下文字符串
        """
        parts: list[str] = []
        estimated_tokens = 0
        token_budget = max_tokens

        # 1. 工作记忆（最高优先级 - 当前任务状态）
        if incident_id:
            try:
                wm_data = await self._working.get_all(incident_id)
                if wm_data:
                    wm_text = "[Current Task Context]\n"
                    for key, value in wm_data.items():
                        wm_text += f"  {key}: {str(value)[:200]}\n"
                    wm_tokens = len(wm_text.split())
                    if estimated_tokens + wm_tokens < token_budget:
                        parts.append(wm_text)
                        estimated_tokens += wm_tokens
            except Exception as e:
                logger.debug(f"Failed to get working memory context: {e}")

        # 2. 短期记忆（高优先级 - 最近对话）
        try:
            st_context = await self._short_term.get_context(session_id)
            if st_context:
                st_text = f"[Recent Conversation]\n{st_context}"
                st_tokens = len(st_text.split())
                if estimated_tokens + st_tokens < token_budget:
                    parts.append(st_text)
                    estimated_tokens += st_tokens
        except Exception as e:
            logger.debug(f"Failed to get short-term context: {e}")

        # 3. 长期记忆（检索相关知识）
        if query:
            try:
                lt_results = await self.search(
                    query_text=query,
                    top_k=5,
                    incident_id=incident_id,
                )
                if not lt_results.is_empty:
                    lt_text = "[Relevant Past Knowledge]\n"
                    for entry in lt_results.results:
                        lt_text += f"  - [{entry.memory_type.value}] {entry.content[:300]}\n"
                    lt_tokens = len(lt_text.split())
                    if estimated_tokens + lt_tokens < token_budget:
                        parts.append(lt_text)
            except Exception as e:
                logger.debug(f"Failed to get long-term context: {e}")

        return "\n\n".join(parts)

    # ---- 工作记忆操作 ----

    async def working_set(
        self,
        key: str,
        value: Any,
        incident_id: str = "default",
        ttl_seconds: int = 3600,
        priority: int = 5,
    ) -> WorkingMemorySlot:
        """
        设置工作记忆

        Args:
            key: 键
            value: 值
            incident_id: 故障ID
            ttl_seconds: TTL
            priority: 优先级

        Returns:
            WorkingMemorySlot: 槽位
        """
        return await self._working.set(key, value, incident_id, ttl_seconds, priority)

    async def working_get(
        self, key: str, incident_id: str = "default", default: Any = None
    ) -> Any:
        """获取工作记忆"""
        return await self._working.get(key, incident_id, default)

    async def working_get_all(self, incident_id: str = "default") -> dict[str, Any]:
        """获取所有工作记忆"""
        return await self._working.get_all(incident_id)

    def create_working_slot(
        self,
        name: str,
        content: Any,
        ttl_seconds: int = 300,
        priority: int = 5,
    ) -> WorkingMemorySlot:
        """创建工作记忆槽位（同步兼容接口）"""
        return self._working.create_slot(name, content, ttl_seconds, priority)

    def get_working_slot(self, name: str) -> WorkingMemorySlot | None:
        """获取工作记忆槽位"""
        return self._working.get_slot(name)

    def clear_working_memory(self) -> None:
        """清空工作记忆"""
        self._working.clear()

    # ---- 记忆流转 ----

    async def flow_stm_to_ltm(self, importance_threshold: float = 0.6) -> int:
        """
        短期记忆 -> 长期记忆

        将重要的短期记忆归档到长期记忆。

        Args:
            importance_threshold: 重要性阈值

        Returns:
            int: 归档数量
        """
        archived = 0
        try:
            all_stm = await self._short_term._storage.get_by_type(
                memory_level=MemoryLevel.SHORT_TERM,
                limit=1000,
            )

            for entry in all_stm:
                if entry.importance_score >= importance_threshold:
                    entry.memory_level = MemoryLevel.LONG_TERM
                    await self._long_term.store(entry)
                    archived += 1

            logger.info(f"Flowed {archived} STM entries to LTM")
            return archived

        except Exception as e:
            logger.error(f"STM->LTM flow failed: {e}")
            return 0

    async def flow_wm_to_ltm(self, incident_id: str) -> int:
        """
        工作记忆 -> 长期记忆

        incident完成后归档工作记忆。

        Args:
            incident_id: 故障ID

        Returns:
            int: 归档数量
        """
        try:
            entries = await self._working.archive(incident_id, self._long_term)
            logger.info(
                f"Flowed {len(entries)} WM entries to LTM for incident {incident_id}"
            )
            return len(entries)
        except Exception as e:
            logger.error(f"WM->LTM flow failed for {incident_id}: {e}")
            return 0

    async def flow_ltm_to_wm(
        self, query: str, incident_id: str, top_k: int = 3
    ) -> list[MemoryEntry]:
        """
        长期记忆 -> 工作记忆

        检索相关历史知识加载到工作记忆。

        Args:
            query: 查询
            incident_id: 故障ID
            top_k: 数量

        Returns:
            list[MemoryEntry]: 加载的记忆
        """
        try:
            results = await self.search(query_text=query, top_k=top_k)
            loaded = []

            for entry in results.results:
                await self._working.set(
                    key=f"ltm_{entry.memory_id[:8]}",
                    value={
                        "content": entry.content,
                        "type": entry.memory_type.value,
                        "importance": entry.importance_score,
                        "source": entry.source_agent,
                    },
                    incident_id=incident_id,
                    ttl_seconds=7200,
                    priority=7,
                )
                loaded.append(entry)

            logger.info(f"Loaded {len(loaded)} LTM entries to WM for {incident_id}")
            return loaded

        except Exception as e:
            logger.error(f"LTM->WM flow failed: {e}")
            return []

    # ---- 维护操作 ----

    async def consolidate(self) -> dict[str, Any]:
        """
        整理记忆

        合并相似记忆，清理过期数据。

        Returns:
            dict: 整理统计
        """
        st_stats = await self._short_term.consolidate()
        lt_stats = await self._long_term.consolidate()

        return {
            "short_term": st_stats,
            "long_term": lt_stats,
        }

    async def archive_old_memories(self) -> int:
        """
        归档过期记忆

        Returns:
            int: 归档数量
        """
        return await self._long_term.forget_old()

    async def get_stats(self) -> dict[str, Any]:
        """获取记忆系统统计"""
        return {
            "short_term": await self._short_term.get_stats(),
            "long_term": await self._long_term.get_stats(),
            "working": self._working.get_stats(),
        }

    # ---- 生命周期 ----

    async def start(self) -> None:
        """启动记忆系统（启动后台任务）"""
        await self._short_term.start()
        logger.info("MemorySystem started")

    async def stop(self) -> None:
        """停止记忆系统"""
        await self._short_term.stop()
        logger.info("MemorySystem stopped")

    # ---- 属性访问 ----

    @property
    def short_term(self) -> ShortTermMemory:
        """短期记忆"""
        return self._short_term

    @property
    def long_term(self) -> LongTermMemory:
        """长期记忆"""
        return self._long_term

    @property
    def working(self) -> WorkingMemory:
        """工作记忆"""
        return self._working
