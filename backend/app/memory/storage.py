"""
AIOps Agent Platform - Memory Storage Layer

记忆系统的存储层抽象，支持多种后端存储。
融合 mem0 和 MemGPT 设计理念，提供统一的存储接口。

存储后端:
- InMemoryStorage: 内存存储，适合开发/测试，支持多维度索引
- ChromaDBStorage: 向量数据库存储，适合生产环境，支持语义搜索
"""

from __future__ import annotations

import asyncio
import hashlib
import math
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from app.models.memory import MemoryEntry, MemoryLevel, MemoryType
from app.utils.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Embedding utilities
# ---------------------------------------------------------------------------


def hash_based_embedding(text: str, dim: int = 384) -> list[float]:
    """
    基于哈希的简单embedding生成（fallback方案）

    将文本哈希为固定维度的向量，保证相同文本产生相同向量。
    适用于没有sentence-transformers的环境。
    """
    vector = [0.0] * dim
    text_bytes = text.encode("utf-8")
    for i in range(dim):
        hash_input = text_bytes + i.to_bytes(4, "big")
        hash_val = int(hashlib.sha256(hash_input).hexdigest(), 16)
        # 归一化到 [-1, 1]
        vector[i] = (hash_val % 20000) / 10000.0 - 1.0

    # L2归一化
    norm = math.sqrt(sum(v * v for v in vector))
    if norm > 0:
        vector = [v / norm for v in vector]
    return vector


async def get_embedding(text: str) -> list[float]:
    """
    获取文本的embedding向量

    优先使用sentence-transformers，回退到hash-based embedding。

    Args:
        text: 输入文本

    Returns:
        list[float]: 向量表示
    """
    try:
        # 模型初始化和 encode 都可能较慢，不在事件循环线程中执行。
        loop = asyncio.get_running_loop()
        model = await loop.run_in_executor(None, _get_sentence_transformer_model)
        embedding = await loop.run_in_executor(None, model.encode, text)
        return embedding.tolist()
    except ImportError:
        logger.debug("sentence-transformers not available, using hash-based embedding")
        return hash_based_embedding(text)
    except Exception as e:
        logger.warning(f"Embedding generation failed: {e}, using hash-based fallback")
        return hash_based_embedding(text)


# 全局缓存的 SentenceTransformer 模型
_sentence_transformer_model: Any = None


def _get_sentence_transformer_model() -> Any:
    """获取或初始化 SentenceTransformer 模型（单例）"""
    global _sentence_transformer_model
    if _sentence_transformer_model is None:
        # 保持可选依赖语义：未安装时由 get_embedding 捕获 ImportError 并降级。
        from sentence_transformers import SentenceTransformer

        _sentence_transformer_model = SentenceTransformer("all-MiniLM-L6-v2")
        logger.info("SentenceTransformer model loaded: all-MiniLM-L6-v2")
    return _sentence_transformer_model


def encode_texts_sync(texts: list[str]) -> list[list[float]] | None:
    """同步批量编码,返回 L2 归一化向量(评测等显式批处理场景用)。

    sentence-transformers 不可用/加载失败时返回 None——调用方应回退
    自己的词面实现。绝不能用 hash_based_embedding 兜底做相似度:
    哈希向量对不同文本近正交(任意余弦≈0),会把语义指标系统性打崩。
    """
    if not texts:
        return []
    try:
        model = _get_sentence_transformer_model()
        vectors = model.encode(texts)
    except Exception as e:
        logger.warning(f"Sync text encoding unavailable: {e}")
        return None
    result: list[list[float]] = []
    for vec in vectors:
        values = vec.tolist()
        norm = math.sqrt(sum(v * v for v in values))
        result.append([v / norm for v in values] if norm > 0 else values)
    return result


# ---------------------------------------------------------------------------
# Base Storage
# ---------------------------------------------------------------------------


class BaseStorage(ABC):
    """
    记忆存储抽象基类

    定义存储层统一接口，所有存储后端必须实现此接口。
    支持不同后端实现（内存、ChromaDB、SQLite等）。
    """

    @abstractmethod
    async def add(self, entry: MemoryEntry) -> None:
        """添加记忆条目"""
        ...

    @abstractmethod
    async def get(self, memory_id: str) -> MemoryEntry | None:
        """根据ID获取记忆"""
        ...

    @abstractmethod
    async def update(self, entry: MemoryEntry) -> bool:
        """更新记忆条目"""
        ...

    @abstractmethod
    async def delete(self, memory_id: str) -> bool:
        """删除记忆"""
        ...

    @abstractmethod
    async def search(
        self,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[MemoryEntry]:
        """搜索记忆"""
        ...

    @abstractmethod
    async def get_by_type(
        self,
        memory_type: MemoryType | None = None,
        memory_level: MemoryLevel | None = None,
        agent_id: str = "",
        session_id: str = "",
        limit: int = 100,
    ) -> list[MemoryEntry]:
        """按类型/层级/Agent/Session获取记忆"""
        ...

    @abstractmethod
    async def clear(self) -> None:
        """清空所有记忆"""
        ...

    @abstractmethod
    async def get_stats(self) -> dict[str, Any]:
        """获取存储统计"""
        ...


# ---------------------------------------------------------------------------
# In-Memory Storage
# ---------------------------------------------------------------------------


class InMemoryStorage(BaseStorage):
    """
    内存存储实现

    基于字典+列表的内存存储，支持按memory_type、agent_id、session_id索引。
    使用asyncio.Lock保证线程安全。

    特点:
    - 快速存取（O(1)平均）
    - 多维度索引（type, level, agent, session）
    - 线程安全（asyncio.Lock）
    - 适合开发和测试环境
    """

    def __init__(self) -> None:
        # 主存储: memory_id -> MemoryEntry
        self._entries: dict[str, MemoryEntry] = {}

        # 多维度索引
        self._index_by_type: dict[str, set[str]] = {}
        self._index_by_level: dict[str, set[str]] = {}
        self._index_by_agent: dict[str, set[str]] = {}
        self._index_by_session: dict[str, set[str]] = {}
        self._index_by_incident: dict[str, set[str]] = {}
        self._index_by_tags: dict[str, set[str]] = {}

        # 线程安全锁
        self._lock = asyncio.Lock()

    # ---- 内部索引管理 ----

    def _add_to_index(self, entry: MemoryEntry) -> None:
        """将条目添加到所有索引"""
        mid = entry.memory_id

        self._index_by_type.setdefault(entry.memory_type.value, set()).add(mid)
        self._index_by_level.setdefault(entry.memory_level.value, set()).add(mid)

        if entry.source_agent:
            self._index_by_agent.setdefault(entry.source_agent, set()).add(mid)
        if entry.source_incident_id:
            self._index_by_incident.setdefault(entry.source_incident_id, set()).add(mid)

        for tag in entry.tags:
            self._index_by_tags.setdefault(tag.lower(), set()).add(mid)

    def _remove_from_index(self, entry: MemoryEntry) -> None:
        """从所有索引中移除条目"""
        mid = entry.memory_id

        self._index_by_type.get(entry.memory_type.value, set()).discard(mid)
        self._index_by_level.get(entry.memory_level.value, set()).discard(mid)
        self._index_by_agent.get(entry.source_agent, set()).discard(mid)
        self._index_by_incident.get(entry.source_incident_id, set()).discard(mid)

        for tag in entry.tags:
            self._index_by_tags.get(tag.lower(), set()).discard(mid)

    def _get_filtered_ids(
        self,
        memory_type: MemoryType | None = None,
        memory_level: MemoryLevel | None = None,
        agent_id: str = "",
        session_id: str = "",
        incident_id: str = "",
    ) -> set[str] | None:
        """
        根据过滤条件获取候选ID集合

        使用索引交集优化过滤性能。
        """
        candidate_sets: list[set[str]] = []

        if memory_type is not None:
            candidate_sets.append(
                self._index_by_type.get(memory_type.value, set()).copy()
            )
        if memory_level is not None:
            candidate_sets.append(
                self._index_by_level.get(memory_level.value, set()).copy()
            )
        if agent_id:
            candidate_sets.append(
                self._index_by_agent.get(agent_id, set()).copy()
            )
        if session_id:
            candidate_sets.append(
                self._index_by_session.get(session_id, set()).copy()
            )
        if incident_id:
            candidate_sets.append(
                self._index_by_incident.get(incident_id, set()).copy()
            )

        if not candidate_sets:
            return None  # 无过滤条件，返回全部

        # 取交集
        result = candidate_sets[0]
        for s in candidate_sets[1:]:
            result &= s
            if not result:
                return set()  # 空交集
        return result

    # ---- 公共接口 ----

    async def add(self, entry: MemoryEntry) -> None:
        """添加记忆条目"""
        async with self._lock:
            # 如果已存在，先移除旧索引
            if entry.memory_id in self._entries:
                self._remove_from_index(self._entries[entry.memory_id])

            self._entries[entry.memory_id] = entry
            self._add_to_index(entry)

        logger.debug(
            "Memory added to InMemoryStorage",
            memory_id=entry.memory_id,
            memory_type=entry.memory_type.value,
        )

    async def get(self, memory_id: str) -> MemoryEntry | None:
        """根据ID获取记忆"""
        async with self._lock:
            entry = self._entries.get(memory_id)
            if entry:
                entry.touch()
            return entry

    async def update(self, entry: MemoryEntry) -> bool:
        """更新记忆条目"""
        async with self._lock:
            if entry.memory_id not in self._entries:
                return False

            # 移除旧索引
            self._remove_from_index(self._entries[entry.memory_id])
            # 更新条目
            entry.updated_at = datetime.now(timezone.utc)
            self._entries[entry.memory_id] = entry
            # 添加新索引
            self._add_to_index(entry)

        logger.debug("Memory updated", memory_id=entry.memory_id)
        return True

    async def delete(self, memory_id: str) -> bool:
        """删除记忆"""
        async with self._lock:
            entry = self._entries.pop(memory_id, None)
            if entry is None:
                return False
            self._remove_from_index(entry)

        logger.debug("Memory deleted", memory_id=memory_id)
        return True

    async def search(
        self,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[MemoryEntry]:
        """
        搜索记忆

        使用关键词匹配 + 向量相似度（如果可用）的混合搜索。
        支持filters过滤（memory_type, memory_level, source_agent, source_incident_id, tags）。
        """
        filters = filters or {}
        query_lower = query.lower()

        async with self._lock:
            # 获取候选集
            candidate_ids = self._get_filtered_ids(
                memory_type=filters.get("memory_type"),
                memory_level=filters.get("memory_level"),
                agent_id=filters.get("source_agent", ""),
                incident_id=filters.get("source_incident_id", ""),
            )

            if candidate_ids is not None and not candidate_ids:
                return []

            entries_to_search = (
                list(self._entries.values())
                if candidate_ids is None
                else [self._entries[mid] for mid in candidate_ids if mid in self._entries]
            )

            # 关键词匹配 + 向量相似度
            scored_entries: list[tuple[MemoryEntry, float]] = []

            for entry in entries_to_search:
                score = self._compute_search_score(entry, query_lower)
                if score > 0:
                    scored_entries.append((entry, score))

            # 按分数排序
            scored_entries.sort(key=lambda x: x[1], reverse=True)

            # 标记检索方法并更新访问统计
            results = []
            for entry, _ in scored_entries[:top_k]:
                entry.touch()
                results.append(entry)

            return results

    def _compute_search_score(self, entry: MemoryEntry, query_lower: str) -> float:
        """
        计算搜索相关性得分

        综合内容匹配、标签匹配、重要性加权。
        """
        score = 0.0
        content_lower = entry.content.lower()

        # 内容关键词匹配
        if query_lower in content_lower:
            score += 0.5
        # 部分匹配（每个词）
        query_words = query_lower.split()
        match_count = sum(1 for w in query_words if w in content_lower)
        score += 0.2 * (match_count / max(len(query_words), 1))

        # 标签匹配
        for tag in entry.tags:
            if query_lower in tag.lower():
                score += 0.3
                break

        # 重要性加权
        score += entry.importance_score * 0.2

        # 访问频率加权（已被频繁访问的条目更可能相关）
        access_boost = min(entry.access_count * 0.02, 0.1)
        score += access_boost

        return min(score, 1.0)

    async def get_by_type(
        self,
        memory_type: MemoryType | None = None,
        memory_level: MemoryLevel | None = None,
        agent_id: str = "",
        session_id: str = "",
        limit: int = 100,
    ) -> list[MemoryEntry]:
        """按类型/层级/Agent/Session获取记忆"""
        async with self._lock:
            candidate_ids = self._get_filtered_ids(
                memory_type=memory_type,
                memory_level=memory_level,
                agent_id=agent_id,
                session_id=session_id,
            )

            if candidate_ids is not None and not candidate_ids:
                return []

            if candidate_ids is not None:
                entries = [
                    self._entries[mid] for mid in candidate_ids if mid in self._entries
                ]
            else:
                entries = list(self._entries.values())

            # 按重要性降序
            entries.sort(key=lambda e: e.importance_score, reverse=True)
            return entries[:limit]

    async def clear(self) -> None:
        """清空所有记忆"""
        async with self._lock:
            self._entries.clear()
            self._index_by_type.clear()
            self._index_by_level.clear()
            self._index_by_agent.clear()
            self._index_by_session.clear()
            self._index_by_incident.clear()
            self._index_by_tags.clear()

        logger.info("InMemoryStorage cleared")

    async def get_stats(self) -> dict[str, Any]:
        """获取存储统计"""
        async with self._lock:
            return {
                "backend": "in_memory",
                "total_entries": len(self._entries),
                "index_type_count": len(self._index_by_type),
                "index_level_count": len(self._index_by_level),
                "index_agent_count": len(self._index_by_agent),
                "index_incident_count": len(self._index_by_incident),
                "index_tag_count": len(self._index_by_tags),
            }


# ---------------------------------------------------------------------------
# ChromaDB Storage
# ---------------------------------------------------------------------------


class ChromaDBStorage(BaseStorage):
    """
    ChromaDB 向量数据库存储

    生产环境推荐的存储后端，支持语义搜索和向量检索。
    自动embedding生成（sentence-transformers优先，回退到hash-based）。

    特点:
    - 持久化存储
    - 语义搜索（向量相似度）
    - 自动embedding生成
    - 元数据过滤
    """

    def __init__(
        self,
        collection_name: str | None = None,
        persist_directory: str | None = None,
        host: str | None = None,
        port: int | None = None,
    ) -> None:
        # 显式参数优先,缺省回退 CHROMA_* 配置(config.ChromaConfig);
        # host 非空 → HttpClient 服务端模式,否则嵌入式 PersistentClient
        chroma_cfg = None
        try:
            from app.config import get_config
            chroma_cfg = get_config().chroma
        except Exception:  # 配置系统不可用时保持历史默认值
            pass

        self.collection_name = collection_name or (
            chroma_cfg.collection_name if chroma_cfg else "aiops_memory"
        )
        self.persist_directory = persist_directory or (
            chroma_cfg.db_path if chroma_cfg else "./data/chromadb"
        )
        self.host = host if host is not None else (
            chroma_cfg.host if chroma_cfg else None
        )
        self.port = port or (chroma_cfg.port if chroma_cfg else 8000)
        self._client: Any = None
        self._collection: Any = None
        self._initialized: bool = False
        self._init_lock = asyncio.Lock()

    async def _ensure_initialized(self) -> None:
        """确保ChromaDB已初始化（线程安全懒加载）"""
        if self._initialized:
            return

        async with self._init_lock:
            if self._initialized:
                return

            try:
                import chromadb
                from chromadb.config import Settings

                if self.host:
                    self._client = chromadb.HttpClient(
                        host=self.host,
                        port=self.port,
                        settings=Settings(anonymized_telemetry=False),
                    )
                else:
                    self._client = chromadb.PersistentClient(
                        path=self.persist_directory,
                        settings=Settings(
                            anonymized_telemetry=False,
                            allow_reset=True,
                        ),
                    )
                self._collection = self._client.get_or_create_collection(
                    name=self.collection_name,
                    metadata={"description": "AIOps memory storage"},
                )
                self._initialized = True
                logger.info(
                    "ChromaDB initialized",
                    collection=self.collection_name,
                    mode="http" if self.host else "embedded",
                    server=f"{self.host}:{self.port}" if self.host else self.persist_directory,
                )

            except ImportError:
                logger.error(
                    "chromadb not installed. Install with: pip install chromadb"
                )
                self._client = None
                self._collection = None
                self._initialized = True  # 标记为已初始化避免重复尝试

            except Exception as e:
                logger.error(f"Failed to initialize ChromaDB: {e}")
                self._client = None
                self._collection = None
                self._initialized = True

    # ---- 公共接口 ----

    async def add(self, entry: MemoryEntry) -> None:
        """添加记忆条目到ChromaDB"""
        await self._ensure_initialized()

        if self._collection is None:
            logger.warning("ChromaDB not available, skipping add")
            return

        try:
            # 生成embedding（如果还没有）
            if entry.content_vector is None:
                entry.content_vector = await get_embedding(entry.content)

            # 构建元数据
            metadata = {
                "memory_type": entry.memory_type.value,
                "memory_level": entry.memory_level.value,
                "source_agent": entry.source_agent or "",
                "source_incident_id": entry.source_incident_id or "",
                "source_event_id": entry.source_event_id or "",
                "importance_score": entry.importance_score,
                "confidence_score": entry.confidence_score,
                "tags": ",".join(entry.tags),
                "created_at": entry.created_at.isoformat() if entry.created_at else "",
                "summary": entry.summary or "",
                **{f"meta_{k}": str(v) for k, v in entry.metadata.items()},
            }

            self._collection.add(
                ids=[entry.memory_id],
                embeddings=[entry.content_vector],
                documents=[entry.content],
                metadatas=[metadata],
            )

            logger.debug(
                "Memory added to ChromaDB",
                memory_id=entry.memory_id,
                memory_type=entry.memory_type.value,
            )

        except Exception as e:
            logger.error(f"Failed to add memory to ChromaDB: {e}", memory_id=entry.memory_id)
            raise

    async def get(self, memory_id: str) -> MemoryEntry | None:
        """根据ID获取记忆"""
        await self._ensure_initialized()

        if self._collection is None:
            return None

        try:
            result = self._collection.get(ids=[memory_id])
            if not result or not result["ids"]:
                return None

            entry = self._parse_chroma_result(result, index=0)
            if entry:
                entry.touch()
            return entry

        except Exception as e:
            logger.error(f"Failed to get memory from ChromaDB: {e}", memory_id=memory_id)
            return None

    async def update(self, entry: MemoryEntry) -> bool:
        """更新记忆条目"""
        await self._ensure_initialized()

        if self._collection is None:
            return False

        try:
            # 重新生成embedding
            entry.content_vector = await get_embedding(entry.content)
            entry.updated_at = datetime.now(timezone.utc)

            metadata = {
                "memory_type": entry.memory_type.value,
                "memory_level": entry.memory_level.value,
                "source_agent": entry.source_agent or "",
                "source_incident_id": entry.source_incident_id or "",
                "importance_score": entry.importance_score,
                "confidence_score": entry.confidence_score,
                "tags": ",".join(entry.tags),
                "summary": entry.summary or "",
            }

            self._collection.update(
                ids=[entry.memory_id],
                embeddings=[entry.content_vector],
                documents=[entry.content],
                metadatas=[metadata],
            )

            logger.debug("Memory updated in ChromaDB", memory_id=entry.memory_id)
            return True

        except Exception as e:
            logger.error(f"Failed to update memory in ChromaDB: {e}", memory_id=entry.memory_id)
            return False

    async def delete(self, memory_id: str) -> bool:
        """删除记忆"""
        await self._ensure_initialized()

        if self._collection is None:
            return False

        try:
            self._collection.delete(ids=[memory_id])
            logger.debug("Memory deleted from ChromaDB", memory_id=memory_id)
            return True

        except Exception as e:
            logger.error(f"Failed to delete memory from ChromaDB: {e}", memory_id=memory_id)
            return False

    async def search(
        self,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[MemoryEntry]:
        """
        语义搜索

        使用向量相似度搜索 + 元数据过滤。
        """
        await self._ensure_initialized()

        if self._collection is None:
            logger.warning("ChromaDB not available, returning empty search results")
            return []

        try:
            # 生成查询向量
            query_vector = await get_embedding(query)

            # 构建过滤条件
            where_filter = self._build_chroma_filter(filters or {})

            # 执行搜索
            results = self._collection.query(
                query_embeddings=[query_vector],
                n_results=top_k,
                where=where_filter if where_filter else None,
            )

            entries: list[MemoryEntry] = []
            if results and results["ids"] and results["ids"][0]:
                for i in range(len(results["ids"][0])):
                    entry = self._parse_chroma_result(results, index=i)
                    if entry:
                        entry.touch()
                        entries.append(entry)

            logger.debug(
                "ChromaDB search completed",
                query=query[:50],
                results_count=len(entries),
            )
            return entries

        except Exception as e:
            logger.error(f"ChromaDB search failed: {e}")
            return []

    def _build_chroma_filter(self, filters: dict[str, Any]) -> dict[str, Any] | None:
        """构建ChromaDB过滤条件"""
        conditions: dict[str, Any] = {}

        if "memory_type" in filters and filters["memory_type"] is not None:
            conditions["memory_type"] = filters["memory_type"].value

        if "memory_level" in filters and filters["memory_level"] is not None:
            conditions["memory_level"] = filters["memory_level"].value

        if filters.get("source_agent"):
            conditions["source_agent"] = filters["source_agent"]

        if filters.get("source_incident_id"):
            conditions["source_incident_id"] = filters["source_incident_id"]

        return conditions if conditions else None

    def _parse_chroma_result(self, result: dict[str, Any], index: int = 0) -> MemoryEntry | None:
        """解析ChromaDB查询结果为MemoryEntry

        兼容两种返回结构:collection.query() 的字段是嵌套列表
        (ids: [["id1", ...]]),collection.get() 的字段是扁平列表
        (ids: ["id1", ...])。此前只按 query() 结构解析,导致按 ID/
        按过滤条件读取(get 路径)永远解析失败。
        """
        try:
            ids = result["ids"]
            nested = bool(len(ids)) and isinstance(ids[0], (list, tuple))

            def _field(name: str) -> Any:
                # embeddings 可能是 numpy 数组,只做 None/长度判断,不做真值判断
                values = result.get(name)
                if values is None:
                    return None
                if nested:
                    if len(values) == 0:
                        return None
                    values = values[0]
                if values is None or len(values) <= index:
                    return None
                return values[index]

            memory_id = ids[0][index] if nested else ids[index]
            document = _field("documents") or ""
            metadata = _field("metadatas") or {}
            embedding = _field("embeddings")
            if embedding is not None and not isinstance(embedding, list):
                embedding = list(embedding)

            return MemoryEntry(
                memory_id=memory_id,
                memory_type=MemoryType(metadata.get("memory_type", "observation")),
                memory_level=MemoryLevel(metadata.get("memory_level", "long_term")),
                content=document,
                content_vector=embedding,
                source_agent=metadata.get("source_agent", ""),
                source_incident_id=metadata.get("source_incident_id", ""),
                source_event_id=metadata.get("source_event_id", ""),
                importance_score=float(metadata.get("importance_score", 0.5)),
                confidence_score=float(metadata.get("confidence_score", 1.0)),
                tags=(
                    metadata.get("tags", "").split(",")
                    if metadata.get("tags")
                    else []
                ),
                summary=metadata.get("summary", ""),
                metadata={
                    k.replace("meta_", ""): v
                    for k, v in metadata.items()
                    if k.startswith("meta_")
                },
            )

        except (IndexError, KeyError, ValueError) as e:
            logger.warning(f"Failed to parse ChromaDB result: {e}")
            return None

    async def get_by_type(
        self,
        memory_type: MemoryType | None = None,
        memory_level: MemoryLevel | None = None,
        agent_id: str = "",
        session_id: str = "",
        limit: int = 100,
    ) -> list[MemoryEntry]:
        """按类型/层级/Agent获取记忆"""
        await self._ensure_initialized()

        if self._collection is None:
            return []

        try:
            # 使用空向量查询 + where过滤
            filters: dict[str, Any] = {}
            if memory_type is not None:
                filters["memory_type"] = memory_type
            if memory_level is not None:
                filters["memory_level"] = memory_level
            if agent_id:
                filters["source_agent"] = agent_id

            where_filter = self._build_chroma_filter(filters)

            result = self._collection.get(
                where=where_filter if where_filter else None,
                limit=limit,
            )

            entries: list[MemoryEntry] = []
            if result and result["ids"]:
                for i in range(len(result["ids"])):
                    entry = self._parse_chroma_result(result, index=i)
                    if entry:
                        entries.append(entry)

            return entries

        except Exception as e:
            logger.error(f"Failed to get memories by type from ChromaDB: {e}")
            return []

    async def clear(self) -> None:
        """清空所有记忆"""
        await self._ensure_initialized()

        if self._collection is not None:
            try:
                self._client.delete_collection(name=self.collection_name)
                self._collection = self._client.get_or_create_collection(
                    name=self.collection_name,
                    metadata={"description": "AIOps memory storage"},
                )
                logger.info("ChromaDB collection cleared")
            except Exception as e:
                logger.error(f"Failed to clear ChromaDB: {e}")

    async def get_stats(self) -> dict[str, Any]:
        """获取存储统计"""
        await self._ensure_initialized()

        count = 0
        if self._collection is not None:
            try:
                count = self._collection.count()
            except Exception:
                pass

        return {
            "backend": "chromadb",
            "collection": self.collection_name,
            "total_entries": count,
            "persist_directory": self.persist_directory,
            "available": self._collection is not None,
        }


# ---------------------------------------------------------------------------
# Legacy storage classes (for backward compatibility)
# ---------------------------------------------------------------------------


class MemoryStorage(BaseStorage):
    """
    向后兼容的存储基类别名

    继承自BaseStorage，用于兼容旧代码。
    """

    @abstractmethod
    async def save(self, entry: MemoryEntry) -> None:
        """保存记忆条目（别名for add）"""
        ...

    async def add(self, entry: MemoryEntry) -> None:
        """添加记忆条目（调用save）"""
        await self.save(entry)


class ChromaStorage(ChromaDBStorage):
    """向后兼容的ChromaDB存储别名"""

    async def save(self, entry: MemoryEntry) -> None:
        """保存记忆条目"""
        await self.add(entry)

    async def get(self, memory_id: str) -> MemoryEntry | None:
        return await super().get(memory_id)

    async def delete(self, memory_id: str) -> bool:
        return await super().delete(memory_id)

    async def search(
        self,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[MemoryEntry]:
        return await super().search(query, top_k, filters)


class SQLiteStorage(BaseStorage):
    """
    SQLite 存储实现（占位）

    基于 SQLite 的结构化存储，适合元数据和关系查询。
    当前为占位实现，可后续扩展。
    """

    def __init__(self, db_path: str = "./data/memory.db") -> None:
        self.db_path = db_path

    async def add(self, entry: MemoryEntry) -> None:
        logger.debug("SQLiteStorage.add not implemented")

    async def get(self, memory_id: str) -> MemoryEntry | None:
        return None

    async def update(self, entry: MemoryEntry) -> bool:
        return False

    async def delete(self, memory_id: str) -> bool:
        return False

    async def search(
        self,
        query: str,
        top_k: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> list[MemoryEntry]:
        return []

    async def get_by_type(
        self,
        memory_type: MemoryType | None = None,
        memory_level: MemoryLevel | None = None,
        agent_id: str = "",
        session_id: str = "",
        limit: int = 100,
    ) -> list[MemoryEntry]:
        return []

    async def clear(self) -> None:
        pass

    async def get_stats(self) -> dict[str, Any]:
        return {"type": "sqlite", "status": "not_implemented"}
