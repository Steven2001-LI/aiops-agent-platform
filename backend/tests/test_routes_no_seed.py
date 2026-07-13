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
