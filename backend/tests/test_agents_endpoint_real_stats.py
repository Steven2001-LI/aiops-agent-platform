"""GET /agents 与 GET /agents/{id}/status 真实统计测试。

历史缺陷:两个端点的执行次数/成功率全部写死(如 monitor 1250 次/98.2%),
与真实执行无关。本文件锁定修复后的行为:
统计来自模块级 Agent 单例的 AgentState,未实例化时诚实报零。
"""

from fastapi.testclient import TestClient

import app.api.routes as routes
from app.agents.monitor_agent import MonitorAgent
from app.main import app


class TestListAgentsRealStats:
    def test_uninstantiated_agents_report_zero_stats(self, monkeypatch) -> None:
        """未实例化的 Agent 统计为 0,不再出现写死的假数。"""
        monkeypatch.setattr(routes, "_monitor_agent", None)
        monkeypatch.setattr(routes, "_rca_agent", None)
        monkeypatch.setattr(routes, "_heal_agent", None)
        monkeypatch.setattr(routes, "_change_agent", None)

        client = TestClient(app)
        response = client.get("/api/v1/agents")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 7
        for agent in data["items"]:
            assert agent["total_executions"] == 0
            assert agent["success_rate"] == 0.0
            assert agent["last_active_at"] is None

    def test_stats_reflect_real_agent_state(self, monkeypatch) -> None:
        """已实例化 Agent 的统计逐次反映真实 record_execution。"""
        agent = MonitorAgent()
        agent.state.record_execution(success=True, execution_time_ms=100.0)
        agent.state.record_execution(success=True, execution_time_ms=200.0)
        agent.state.record_execution(success=False, execution_time_ms=50.0)
        monkeypatch.setattr(routes, "_monitor_agent", agent)

        client = TestClient(app)
        response = client.get("/api/v1/agents?agent_type=monitor")

        assert response.status_code == 200
        items = response.json()["items"]
        assert len(items) == 1
        monitor = items[0]
        assert monitor["total_executions"] == 3
        assert monitor["successful_executions"] == 2
        assert monitor["failed_executions"] == 1
        assert monitor["success_rate"] == round(2 / 3, 4)
        assert monitor["last_active_at"] is not None


class TestAgentStatusRealStats:
    def test_status_reflects_real_agent_state(self, monkeypatch) -> None:
        agent = MonitorAgent()
        agent.state.record_execution(success=True, execution_time_ms=80.0)
        monkeypatch.setattr(routes, "_monitor_agent", agent)

        client = TestClient(app)
        response = client.get("/api/v1/agents/monitor-agent-001/status")

        assert response.status_code == 200
        data = response.json()
        assert data["total_executions"] == 1
        assert data["success_rate"] == 1.0

    def test_uninstantiated_agent_reports_zero(self, monkeypatch) -> None:
        monkeypatch.setattr(routes, "_rca_agent", None)

        client = TestClient(app)
        response = client.get("/api/v1/agents/rca-agent-001/status")

        assert response.status_code == 200
        data = response.json()
        assert data["total_executions"] == 0
        assert data["success_rate"] == 0.0
        # capabilities 来自注册表,而非旧实现的通用英文占位列表
        assert "贝叶斯推理" in data["capabilities"]

    def test_unknown_agent_id_returns_zero_fallback(self) -> None:
        """未知 id 保持零值兜底(不 404),不再返回占位能力列表。"""
        client = TestClient(app)
        response = client.get("/api/v1/agents/no-such-agent/status")

        assert response.status_code == 200
        data = response.json()
        assert data["total_executions"] == 0
        assert data["capabilities"] == []
