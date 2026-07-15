"""QueryKnowledgeBaseTool 关键词回退路径测试。

历史缺陷:回退分支读取 KNOWLEDGE_BASE 中不存在的 content/tags 键,
导致向量库不可用时检索永远返回空。本文件锁定修复后的行为:
按真实键(category/symptoms/root_causes/solutions)匹配并支持 category 过滤。
"""

import pytest

from app.tools.knowledge_tools import QueryKnowledgeBaseTool


@pytest.fixture(autouse=True)
def _force_keyword_fallback(monkeypatch):
    """让 ChromaDB 初始化失败,强制走关键词回退分支。"""

    class _BrokenStorage:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("chromadb unavailable in test")

    monkeypatch.setattr("app.memory.storage.ChromaDBStorage", _BrokenStorage)


@pytest.mark.asyncio
async def test_fallback_matches_real_kb_keys():
    """按真实条目键匹配:high cpu + deployment 应命中 kb_001 且排第一。"""
    tool = QueryKnowledgeBaseTool()
    result = await tool.execute(query="high cpu deployment")

    assert result.success
    data = result.data
    assert data["source"] == "keyword_fallback"
    assert data["count"] > 0
    assert data["results"][0]["id"] == "kb_001"
    assert data["results"][0]["score"] > 0


@pytest.mark.asyncio
async def test_fallback_snake_case_query_also_matches():
    """snake_case 原词形查询(high_cpu)与空格形式应同样命中。"""
    tool = QueryKnowledgeBaseTool()
    result = await tool.execute(query="high_cpu")

    assert result.success
    assert result.data["count"] > 0
    assert "kb_001" in [r["id"] for r in result.data["results"]]


@pytest.mark.asyncio
async def test_fallback_category_filter():
    """category 过滤只返回该类条目。"""
    tool = QueryKnowledgeBaseTool()
    result = await tool.execute(query="memory", category="resource_exhaustion")

    assert result.success
    data = result.data
    assert data["count"] > 0
    assert all(r["category"] == "resource_exhaustion" for r in data["results"])


@pytest.mark.asyncio
async def test_fallback_unrelated_query_returns_empty():
    """无关查询返回空结果而不报错。"""
    tool = QueryKnowledgeBaseTool()
    result = await tool.execute(query="zzzz nonexistent xyzzy")

    assert result.success
    data = result.data
    assert data["results"] == []
    assert data["count"] == 0
    assert data["source"] == "keyword_fallback"
