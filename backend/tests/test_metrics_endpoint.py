"""测试 /metrics 真实指标"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_metrics_contains_app_up():
    """/metrics 必须包含 app_up"""
    from app.main import create_app
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/metrics")
        text = resp.text
        assert "app_up" in text
        assert "app_up 1.0" in text


def test_metrics_counts_http_requests():
    """/metrics 必须真实统计 HTTP 请求"""
    from app.main import create_app
    app = create_app()
    with TestClient(app) as client:
        # 先做几次调用
        client.get("/health")
        client.get("/health")
        resp = client.get("/metrics")
        text = resp.text
        # prometheus 客户端 label 顺序为 method,path,status（字典序）
        # 验证 path="/health" 出现在 metrics 中且计数 ≥ 2
        import re
        match = re.search(r'http_requests_total\{[^}]*path="/health"[^}]*\}\s+(\d+)', text)
        assert match is not None, "path=\"/health\" metric not found"
        assert int(match.group(1)) >= 2, f"count={match.group(1)}, expected >= 2"


def test_metrics_skips_self():
    """/metrics 调用不应被自身计数（避免递归）"""
    from app.main import create_app
    app = create_app()
    with TestClient(app) as client:
        client.get("/metrics")
        resp = client.get("/metrics")
        text = resp.text
        # 不应有 path="/metrics" 的计数（按 label 顺序搜任意位置）
        assert 'path="/metrics"' not in text