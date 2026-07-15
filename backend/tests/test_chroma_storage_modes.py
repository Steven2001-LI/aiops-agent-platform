"""ChromaDBStorage 双模式(嵌入式/服务端)测试。

历史缺陷:docker-compose 声明并启动了独立 chroma 容器,还给后端注入了
CHROMA_DB_HOST/PORT/PATH 三个环境变量——但后端代码一个都不读,
存储硬编码 PersistentClient,容器纯属空转,compose 声明与代码脱节。
本文件锁定修复后的行为:CHROMA_HOST 非空走 HttpClient,否则嵌入式;
连接失败沿用既有降级(collection 置 None,不抛出)。
"""

from __future__ import annotations

import pytest

import chromadb

from app.config import ChromaConfig
from app.memory.storage import ChromaDBStorage


class _StubClient:
    def get_or_create_collection(self, name: str, metadata=None):
        return f"collection:{name}"


@pytest.mark.asyncio
async def test_host_switches_to_http_client(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, object] = {}

    def fake_http(host, port, settings=None):
        calls["http"] = (host, port)
        return _StubClient()

    def fake_persistent(path, settings=None):
        calls["persistent"] = path
        return _StubClient()

    monkeypatch.setattr(chromadb, "HttpClient", fake_http)
    monkeypatch.setattr(chromadb, "PersistentClient", fake_persistent)

    storage = ChromaDBStorage(host="chroma-server", port=9001)
    await storage._ensure_initialized()

    assert calls == {"http": ("chroma-server", 9001)}
    assert storage._collection == "collection:aiops_memory"


@pytest.mark.asyncio
async def test_no_host_uses_embedded_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    calls: dict[str, object] = {}

    def fake_http(host, port, settings=None):
        calls["http"] = (host, port)
        return _StubClient()

    def fake_persistent(path, settings=None):
        calls["persistent"] = path
        return _StubClient()

    monkeypatch.setattr(chromadb, "HttpClient", fake_http)
    monkeypatch.setattr(chromadb, "PersistentClient", fake_persistent)

    storage = ChromaDBStorage(host="", persist_directory=str(tmp_path / "chroma"))
    await storage._ensure_initialized()

    assert calls == {"persistent": str(tmp_path / "chroma")}
    assert storage._collection == "collection:aiops_memory"


@pytest.mark.asyncio
async def test_http_connection_failure_degrades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """服务端连不上时沿用既有降级:collection 置 None,不抛异常。"""

    def broken_http(host, port, settings=None):
        raise ConnectionError("chroma server unreachable")

    monkeypatch.setattr(chromadb, "HttpClient", broken_http)

    storage = ChromaDBStorage(host="chroma-server")
    await storage._ensure_initialized()

    assert storage._client is None
    assert storage._collection is None
    assert storage._initialized is True


def test_chroma_config_parses_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHROMA_HOST", "chroma")
    monkeypatch.setenv("CHROMA_PORT", "8000")
    monkeypatch.setenv("CHROMA_COLLECTION_NAME", "custom_memory")

    cfg = ChromaConfig()

    assert cfg.host == "chroma"
    assert cfg.port == 8000
    assert cfg.collection_name == "custom_memory"


def test_chroma_config_defaults_to_embedded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHROMA_HOST", raising=False)

    cfg = ChromaConfig(_env_file=None)

    assert cfg.host is None
    assert cfg.db_path == "./data/chromadb"
