"""测试 routes.py 不再在模块加载时 seed incidents"""
from __future__ import annotations


def test_import_routes_no_seed():
    """导入 routes 后 _incident_service 不应有 seed 数据（模块顶层无调用）"""
    # 通过行为验证：导入 routes 后 incidents 字典应为空
    import importlib
    import sys
    # 强制重新加载以确保无副作用残留
    if "app.api.routes" in sys.modules:
        del sys.modules["app.api.routes"]
    import app.api.routes as routes_module
    # 行为验证：导入后 incidents 应为空（因为 _seed_incidents() 不再被自动调用）
    assert len(routes_module._incident_service._incidents) == 0, (
        f"Expected 0 incidents after import, got {len(routes_module._incident_service._incidents)}"
    )


def test_seed_demo_endpoint_exists():
    """/incidents/seed-demo 端点必须存在"""
    from app.main import create_app
    app = create_app()
    assert "/api/v1/incidents/seed-demo" in app.openapi()["paths"], (
        "seed-demo endpoint should be registered"
    )


def test_seed_demo_disabled_by_default():
    """演示种子默认关:未设 APP_ENABLE_DEMO_SEED 时端点 403,不注入任何数据"""
    from fastapi.testclient import TestClient

    import app.api.routes as routes_module
    from app.main import create_app

    routes_module._incident_service._incidents.clear()
    app = create_app()
    with TestClient(app) as client:
        resp = client.post("/api/v1/incidents/seed-demo")
        assert resp.status_code == 403
        assert "APP_ENABLE_DEMO_SEED" in resp.json()["detail"]
    assert len(routes_module._incident_service._incidents) == 0


def test_seed_demo_enabled_by_flag(monkeypatch):
    """APP_ENABLE_DEMO_SEED=true 时端点注入 7 条演示事故

    注意:不能 monkeypatch routes 模块属性 — 本文件的 test_import_routes_no_seed
    会重导 app.api.routes,而 app 里注册的 handler 属于旧模块对象;
    改走环境变量 + reload_config(单例整体替换),对模块身份不敏感。
    """
    from fastapi.testclient import TestClient

    from app.config import reload_config
    from app.main import create_app
    from app.services.incident_service import get_incident_service

    service = get_incident_service()
    monkeypatch.setenv("APP_ENABLE_DEMO_SEED", "true")
    reload_config()
    service._incidents.clear()
    try:
        app = create_app()
        with TestClient(app) as client:
            # lifespan 已按开关自动预置;端点幂等,返回当前种子清单
            resp = client.post("/api/v1/incidents/seed-demo")
            assert resp.status_code == 200
            body = resp.json()
            assert body["count"] == 7
            assert "INC-2026-001" in body["incident_ids"]
    finally:
        service._incidents.clear()
        monkeypatch.delenv("APP_ENABLE_DEMO_SEED", raising=False)
        reload_config()


def test_lifespan_seeds_when_flag_enabled(monkeypatch):
    """开关打开时启动即预置 7 条演示事故(启动预置语义在开关下恢复)"""
    from fastapi.testclient import TestClient

    import app.api.routes as routes_module
    from app.config import reload_config
    from app.main import create_app

    monkeypatch.setenv("APP_ENABLE_DEMO_SEED", "true")
    reload_config()
    routes_module._incident_service._incidents.clear()
    try:
        app = create_app()
        with TestClient(app):
            assert len(routes_module._incident_service._incidents) == 7
    finally:
        routes_module._incident_service._incidents.clear()
        monkeypatch.delenv("APP_ENABLE_DEMO_SEED", raising=False)
        reload_config()
