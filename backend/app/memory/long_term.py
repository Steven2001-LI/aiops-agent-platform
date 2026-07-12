"""
AIOps Agent Platform - Long Term Memory

长期记忆实现，基于向量存储 + 语义检索。
融合 MemGPT 设计理念：
- 持久化知识沉淀
- 向量存储 + 语义搜索
- 记忆重要性评分（0-1）
- 记忆衰减机制（长时间不访问降低重要性）
- 记忆合并（相似记忆合并去重）
- 混合检索（RRF融合：语义 + 关键词 + 时间衰减 + 重要性）
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone
from typing import Any

from app.memory.storage import BaseStorage, ChromaDBStorage, get_embedding
from app.models.memory import MemoryEntry, MemoryLevel, MemoryQuery, MemoryQueryResult, MemoryType
from app.utils.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# RRF Fusion & Scoring
# ---------------------------------------------------------------------------


def time_decay_score(
    entry: MemoryEntry,
    current_time: datetime | None = None,
    half_life_days: float = 30.0,
) -> float:
    """
    计算时间衰减得分

    使用指数衰减模型，越新的记忆得分越高。
    访问次数可部分抵消衰减。

    Formula:
        decay = 0.5 ^ (age_days / half_life_days)
        access_boost = min(access_count * 0.05, 0.3)
        score = importance * decay + access_boost

    Args:
        entry: 记忆条目
        current_time: 当前时间（默认UTC now）
        half_life_days: 半衰期（天数）

    Returns:
        float: 衰减后的得分
    """
    if current_time is None:
        current_time = datetime.now(timezone.utc)

    # 计算年龄（使用最后访问时间或创建时间）
    reference_time = entry.last_accessed_at or entry.created_at
    if reference_time is None:
        age_days = half_life_days  # 无时间信息，按半衰期处理
    else:
        # 确保时区一致
        if reference_time.tzinfo is None:
            reference_time = reference_time.replace(tzinfo=timezone.utc)
        age = current_time - reference_time
        age_days = max(0, age.total_seconds() / 86400)

    # 指数衰减
    decay_factor = 0.5 ** (age_days / half_life_days)

    # 访问频率增益（最多+0.3）
    access_boost = min(entry.access_count * 0.05, 0.3)

    return entry.importance_score * decay_factor + access_boost


def keyword_search_score(query: str, entry: MemoryEntry) -> float:
    """
    关键词搜索得分（BM25简化版）

    基于TF-IDF思想的简化关键词匹配。
    """
    query_lower = query.lower()
    content_lower = entry.content.lower()
    query_words = query_lower.split()

    if not query_words:
        return 0.0

    score = 0.0

    # 完全匹配加分
    if query_lower in content_lower:
        score += 0.8

    # 分词匹配（TF思想）
    content_words = content_lower.split()
    content_len = len(content_words)
    if content_len == 0:
        return score

    for word in query_words:
        word_count = content_words.count(word)
        # TF = 词频 / 文档长度
        tf = word_count / content_len
        # IDF简化 = log(1 + 1/包含该词的文档比例) -- 这里简化为常数
        score += tf * 2.0

    # 标签匹配
    for tag in entry.tags:
        tag_lower = tag.lower()
        for word in query_words:
            if word in tag_lower:
                score += 0.5
                break

    # 摘要匹配
    if entry.summary and query_lower in entry.summary.lower():
        score += 0.3

    return min(score, 2.0)


def rrf_fusion(
    semantic_ranking: list[tuple[str, float]],
    keyword_ranking: list[tuple[str, float]],
    time_ranking: list[tuple[str, float]],
    importance_scores: dict[str, float],
    k: float = 60.0,
    weights: dict[str, float] | None = None,
) -> list[tuple[str, float]]:
    """
    Reciprocal Rank Fusion (RRF)

    融合多个排序列表为一个综合排序。

    Formula: score = sum(weight_i / (k + rank_i))

    Args:
        semantic_ranking: [(memory_id, score), ...]
        keyword_ranking: [(memory_id, score), ...]
        time_ranking: [(memory_id, score), ...]
        importance_scores: {memory_id: importance_score}
        k: RRF常数（防止高排名过度主导）
        weights: 各排序的权重

    Returns:
        [(memory_id, fused_score), ...] 按得分降序
    """
    if weights is None:
        weights = {
            "semantic": 0.4,
            "keyword": 0.3,
            "time": 0.2,
            "importance": 0.1,
        }

    # 构建排名映射
    def build_rank_map(ranking: list[tuple[str, float]]) -> dict[str, int]:
        return {mid: rank for rank, (mid, _) in enumerate(ranking, start=1)}

    semantic_ranks = build_rank_map(semantic_ranking)
    keyword_ranks = build_rank_map(keyword_ranking)
    time_ranks = build_rank_map(time_ranking)

    # 所有memory_id的集合
    all_ids = set(semantic_ranks.keys()) | set(keyword_ranks.keys()) | set(time_ranks.keys())

    # 计算RRF分数
    fused_scores: dict[str, float] = {}
    for mid in all_ids:
        score = 0.0

        if mid in semantic_ranks:
            score += weights.get("semantic", 0.4) / (k + semantic_ranks[mid])
        if mid in keyword_ranks:
            score += weights.get("keyword", 0.3) / (k + keyword_ranks[mid])
        if mid in time_ranks:
            score += weights.get("time", 0.2) / (k + time_ranks[mid])

        # 重要性作为直接加分项
        imp = importance_scores.get(mid, 0.5)
        score += weights.get("importance", 0.1) * imp / k

        fused_scores[mid] = score

    # 按分数降序排序
    sorted_results = sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)
    return sorted_results


# ---------------------------------------------------------------------------
# Long Term Memory
# ---------------------------------------------------------------------------


class LongTermMemory:
    """
    长期记忆管理器

    持久化知识沉淀，支持语义检索和混合搜索。

    特点:
    - 持久化存储（ChromaDB）
    - 语义检索（向量相似度）
    - 混合搜索（RRF融合：语义 + 关键词 + 时间衰减 + 重要性）
    - 记忆衰减（长时间不访问降低重要性）
    - 记忆合并（相似记忆合并去重）
    - 记忆自编辑（冲突时更新而非追加）

    Attributes:
        storage: 存储后端
        similarity_threshold: 相似度阈值
        retention_days: 保留天数
    """

    def __init__(
        self,
        storage: BaseStorage | None = None,
        similarity_threshold: float = 0.85,
        retention_days: int = 90,
        decay_half_life_days: float = 30.0,
    ) -> None:
        self._storage = storage or ChromaDBStorage()
        self._similarity_threshold = similarity_threshold
        self._retention_days = retention_days
        self._decay_half_life = decay_half_life_days

        # 内存缓存（加速频繁访问）
        self._cache: dict[str, tuple[MemoryEntry, float]] = {}  # memory_id -> (entry, cache_time)
        self._cache_ttl = 300.0  # 缓存5分钟
        self._lock = asyncio.Lock()

    # ---- 核心操作 ----

    async def store(self, entry: MemoryEntry) -> None:
        """
        存储记忆到长期记忆

        如果存在高度相似的记忆，则更新而非追加。

        Args:
            entry: 记忆条目
        """
        entry.memory_level = MemoryLevel.LONG_TERM

        # 生成embedding（如果还没有）
        if entry.content_vector is None:
            entry.content_vector = await get_embedding(entry.content)

        async with self._lock:
            # 检查是否存在相似记忆
            similar = await self._find_similar(entry)
            if similar and similar.importance_score > 0.7:
                # 自编辑：更新现有记忆而非追加
                similar.content = self._merge_content(similar.content, entry.content)
                similar.importance_score = max(similar.importance_score, entry.importance_score)
                similar.tags = list(set(similar.tags + entry.tags))
                similar.updated_at = datetime.now(timezone.utc)
                if entry.content_vector:
                    similar.content_vector = entry.content_vector

                await self._storage.update(similar)
                logger.debug(
                    "Memory self-edited (merged with similar)",
                    memory_id=similar.memory_id,
                )
            else:
                await self._storage.add(entry)
                logger.debug(
                    "Stored in long-term memory",
                    memory_id=entry.memory_id,
                )

    async def _find_similar(self, entry: MemoryEntry) -> MemoryEntry | None:
        """
        查找与entry最相似的记忆

        Returns:
            最相似的记忆条目，如果相似度低于阈值则返回None
        """
        try:
            results = await self._storage.search(
                query=entry.content,
                top_k=1,
                filters={
                    "memory_type": entry.memory_type,
                    "source_agent": entry.source_agent,
                },
            )
            if results:
                # 计算向量相似度
                best = results[0]
                if best.content_vector and entry.content_vector:
                    similarity = self._cosine_similarity(
                        best.content_vector, entry.content_vector
                    )
                    if similarity >= self._similarity_threshold:
                        return best
                else:
                    # 回退到关键词相似度
                    return best
            return None
        except Exception as e:
            logger.warning(f"Similar memory search failed: {e}")
            return None

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """计算两个向量的余弦相似度"""
        if len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    @staticmethod
    def _merge_content(existing: str, new: str) -> str:
        """
        合并两个内容（冲突解决）

        策略：保留更详细的版本，或拼接两个版本。
        """
        if new in existing:
            return existing
        if existing in new:
            return new
        return f"{existing}\n[Updated]: {new}"

    # ---- 检索 ----

    async def retrieve(self, query: MemoryQuery) -> MemoryQueryResult:
        """
        从长期记忆检索

        使用混合搜索（RRF融合）。

        Args:
            query: 查询

        Returns:
            MemoryQueryResult: 结果
        """
        start_time = datetime.now(timezone.utc)

        try:
            # 1. 语义搜索
            semantic_results = await self._semantic_search(
                query.query_text, query.top_k * 2, query
            )

            # 2. 获取候选集进行关键词和时间排序
            all_candidate_ids = list({r.memory_id for r in semantic_results})

            # 从存储获取完整条目（用于关键词和时间排序）
            all_entries = []
            for mid in all_candidate_ids:
                entry = await self._storage.get(mid)
                if entry:
                    all_entries.append(entry)

            # 3. 关键词排序
            keyword_ranking = [
                (e.memory_id, keyword_search_score(query.query_text, e))
                for e in all_entries
            ]
            keyword_ranking.sort(key=lambda x: x[1], reverse=True)

            # 4. 时间衰减排序
            current_time = datetime.now(timezone.utc)
            time_ranking = [
                (e.memory_id, time_decay_score(e, current_time, self._decay_half_life))
                for e in all_entries
            ]
            time_ranking.sort(key=lambda x: x[1], reverse=True)

            # 5. 重要性映射
            importance_scores = {e.memory_id: e.importance_score for e in all_entries}

            # 6. RRF融合
            semantic_ranking = [(e.memory_id, 0.0) for e in semantic_results]
            fused = rrf_fusion(
                semantic_ranking,
                keyword_ranking,
                time_ranking,
                importance_scores,
            )

            # 7. 组装结果
            entry_map = {e.memory_id: e for e in all_entries}
            results: list[MemoryEntry] = []
            similarities: list[float] = []

            for mid, fused_score in fused[: query.top_k]:
                if mid in entry_map:
                    entry = entry_map[mid]
                    entry.touch()
                    results.append(entry)
                    similarities.append(fused_score)

            query_time_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000

            return MemoryQueryResult(
                query_id=query.query_id,
                results=results,
                similarities=similarities,
                total_found=len(fused),
                query_time_ms=query_time_ms,
            )

        except Exception as e:
            logger.error(f"Long-term memory retrieval failed: {e}")
            return MemoryQueryResult(query_id=query.query_id)

    async def _semantic_search(
        self,
        query_text: str,
        top_k: int,
        query: MemoryQuery,
    ) -> list[MemoryEntry]:
        """执行语义搜索"""
        filters: dict[str, Any] = {}

        if query.memory_types:
            filters["memory_type"] = query.memory_types[0]
        if query.source_agent:
            filters["source_agent"] = query.source_agent
        if query.source_incident_id:
            filters["source_incident_id"] = query.source_incident_id

        try:
            return await self._storage.search(
                query=query_text,
                top_k=top_k,
                filters=filters,
            )
        except Exception as e:
            logger.error(f"Semantic search failed: {e}")
            return []

    # ---- 记忆管理 ----

    async def update_importance(self, memory_id: str, new_importance: float) -> bool:
        """
        更新记忆重要性

        Args:
            memory_id: 记忆ID
            new_importance: 新重要性值 (0-1)

        Returns:
            bool: 是否成功
        """
        entry = await self._storage.get(memory_id)
        if entry is None:
            return False

        entry.importance_score = max(0.0, min(1.0, new_importance))
        entry.updated_at = datetime.now(timezone.utc)
        return await self._storage.update(entry)

    async def forget_old(self, max_age_days: int | None = None) -> int:
        """
        遗忘过期记忆

        删除超过保留期的记忆。

        Args:
            max_age_days: 最大保留天数（默认使用配置值）

        Returns:
            int: 删除的条目数
        """
        max_age = max_age_days or self._retention_days
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age)

        removed = 0
        try:
            # 获取所有长期记忆
            all_entries = await self._storage.get_by_type(
                memory_level=MemoryLevel.LONG_TERM,
                limit=10000,
            )

            for entry in all_entries:
                created_at = entry.created_at
                if created_at and created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)

                if created_at and created_at < cutoff:
                    # 检查是否被频繁访问（重要记忆不过期）
                    if entry.importance_score < 0.8 or entry.access_count < 5:
                        await self._storage.delete(entry.memory_id)
                        removed += 1

            logger.info(f"Forgot {removed} old memories (older than {max_age} days)")
            return removed

        except Exception as e:
            logger.error(f"Failed to forget old memories: {e}")
            return 0

    async def merge_similar(self, similarity_threshold: float | None = None) -> int:
        """
        合并相似记忆

        查找相似度高于阈值的记忆对，合并为一条。

        Args:
            similarity_threshold: 相似度阈值（默认使用配置值）

        Returns:
            int: 合并的条目数
        """
        threshold = similarity_threshold or self._similarity_threshold
        merged = 0

        try:
            all_entries = await self._storage.get_by_type(
                memory_level=MemoryLevel.LONG_TERM,
                limit=5000,
            )

            # 按类型分组（减少比较次数）
            by_type: dict[str, list[MemoryEntry]] = {}
            for entry in all_entries:
                by_type.setdefault(entry.memory_type.value, []).append(entry)

            for type_entries in by_type.values():
                merged_ids: set[str] = set()

                for i, entry_a in enumerate(type_entries):
                    if entry_a.memory_id in merged_ids:
                        continue
                    if not entry_a.content_vector:
                        continue

                    for entry_b in type_entries[i + 1 :]:
                        if entry_b.memory_id in merged_ids:
                            continue
                        if not entry_b.content_vector:
                            continue

                        sim = self._cosine_similarity(
                            entry_a.content_vector, entry_b.content_vector
                        )

                        if sim >= threshold:
                            # 合并：保留更详细的版本
                            entry_a.content = self._merge_content(
                                entry_a.content, entry_b.content
                            )
                            entry_a.importance_score = max(
                                entry_a.importance_score, entry_b.importance_score
                            )
                            entry_a.tags = list(set(entry_a.tags + entry_b.tags))
                            entry_a.access_count += entry_b.access_count
                            entry_a.updated_at = datetime.now(timezone.utc)

                            await self._storage.update(entry_a)
                            await self._storage.delete(entry_b.memory_id)

                            merged_ids.add(entry_b.memory_id)
                            merged += 1

            logger.info(f"Merged {merged} similar memories")
            return merged

        except Exception as e:
            logger.error(f"Failed to merge similar memories: {e}")
            return 0

    # ---- 维护操作 ----

    async def consolidate(self) -> dict[str, Any]:
        """
        整理长期记忆

        合并相似记忆，遗忘过期记忆。

        Returns:
            dict: 整理统计
        """
        merged = await self.merge_similar()
        forgotten = await self.forget_old()
        stats = await self.get_stats()

        return {
            "type": "long_term",
            "merged": merged,
            "forgotten": forgotten,
            **stats,
        }

    async def get_stats(self) -> dict[str, Any]:
        """获取统计"""
        storage_stats = await self._storage.get_stats()
        return {
            "similarity_threshold": self._similarity_threshold,
            "retention_days": self._retention_days,
            "decay_half_life_days": self._decay_half_life,
            **storage_stats,
        }
