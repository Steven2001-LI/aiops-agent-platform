"""
AIOps Agent Platform - API Endpoint Integration Tests

测试API端点的正常工作，包括故障触发、查询、Agent列表和拓扑查询。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


class TestHealthEndpoints:
    """健康检查端点测试"""

    def test_health_check(self) -> None:
        """测试健康检查端点"""
        client = TestClient(app)
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "aiops-agent-platform"

    def test_readiness_check(self) -> None:
        """测试就绪检查端点"""
        client = TestClient(app)
        response = client.get("/ready")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"

    def test_root_endpoint(self) -> None:
        """测试根路径端点"""
        client = TestClient(app)
        response = client.get("/")

        assert response.status_code == 200
        data = response.json()
        assert "name" in data
        assert "version" in data


class TestIncidentEndpoints:
    """故障管理端点测试"""

    def test_trigger_incident(self) -> None:
        """
        测试触发故障端点

        发送告警事件并验证响应。
        """
        client = TestClient(app)
        alert_data = {
            "service": "order-service",
            "metric": "cpu_usage_percent",
            "value": 95.0,
            "threshold": 80.0,
            "severity": "high",
            "labels": {"environment": "production", "tier": "critical"},
        }
        response = client.post("/api/v1/incidents/trigger", json=alert_data)

        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"
        assert "incident_id" in data
        assert "state" in data

    def test_trigger_incident_critical(self) -> None:
        """测试触发关键故障"""
        client = TestClient(app)
        alert_data = {
            "service": "payment-service",
            "metric": "error_rate_percent",
            "value": 15.0,
            "threshold": 5.0,
            "severity": "critical",
        }
        response = client.post("/api/v1/incidents/trigger", json=alert_data)

        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "accepted"

    def test_list_incidents(self) -> None:
        """
        测试故障列表查询

        验证列表查询返回正确的分页结构。
        """
        client = TestClient(app)
        response = client.get("/api/v1/incidents")

        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data
        assert "page_size" in data

    def test_list_incidents_with_filters(self) -> None:
        """测试带过滤条件的故障列表"""
        client = TestClient(app)
        service = "severity-filter-test-service"
        for severity in ("critical", "high"):
            trigger_response = client.post(
                "/api/v1/incidents/trigger",
                json={
                    "service": service,
                    "metric": f"{severity}_severity_test_metric",
                    "value": 95.0,
                    "threshold": 80.0,
                    "severity": severity,
                },
            )
            assert trigger_response.status_code == 202

        response = client.get(
            "/api/v1/incidents",
            params={"severity": "critical", "service": service},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["items"]
        assert all(item["severity"] == "critical" for item in data["items"])
        assert all(item["service"] == service for item in data["items"])
        assert data["filters"]["severity"] == "critical"
        assert data["filters"]["service"] == service

    def test_get_incident(self) -> None:
        """
        测试故障详情查询

        验证未知 ID 的 404 语义和已创建故障的详情查询。
        """
        client = TestClient(app)
        missing_id = "incident-that-does-not-exist"
        response = client.get(f"/api/v1/incidents/{missing_id}")

        assert response.status_code == 404
        assert response.json()["detail"] == f"Incident {missing_id} not found"

        trigger_response = client.post(
            "/api/v1/incidents/trigger",
            json={
                "service": "get-incident-test-service",
                "metric": "latency_ms",
                "value": 1500.0,
                "threshold": 500.0,
                "severity": "medium",
            },
        )
        assert trigger_response.status_code == 202
        incident_id = trigger_response.json()["incident_id"]

        response = client.get(f"/api/v1/incidents/{incident_id}")
        assert response.status_code == 200
        assert response.json()["incident_id"] == incident_id


class TestAgentEndpoints:
    """Agent管理端点测试"""

    def test_list_agents(self) -> None:
        """
        测试Agent列表

        验证返回所有已注册的Agent。
        """
        client = TestClient(app)
        response = client.get("/api/v1/agents")

        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert data["total"] >= 7  # 至少7个Agent

        # 验证Agent结构
        agents = data["items"]
        assert len(agents) > 0
        for agent in agents:
            assert "agent_id" in agent
            assert "agent_type" in agent
            assert "name" in agent
            assert "status" in agent

    def test_list_agents_with_type_filter(self) -> None:
        """测试按类型过滤Agent列表"""
        client = TestClient(app)
        response = client.get("/api/v1/agents?agent_type=monitor")

        assert response.status_code == 200
        data = response.json()
        # 验证过滤后的结果
        for agent in data["items"]:
            assert agent["agent_type"] == "monitor"

    def test_get_agent_status(self) -> None:
        """测试Agent状态查询"""
        client = TestClient(app)
        response = client.get("/api/v1/agents/monitor-agent-001/status")

        assert response.status_code == 200
        data = response.json()
        assert "agent_id" in data
        assert "status" in data


class TestTopologyEndpoints:
    """拓扑管理端点测试"""

    def test_get_topology(self) -> None:
        """
        测试拓扑查询

        验证返回拓扑结构。
        """
        client = TestClient(app)
        response = client.get("/api/v1/topology")

        assert response.status_code == 200
        data = response.json()
        assert "nodes" in data
        assert "edges" in data

    def test_get_topology_with_service(self) -> None:
        """测试指定服务的拓扑查询"""
        client = TestClient(app)
        response = client.get("/api/v1/topology?service=order-service&depth=2")

        assert response.status_code == 200
        data = response.json()
        assert data["root_service"] == "order-service"
        assert data["depth"] == 2


class TestEvaluationEndpoints:
    """评估管理端点测试"""

    def test_list_evaluations(self) -> None:
        """测试评估列表查询"""
        client = TestClient(app)
        response = client.get("/api/v1/evaluations")

        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data

    def test_run_evaluation(self) -> None:
        """测试执行评估"""
        client = TestClient(app)
        response = client.post("/api/v1/evaluations/run?eval_type=end_to_end")

        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "started"
        assert "eval_id" in data


class TestMemoryEndpoints:
    """记忆管理端点测试"""

    def test_search_memory(self) -> None:
        """测试记忆搜索"""
        client = TestClient(app)
        response = client.get("/api/v1/memory/search?query=cpu%20high&top_k=5")

        assert response.status_code == 200
        data = response.json()
        assert "query" in data
        assert "results" in data
        assert data["query"] == "cpu high"

    def test_store_memory(self) -> None:
        """测试存储记忆"""
        client = TestClient(app)
        response = client.post(
            "/api/v1/memory/store?content=CPU%20usage%20at%2095%25&memory_type=observation"
        )

        assert response.status_code == 200
        data = response.json()
        assert "memory_id" in data
        assert data["status"] == "stored"


class TestDocsEndpoint:
    """API文档端点测试"""

    def test_openapi_json(self) -> None:
        """测试OpenAPI规范"""
        client = TestClient(app)
        response = client.get("/openapi.json")

        assert response.status_code == 200
        data = response.json()
        assert "openapi" in data
        assert "paths" in data

    def test_docs_ui(self) -> None:
        """测试Swagger UI"""
        client = TestClient(app)
        response = client.get("/docs")

        assert response.status_code == 200
        assert "text/html" in response.headers.get("content-type", "")
