"""GET /evaluations 真实历史测试。

历史缺陷:该端点返回 4 条硬编码假数据(EVAL-001~004),与任何真实评测运行无关。
本文件锁定修复后的行为:POST /evaluations/run 成功后按维度落入历史,
GET /evaluations 返回真实运行记录(空历史返回空列表),分页与过滤真实生效。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import routes as routes_module
from app.main import app
from tests.test_eval_data_plane import make_incident


@pytest.fixture(autouse=True)
def _clean_evaluation_runs():
    """隔离模块级评测历史,避免跨用例状态泄漏。"""
    routes_module._evaluation_runs.clear()
    yield
    routes_module._evaluation_runs.clear()


def test_empty_history_returns_empty_list() -> None:
    """未跑过评测时返回空列表,而非硬编码假数据。"""
    client = TestClient(app)
    response = client.get("/api/v1/evaluations")

    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total"] == 0


def test_run_then_list_returns_real_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """先 run 后 list:历史来自真实评测报告,分值为 0~100 口径。"""
    incident = make_incident("fs_002", "memory_leak")
    monkeypatch.setattr(
        routes_module._incident_service, "_incidents", {incident.incident_id: incident}
    )
    client = TestClient(app)

    run_resp = client.post("/api/v1/evaluations/run?eval_type=reasoning")
    assert run_resp.status_code == 202
    eval_id = run_resp.json()["eval_id"]

    list_resp = client.get("/api/v1/evaluations")
    assert list_resp.status_code == 200
    data = list_resp.json()
    assert data["total"] > 0

    item = data["items"][0]
    assert item["run_id"] == eval_id
    assert item["eval_type"] == "reasoning"
    assert item["dimension"] == "reasoning"
    assert 0.0 <= item["overall_score"] <= 100.0
    assert item["metrics"], "维度记录应包含指标明细"
    for metric in item["metrics"]:
        assert 0.0 <= metric["score"] <= 100.0
    assert item["timestamp"]


def test_eval_type_filter_and_pagination(monkeypatch: pytest.MonkeyPatch) -> None:
    """eval_type 过滤与分页切片真实生效。"""
    incident = make_incident("fs_002", "memory_leak")
    monkeypatch.setattr(
        routes_module._incident_service, "_incidents", {incident.incident_id: incident}
    )
    client = TestClient(app)

    for _ in range(3):
        assert (
            client.post("/api/v1/evaluations/run?eval_type=reasoning").status_code
            == 202
        )

    filtered = client.get("/api/v1/evaluations?eval_type=reasoning").json()
    assert filtered["total"] == 3
    assert all(i["eval_type"] == "reasoning" for i in filtered["items"])

    other = client.get("/api/v1/evaluations?eval_type=rag").json()
    assert other["total"] == 0

    paged = client.get("/api/v1/evaluations?page=2&page_size=2").json()
    assert paged["total"] == 3
    assert len(paged["items"]) == 1
