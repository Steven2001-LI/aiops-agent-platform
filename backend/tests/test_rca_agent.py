"""
AIOps Agent Platform - RCAAgent Integration Tests

测试RCAAgent的贝叶斯推理、BFS依赖遍历、RAG知识检索和完整根因分析流程。
"""

from __future__ import annotations

import pytest

from app.agents.rca_agent import (
    BayesianNode,
    RCAInput,
    RCAAgent,
    ServiceImpact,
)
from app.models.agent import AgentExecutionContext
from app.models.events import AlertEvent, SeverityLevel


class TestRCAAgent:
    """RCAAgent集成测试套件"""

    @pytest.fixture
    def agent(self) -> RCAAgent:
        """创建RCAAgent实例"""
        return RCAAgent()

    @pytest.fixture
    def context(self) -> AgentExecutionContext:
        """创建Agent执行上下文"""
        return AgentExecutionContext(
            incident_id="test-incident-001",
            input_data={"test": True},
        )

    @pytest.fixture
    def sample_alert(self) -> AlertEvent:
        """创建测试告警"""
        return AlertEvent(
            service="order-service",
            metric="cpu_usage_percent",
            value=95.0,
            threshold=80.0,
            severity=SeverityLevel.HIGH,
            labels={"environment": "production", "tier": "critical"},
        )

    # ========================================================================
    # Bayesian Inference Tests
    # ========================================================================

    def test_bayesian_inference_basic(self, agent: RCAAgent) -> None:
        """
        测试贝叶斯推理 - 基本功能

        确保贝叶斯推理能正确计算后验概率。
        """
        symptoms = ["high_cpu", "increased_latency"]
        results = agent.bayesian_inference(symptoms)

        assert len(results) > 0
        assert isinstance(results[0], BayesianNode)

        # 按后验概率降序排序
        for i in range(len(results) - 1):
            assert results[i].posterior >= results[i + 1].posterior

        # 后验概率应在[0, 1]范围内
        for r in results:
            assert 0.0 <= r.posterior <= 1.0
            assert 0.0 <= r.prior <= 1.0
            assert 0.0 <= r.likelihood <= 1.0
            assert 0.0 <= r.evidence_strength <= 1.0

    def test_bayesian_inference_empty_symptoms(self, agent: RCAAgent) -> None:
        """测试空症状的贝叶斯推理"""
        results = agent.bayesian_inference([])

        assert len(results) > 0
        # 空症状时，后验应接近先验
        for r in results:
            assert abs(r.posterior - r.prior) < 0.5

    def test_bayesian_inference_single_symptom(self, agent: RCAAgent) -> None:
        """测试单症状的贝叶斯推理"""
        results = agent.bayesian_inference(["high_cpu"])

        assert len(results) > 0
        # high_cpu相关的根因应该有更高的后验
        traffic_spike = next((r for r in results if r.name == "traffic_spike"), None)
        if traffic_spike:
            assert traffic_spike.posterior > 0

    def test_bayesian_prior_probabilities(self, agent: RCAAgent) -> None:
        """测试先验概率配置"""
        assert len(agent.PRIOR_PROBABILITIES) > 0
        # 所有先验概率之和应接近1（大致检查）
        total = sum(agent.PRIOR_PROBABILITIES.values())
        assert 0.5 < total < 2.0

    def test_bayesian_likelihoods(self, agent: RCAAgent) -> None:
        """测试似然概率配置"""
        assert len(agent.LIKELIHOODS) > 0
        for cause, symptoms in agent.LIKELIHOODS.items():
            assert len(symptoms) > 0
            for symptom, prob in symptoms.items():
                assert 0.0 <= prob <= 1.0

    def test_calculate_p_symptoms(self, agent: RCAAgent) -> None:
        """测试P(症状)计算"""
        p = agent._calculate_p_symptoms(["high_cpu"])
        assert p > 0.0

        p_empty = agent._calculate_p_symptoms([])
        assert p_empty == 1.0

    # ========================================================================
    # BFS Traversal Tests
    # ========================================================================

    def test_bfs_traverse_basic(self, agent: RCAAgent) -> None:
        """
        测试BFS依赖遍历 - 基本功能

        从order-service出发，确保能遍历到依赖服务。
        """
        results = agent.bfs_traverse("order-service", max_hops=2)

        assert len(results) > 0
        assert results[0].service == "order-service"
        assert results[0].hop_distance == 0

        # 检查发现了依赖服务
        services = [r.service for r in results]
        assert len(services) >= 1

    def test_bfs_traverse_max_hops(self, agent: RCAAgent) -> None:
        """测试BFS最大跳数限制"""
        results_1 = agent.bfs_traverse("order-service", max_hops=1)
        results_3 = agent.bfs_traverse("order-service", max_hops=3)

        # 更多跳数应发现更多服务（或相等）
        assert len(results_3) >= len(results_1)

    def test_bfs_traverse_unknown_service(self, agent: RCAAgent) -> None:
        """测试BFS遍历未知服务"""
        results = agent.bfs_traverse("unknown-service-xyz", max_hops=2)
        assert len(results) == 1
        assert results[0].service == "unknown-service-xyz"

    def test_bfs_traverse_impact_levels(self, agent: RCAAgent) -> None:
        """测试BFS影响级别"""
        results = agent.bfs_traverse("order-service", max_hops=2)

        for impact in results:
            assert impact.impact_level in ["direct", "indirect", "peripheral", "unknown"]
            assert impact.hop_distance >= 0

    def test_create_impact_entry(self, agent: RCAAgent) -> None:
        """测试影响条目创建"""
        entry = agent._create_impact_entry("order-service", 0)
        assert isinstance(entry, ServiceImpact)
        assert entry.service == "order-service"
        assert entry.hop_distance == 0

    # ========================================================================
    # RAG Retrieval Tests
    # ========================================================================

    def test_rag_retrieval(self, agent: RCAAgent) -> None:
        """
        测试RAG知识检索

        确保能从知识库检索到相关案例。
        """
        alert = AlertEvent(
            service="order-service",
            metric="cpu_usage_percent",
            value=95.0,
            threshold=80.0,
            severity=SeverityLevel.HIGH,
        )
        symptoms = ["high_cpu", "increased_latency"]
        results = agent._rag_retrieve(alert, symptoms)

        assert isinstance(results, list)
        # 即使知识库为空也不应报错
        for r in results:
            assert "id" in r
            assert "category" in r

    def test_kb_match_score(self, agent: RCAAgent) -> None:
        """测试知识库匹配得分计算"""
        kb_entry = {
            "id": "kb_001",
            "category": "resource_exhaustion",
            "symptoms": ["high_cpu", "increased_latency"],
            "confidence_boost": 0.1,
        }
        alert = AlertEvent(
            service="order-service",
            metric="cpu_usage_percent",
            value=95.0,
            threshold=80.0,
        )
        symptoms = ["high_cpu", "increased_latency"]
        score = agent._calculate_kb_match_score(kb_entry, alert, symptoms)

        assert score > 0.3  # 良好匹配应得分较高
        assert score <= 1.0

    def test_kb_match_score_empty(self, agent: RCAAgent) -> None:
        """测试空知识库条目的匹配得分"""
        kb_entry = {"id": "empty_001"}
        alert = AlertEvent(
            service="order-service",
            metric="cpu_usage_percent",
            value=95.0,
        )
        score = agent._calculate_kb_match_score(kb_entry, alert, [])
        assert 0.0 <= score <= 1.0

    # ========================================================================
    # Symptom Extraction Tests
    # ========================================================================

    def test_extract_symptoms_cpu(self, agent: RCAAgent) -> None:
        """测试CPU指标的症状提取"""
        alert = AlertEvent(
            service="order-service",
            metric="cpu_usage_percent",
            value=95.0,
            threshold=80.0,
        )
        symptoms = agent._extract_symptoms(alert)
        assert "high_cpu" in symptoms

    def test_extract_symptoms_memory(self, agent: RCAAgent) -> None:
        """测试内存指标的症状提取"""
        alert = AlertEvent(
            service="payment-service",
            metric="memory_usage_percent",
            value=92.0,
            threshold=85.0,
        )
        symptoms = agent._extract_symptoms(alert)
        assert "high_memory" in symptoms

    def test_extract_symptoms_latency(self, agent: RCAAgent) -> None:
        """测试延迟指标的症状提取"""
        alert = AlertEvent(
            service="user-service",
            metric="p99_latency_ms",
            value=5000.0,
            threshold=1000.0,
        )
        symptoms = agent._extract_symptoms(alert)
        assert "increased_latency" in symptoms

    # ========================================================================
    # Synthesis Tests
    # ========================================================================

    def test_synthesize_analysis(self, agent: RCAAgent) -> None:
        """测试综合分析"""
        bayesian_results = [
            BayesianNode(
                name="traffic_spike",
                prior=0.18,
                likelihood=0.8,
                posterior=0.85,
                evidence_strength=0.9,
            ),
            BayesianNode(
                name="recent_deployment",
                prior=0.15,
                likelihood=0.6,
                posterior=0.5,
                evidence_strength=0.7,
            ),
        ]
        rag_results = [
            {
                "id": "kb_001",
                "category": "deployment_issue",
                "match_score": 0.8,
                "root_causes": ["recent_deployment"],
                "confidence_boost": 0.2,
            },
        ]
        impact_chain = [
            ServiceImpact(service="order-service", hop_distance=0, tier="critical"),
        ]
        alert = AlertEvent(
            service="order-service",
            metric="cpu_usage_percent",
            value=95.0,
            threshold=80.0,
        )

        root_cause, confidence, evidence = agent._synthesize_analysis(
            bayesian_results, rag_results, impact_chain, alert
        )

        assert root_cause != ""
        assert 0.0 <= confidence <= 1.0
        assert "bayesian_top" in evidence
        assert "affected_services" in evidence

    # ========================================================================
    # Suggested Actions Tests
    # ========================================================================

    def test_generate_suggested_actions(self, agent: RCAAgent) -> None:
        """测试建议操作生成"""
        actions = agent._generate_suggested_actions(
            "resource_exhaustion",
            [ServiceImpact(service="order-service", hop_distance=0, tier="critical")],
            [{"solutions": ["scale_up_resources"]}],
        )
        assert len(actions) > 0
        assert "scale_up_resources" in actions

    def test_generate_suggested_actions_unknown_cause(self, agent: RCAAgent) -> None:
        """测试未知根因的建议操作"""
        actions = agent._generate_suggested_actions(
            "unknown_cause",
            [],
            [],
        )
        assert len(actions) > 0
        assert "investigate_manually" in actions

    # ========================================================================
    # Full RCA Process Integration Tests
    # ========================================================================

    @pytest.mark.asyncio
    async def test_full_rca_process(
        self,
        agent: RCAAgent,
        sample_alert: AlertEvent,
        context: AgentExecutionContext,
    ) -> None:
        """
        测试完整根因分析流程

        端到端测试RCAAgent的完整处理流程。
        """
        rca_input = RCAInput(
            alert=sample_alert,
            incident_id="test-incident-001",
        )
        result = await agent.process(rca_input, context)

        assert result.success is True
        assert result.agent_name == "rca_agent"
        assert "rca_event" in result.output_data
        assert "root_cause" in result.output_data
        assert "confidence" in result.output_data
        assert result.output_data["confidence"] >= 0.0

    @pytest.mark.asyncio
    async def test_full_rca_latency_scenario(
        self,
        agent: RCAAgent,
        context: AgentExecutionContext,
    ) -> None:
        """测试延迟场景的完整RCA流程"""
        alert = AlertEvent(
            service="api-gateway",
            metric="p99_latency_ms",
            value=3000.0,
            threshold=500.0,
            severity=SeverityLevel.CRITICAL,
            labels={"tier": "critical"},
        )
        rca_input = RCAInput(
            alert=alert,
            incident_id="test-incident-latency",
            max_hops=3,
        )
        result = await agent.process(rca_input, context)

        assert result.success is True
        assert "rca_event" in result.output_data
        assert "bayesian_results" in result.output_data
        assert "impact_services_count" in result.output_data


class TestFindRecentChanges:
    """近期变更双源查询:平台 ChangeEvent 史(源1) + CHANGE_RECORDS 数据集兜底(源2)"""

    @staticmethod
    def _make_incident_with_change(
        service: str,
        minutes_ago: int,
        change_type: str = "auto_heal",
    ):
        from datetime import datetime, timedelta, timezone

        from app.models.events import ApprovalStatus, ChangeEvent
        from app.models.incident import Incident

        alert = AlertEvent(
            service=service,
            metric="cpu_usage_percent",
            value=95.0,
            threshold=80.0,
            severity=SeverityLevel.HIGH,
        )
        incident = Incident.from_alert(alert)
        incident.change_events.append(
            ChangeEvent(
                incident_id=incident.incident_id,
                change_id="CHG-TEST-1",
                change_type=change_type,
                approval_status=ApprovalStatus.AUTO_APPROVED,
                risk_score=0.2,
                risk_level="low",
                automated_decision_reason="test change",
                timestamp=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
            )
        )
        return incident

    @pytest.fixture
    def isolated_service(self, tmp_path):
        """隔离的 IncidentService 实例,不碰全局单例与真实 SQLITE_PATH"""
        from app.services.incident_service import IncidentService

        return IncidentService(db_path=str(tmp_path / "changes.db"))

    def test_incident_history_source(self, isolated_service) -> None:
        """平台自产 ChangeEvent 史进入近期变更(source=incident_history)"""
        agent = RCAAgent(incident_service=isolated_service)
        incident = self._make_incident_with_change("payment-service", minutes_ago=10)
        isolated_service._incidents[incident.incident_id] = incident

        changes = agent._find_recent_changes("payment-service")
        history = [c for c in changes if c["source"] == "incident_history"]
        assert history, f"expected incident_history entries, got {changes}"
        assert history[0]["change_id"] == "CHG-TEST-1"
        assert history[0]["status"] == "auto_approved"
        assert history[0]["type"] == "auto_heal"

    def test_lookback_window(self, isolated_service) -> None:
        """时间窗过滤:2 小时前的变更在 60 分钟窗外、180 分钟窗内"""
        agent = RCAAgent(incident_service=isolated_service)
        incident = self._make_incident_with_change("payment-service", minutes_ago=120)
        isolated_service._incidents[incident.incident_id] = incident

        within_60 = [
            c for c in agent._find_recent_changes("payment-service", lookback_minutes=60)
            if c["source"] == "incident_history"
        ]
        within_180 = [
            c for c in agent._find_recent_changes("payment-service", lookback_minutes=180)
            if c["source"] == "incident_history"
        ]
        assert not within_60
        assert within_180

    def test_exclude_self(self, isolated_service) -> None:
        """当前正在分析的事故自身的变更不作为'历史'证据"""
        agent = RCAAgent(incident_service=isolated_service)
        incident = self._make_incident_with_change("payment-service", minutes_ago=5)
        isolated_service._incidents[incident.incident_id] = incident

        history = [
            c
            for c in agent._find_recent_changes(
                "payment-service", exclude_incident_id=incident.incident_id
            )
            if c["source"] == "incident_history"
        ]
        assert not history

    def test_dataset_fallback(self, isolated_service) -> None:
        """无平台变更史时,CHANGE_RECORDS 数据集(模拟 CMDB)兜底;未知服务返回空"""
        agent = RCAAgent(incident_service=isolated_service)

        changes = agent._find_recent_changes("order-service")
        assert changes, "order-service 应有数据集兜底变更"
        assert all(c["source"] == "change_records" for c in changes)
        assert any(c["type"] == "deployment" for c in changes)

        assert agent._find_recent_changes("redis-cache") == []

    def test_changes_sorted_and_capped(self, isolated_service) -> None:
        """双源合并按时间倒序,最多 5 条"""
        agent = RCAAgent(incident_service=isolated_service)
        for i in range(6):
            incident = self._make_incident_with_change("order-service", minutes_ago=i + 1)
            isolated_service._incidents[incident.incident_id] = incident

        changes = agent._find_recent_changes("order-service")
        assert len(changes) == 5
        times = [c["time"] for c in changes]
        assert times == sorted(times, reverse=True)

    @pytest.mark.asyncio
    async def test_process_evidence_contains_related_changes(
        self, isolated_service
    ) -> None:
        """消费闭环:evidence.impact_chain_detail 序列化出 related_changes"""
        agent = RCAAgent(incident_service=isolated_service)
        alert = AlertEvent(
            service="order-service",
            metric="cpu_usage_percent",
            value=95.0,
            threshold=80.0,
            severity=SeverityLevel.HIGH,
        )
        result = await agent.process(
            RCAInput(alert=alert, incident_id="test-incident-changes"),
            AgentExecutionContext(incident_id="test-incident-changes", input_data={}),
        )

        assert result.success is True
        detail = result.output_data["rca_event"]["evidence"]["impact_chain_detail"]
        assert all("related_changes" in entry for entry in detail)
        order_entry = next(e for e in detail if e["service"] == "order-service")
        assert isinstance(order_entry["related_changes"], list)
        assert len(order_entry["related_changes"]) <= 3
