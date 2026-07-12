"""测试 /ready 真实探活"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_ready_returns_dependencies():
    """ /ready 必须返回各依赖的真实状态"""
    from app.main import create_app
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/ready")
        body = resp.json()
        assert "checks" in body
        assert "chromadb" in body["checks"]
        assert "memory" in body["checks"]
        assert "prometheus" in body["checks"]
        assert "knowledge_base" in body["checks"]
        assert "langgraph" in body["checks"]


def test_ready_status_field_valid():
    """/ready 返回的 status 字段值合法"""
    from app.main import create_app
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/ready")
        body = resp.json()
        assert body["status"] in ("ready", "degraded")
        assert "version" in body