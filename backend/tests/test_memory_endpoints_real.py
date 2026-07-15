"""/memory/search 与 /memory/store 真实接入测试。

历史缺陷:search 返回 6 条内联假记忆(MEM-001~006),store 只生成假 ID 不落任何存储。
本文件锁定修复后的行为:两个端点经 _get_memory_system 接真实 MemorySystem 接口,
字段映射自 MemoryEntry;系统不可用时 search 降级空结果、store 返回 503。

测试注入 FakeMemorySystem(与 MemorySystem.store/search 同签名、返回真实
Pydantic 模型),避免真实单例的 asyncio.Lock 跨 TestClient 事件循环绑定问题。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import app.api.routes as routes
from app.main import app
from app.models.memory import MemoryEntry, MemoryQueryResult, MemoryType


class FakeMemorySystem:
    """与 MemorySystem 检索/存储接口同形的进程内假实现。"""

    def __init__(self) -> None:
        self.entries: list[MemoryEntry] = []

    async def store(
        self,
        content: str,
        memory_type: MemoryType = MemoryType.OBSERVATION,
        source_agent: str = "",
        source_incident_id: str = "",
        importance: float = 0.5,
        tags: list[str] | None = None,
        **kwargs,
    ) -> MemoryEntry:
        entry = MemoryEntry(
            content=content,
            memory_type=memory_type,
            source_agent=source_agent,
            source_incident_id=source_incident_id,
            importance_score=importance,
            tags=tags or [],
        )
        self.entries.append(entry)
        return entry

    async def search(
        self,
        query_text: str,
        top_k: int = 10,
        memory_type: MemoryType | None = None,
        incident_id: str = "",
        agent_id: str = "",
    ) -> MemoryQueryResult:
        hits = [
            e
            for e in self.entries
            if query_text.lower() in e.content.lower()
            and (memory_type is None or e.memory_type == memory_type)
        ][:top_k]
        return MemoryQueryResult(
            results=hits,
            similarities=[0.9] * len(hits),
            total_found=len(hits),
            query_time_ms=1.5,
        )


def install_fake_memory_system(monkeypatch) -> FakeMemorySystem:
    """把端点的记忆系统 getter 替换为 Fake,返回 fake 供测试预置数据。"""
    fake = FakeMemorySystem()

    async def _fake_getter():
        return fake

    monkeypatch.setattr(routes, "_get_memory_system", _fake_getter)
    return fake


class TestSearchMemoryReal:
    def test_search_maps_memory_entry_fields(self, monkeypatch) -> None:
        """结果来自 MemorySystem 检索,entry 字段映射自 MemoryEntry。"""
        fake = install_fake_memory_system(monkeypatch)
        entry = MemoryEntry(
            content="payment-service 连接池耗尽,扩容后恢复",
            memory_type=MemoryType.EPISODIC,
            importance_score=0.8,
            tags=["payment-service", "db_pool"],
        )
        fake.entries.append(entry)

        client = TestClient(app)
        response = client.get("/api/v1/memory/search?query=连接池")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        got = data["results"][0]
        assert got["entry"]["memory_id"] == entry.memory_id
        assert got["entry"]["value"] == entry.content
        assert got["entry"]["type"] == "episodic"
        assert got["entry"]["tags"] == ["payment-service", "db_pool"]
        assert got["score"] == 0.9

    def test_search_empty_store_returns_empty(self, monkeypatch) -> None:
        """空库诚实返回空列表,不再出现内联假记忆。"""
        install_fake_memory_system(monkeypatch)

        client = TestClient(app)
        response = client.get("/api/v1/memory/search?query=connection_pool")

        assert response.status_code == 200
        data = response.json()
        assert data["results"] == []
        assert data["total"] == 0

    def test_search_invalid_memory_type_400(self, monkeypatch) -> None:
        """非法 memory_type(如旧假数据词表 knowledge)返回 400。"""
        install_fake_memory_system(monkeypatch)

        client = TestClient(app)
        response = client.get("/api/v1/memory/search?query=x&memory_type=knowledge")

        assert response.status_code == 400

    def test_search_memory_system_unavailable_degrades_to_empty(
        self, monkeypatch
    ) -> None:
        async def _none_getter():
            return None

        monkeypatch.setattr(routes, "_get_memory_system", _none_getter)

        client = TestClient(app)
        response = client.get("/api/v1/memory/search?query=anything")

        assert response.status_code == 200
        assert response.json()["results"] == []


class TestStoreMemoryReal:
    def test_store_then_search_roundtrip(self, monkeypatch) -> None:
        """存→搜闭环:store 真实写入,search 能搜回同一条记忆。"""
        fake = install_fake_memory_system(monkeypatch)

        client = TestClient(app)
        store_resp = client.post(
            "/api/v1/memory/store?content=redis 缓存穿透用布隆过滤器修复&memory_type=semantic"
        )
        assert store_resp.status_code == 200
        memory_id = store_resp.json()["memory_id"]
        assert store_resp.json()["status"] == "stored"
        assert len(fake.entries) == 1

        search_resp = client.get("/api/v1/memory/search?query=布隆过滤器")
        assert search_resp.status_code == 200
        results = search_resp.json()["results"]
        assert len(results) == 1
        assert results[0]["entry"]["memory_id"] == memory_id

    def test_store_memory_system_unavailable_503(self, monkeypatch) -> None:
        async def _none_getter():
            return None

        monkeypatch.setattr(routes, "_get_memory_system", _none_getter)

        client = TestClient(app)
        response = client.post("/api/v1/memory/store?content=x")

        assert response.status_code == 503
