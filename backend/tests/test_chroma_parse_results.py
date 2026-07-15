"""_parse_chroma_result 双结构兼容测试。

历史缺陷:该方法只按 collection.query() 的嵌套列表结构解析
(result["ids"][0][index]),但 storage.get()/按过滤读取走的是
collection.get(),其返回字段是扁平列表——metadatas[0][index] 变成
对 dict 取整数键,抛 KeyError 后所有按 ID/全量读取恒返回空,
长期记忆 RRF 融合的关键词检索路因此拿不到候选数据。
"""

from __future__ import annotations

import pytest

from app.memory.storage import ChromaDBStorage
from app.models.memory import MemoryType


def _metadata(memory_id: str) -> dict:
    return {
        "memory_type": "episodic",
        "memory_level": "long_term",
        "source_agent": "rca_agent",
        "source_incident_id": "INC-001",
        "importance_score": 0.8,
        "confidence_score": 1.0,
        "tags": "payment-service,db_pool",
        "summary": f"summary of {memory_id}",
    }


def _query_style_result() -> dict:
    """collection.query() 返回:字段为嵌套列表。"""
    return {
        "ids": [["mem-q-1", "mem-q-2"]],
        "documents": [["doc one", "doc two"]],
        "metadatas": [[_metadata("mem-q-1"), _metadata("mem-q-2")]],
        "embeddings": None,
        "distances": [[0.1, 0.3]],
    }


def _get_style_result() -> dict:
    """collection.get() 返回:字段为扁平列表。"""
    return {
        "ids": ["mem-g-1", "mem-g-2"],
        "documents": ["flat doc one", "flat doc two"],
        "metadatas": [_metadata("mem-g-1"), _metadata("mem-g-2")],
        "embeddings": None,
    }


class TestParseChromaResult:
    def test_parses_nested_query_result(self) -> None:
        storage = ChromaDBStorage()
        entry = storage._parse_chroma_result(_query_style_result(), index=1)

        assert entry is not None
        assert entry.memory_id == "mem-q-2"
        assert entry.content == "doc two"
        assert entry.memory_type == MemoryType.EPISODIC
        assert entry.tags == ["payment-service", "db_pool"]

    def test_parses_flat_get_result(self) -> None:
        """get() 扁平结构此前恒解析失败(KeyError: 0),现在必须成功。"""
        storage = ChromaDBStorage()
        entry = storage._parse_chroma_result(_get_style_result(), index=0)

        assert entry is not None
        assert entry.memory_id == "mem-g-1"
        assert entry.content == "flat doc one"
        assert entry.source_agent == "rca_agent"
        assert entry.importance_score == 0.8

    def test_parses_all_flat_entries_by_index(self) -> None:
        """按过滤条件全量读取的逐 index 解析路径。"""
        storage = ChromaDBStorage()
        result = _get_style_result()
        entries = [
            storage._parse_chroma_result(result, index=i)
            for i in range(len(result["ids"]))
        ]

        assert [e.memory_id for e in entries if e] == ["mem-g-1", "mem-g-2"]

    def test_missing_optional_fields_use_defaults(self) -> None:
        storage = ChromaDBStorage()
        entry = storage._parse_chroma_result({"ids": ["mem-min"]}, index=0)

        assert entry is not None
        assert entry.memory_id == "mem-min"
        assert entry.content == ""
        assert entry.memory_type == MemoryType.OBSERVATION

    def test_out_of_range_index_returns_none_fields_not_crash(self) -> None:
        """索引越界时抛 IndexError 被捕获,返回 None 而非崩溃。"""
        storage = ChromaDBStorage()
        entry = storage._parse_chroma_result(_get_style_result(), index=9)

        assert entry is None


class TestGetPathIntegration:
    @pytest.mark.asyncio
    async def test_storage_get_uses_flat_structure(self, monkeypatch) -> None:
        """storage.get() 走 collection.get() 扁平结构,现在能取回条目。"""

        class _StubCollection:
            def get(self, ids=None, where=None, limit=None):
                return {
                    "ids": ["mem-g-1"],
                    "documents": ["flat doc one"],
                    "metadatas": [_metadata("mem-g-1")],
                }

        storage = ChromaDBStorage()
        storage._collection = _StubCollection()
        storage._initialized = True

        entry = await storage.get("mem-g-1")

        assert entry is not None
        assert entry.memory_id == "mem-g-1"
        assert entry.content == "flat doc one"
