"""
AIOps Agent Platform - Unit Tests for All Agents

测试覆盖：
1. MonitorAgent - 异常检测、投票机制、去重
2. RCAAgent - 贝叶斯推理、BFS遍历、RAG检索
3. HealAgent - Playbook匹配、dry-run、爆炸半径评估
4. ChangeAgent - 风险评分、分级审批、审计日志
5. MemoryAgent - 存储检索、混合搜索、记忆管理
6. EvalAgent - 端到端评估、推理评估、报告生成
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone

# Ensure the app module is importable
sys.path.insert(0, "/mnt/agents/output/aiops-agent-platform/backend")

import pytest

from app.agents.monitor_agent import (
    AlgorithmVote,
    AnomalyDetectionResult,
    MetricInput,
    MonitorAgent,
)
from app.agents.rca_agent import BayesianNode, RCAInput, RCAAgent, ServiceImpact
from app.agents.heal_agent import BlastRadiusResult, CircuitBreaker, CircuitBreakerState, HealInput, HealAgent, HealLevel
from app.agents.change_agent import ChangeAgent, ChangeInput, RiskFactor
from app.agents.memory_agent import MemoryAgent, MemoryInput
from app.agents.eval_agent import EvalAgent, EvalInput, EvalType

from app.models.agent import AgentExecutionContext
from app.models.events import (
    AlertEvent,
    ApprovalStatus,
    ChangeEvent,
    HealEvent,
    RCAEvent,
    SeverityLevel,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def agent_context() -> AgentExecutionContext:
    """创建测试用的 AgentExecutionContext"""
    return AgentExecutionContext(
        incident_id="test-incident-001",
        input_data={"test": True},
    )


@pytest.fixture
def sample_alert() -> AlertEvent:
    """创建测试用的 AlertEvent"""
    return AlertEvent(
        service="order-service",
        metric="cpu_usage_percent",
        value=95.0,
        threshold=80.0,
        severity=SeverityLevel.HIGH,
        labels={"environment": "production", "tier": "critical"},
        annotations={"description": "CPU usage is very high"},
    )


@pytest.fixture
def sample_rca_event(sample_alert: AlertEvent) -> RCAEvent:
    """创建测试用的 RCAEvent"""
    return RCAEvent(
        incident_id="test-incident-001",
        root_cause="traffic_spike",
        confidence=0.85,
        impact_chain=["order-service", "api-gateway"],
        evidence={
            "alert_metric": "cpu_usage_percent",
            "alert_value": 95.0,
            "affected_services": ["order-service"],
            "critical_services_affected": ["order-service"],
        },
        recommended_actions=["scale_up_resources", "enable_rate_limiting"],
    )


@pytest.fixture
def sample_heal_event(sample_rca_event: RCAEvent) -> HealEvent:
    """创建测试用的 HealEvent"""
    return HealEvent(
        incident_id="test-incident-001",
        action="scale_up",
        action_category="pb_high_cpu",
        level="L0",
        target_resource="order-service",
        dry_run=True,
        dry_run_result={
            "all_executable": True,
            "results": [],
            "playbook_matched": "pb_high_cpu",
            "blast_radius_info": {
                "blast_radius_ratio": 0.05,
                "affected_service_count": 1,
                "total_service_count": 8,
            },
        },
        requires_approval=False,
    )


# ============================================================
# MonitorAgent Tests
# ============================================================

class TestMonitorAgent:
    """MonitorAgent 测试套件"""

    @pytest.fixture
    def agent(self) -> MonitorAgent:
        return MonitorAgent()

    @pytest.fixture
    def normal_metric(self) -> MetricInput:
        return MetricInput(
            metric_name="cpu_usage_percent",
            metric_value=45.0,
            service_name="order-service",
            history_values=[40.0, 42.0, 43.0, 41.0, 44.0, 42.0, 45.0, 43.0, 41.0, 42.0],
        )

    @pytest.fixture
    def anomaly_metric(self) -> MetricInput:
        return MetricInput(
            metric_name="cpu_usage_percent",
            metric_value=98.0,
            service_name="order-service",
            history_values=[40.0, 42.0, 43.0, 41.0, 44.0, 42.0, 45.0, 43.0, 41.0, 42.0],
            labels={"environment": "production", "tier": "critical"},
        )

    def test_agent_metadata(self, agent: MonitorAgent) -> None:
        """测试 Agent 元数据"""
        assert agent.get_name() == "monitor_agent"
        assert "监控告警" in agent.get_description()
        assert agent.get_version() == "1.0.0"

    @pytest.mark.asyncio
    async def test_detect_anomaly_normal(
        self,
        agent: MonitorAgent,
        normal_metric: MetricInput,
    ) -> None:
        """测试正常数据的异常检测"""
        # 先注入历史数据
        agent._update_history(normal_metric)
        agent._update_adaptive_thresholds(normal_metric)

        result = agent.detect_anomaly(normal_metric)

        assert isinstance(result, AnomalyDetectionResult)
        assert result.is_anomaly is False
        assert len(result.algorithms_voted) == 3
        assert result.algorithm_consensus in ["minority", "unanimous"]
        assert 0.0 <= result.confidence <= 1.0

    @pytest.mark.asyncio
    async def test_detect_anomaly_anomaly(
        self,
        agent: MonitorAgent,
        anomaly_metric: MetricInput,
    ) -> None:
        """测试异常数据的检测"""
        # 先注入历史数据
        agent._update_history(anomaly_metric)
        agent._update_adaptive_thresholds(anomaly_metric)

        result = agent.detect_anomaly(anomaly_metric)

        assert isinstance(result, AnomalyDetectionResult)
        assert result.is_anomaly is True
        assert len(result.algorithms_voted) == 3
        # 至少 2/3 算法投票同意
        positive_votes = sum(1 for v in result.algorithms_voted if v.voted_anomaly)
        assert positive_votes >= agent.VOTE_THRESHOLD
        assert result.fingerprint != ""

    def test_fingerprint_generation(self, agent: MonitorAgent) -> None:
        """测试告警指纹生成"""
        metric = MetricInput(
            metric_name="cpu_usage_percent",
            metric_value=95.0,
            service_name="order-service",
            labels={"cluster": "prod", "namespace": "default"},
        )
        agent._update_history(metric)
        agent._update_adaptive_thresholds(metric)
        result = agent.detect_anomaly(metric)

        assert len(result.fingerprint) == 16  # MD5 前 16 位
        # 相同输入应生成相同指纹
        result2 = agent.detect_anomaly(metric)
        assert result.fingerprint == result2.fingerprint

    def test_deduplication(self, agent: MonitorAgent) -> None:
        """测试告警去重"""
        fingerprint = "test_fp_12345678"

        # 第一次不应是重复
        assert agent._check_duplicate(fingerprint) is False

        # 记录指纹
        agent._record_fingerprint(fingerprint)

        # 5 分钟内应是重复
        assert agent._check_duplicate(fingerprint) is True

    def test_adaptive_thresholds(self, agent: MonitorAgent) -> None:
        """测试自适应阈值更新"""
        metric = MetricInput(
            metric_name="cpu_usage_percent",
            metric_value=50.0,
            service_name="order-service",
            history_values=[float(x) for x in range(30, 70)],
        )
        agent._update_history(metric)
        agent._update_adaptive_thresholds(metric)

        key = f"{metric.service_name}:{metric.metric_name}"
        thresholds = agent._adaptive_thresholds.get(key, {})

        assert "mean" in thresholds
        assert "std" in thresholds
        assert "ewma_value" in thresholds
        assert "ewma_std" in thresholds

    def test_severity_determination(self, agent: MonitorAgent) -> None:
        """测试严重级别判定"""
        from app.agents.monitor_agent import AnomalyDetectionResult

        metric = MetricInput(
            metric_name="cpu_usage_percent",
            metric_value=95.0,
            service_name="order-service",
            labels={"tier": "critical"},
        )

        # 高风险场景
        high_result = AnomalyDetectionResult(
            is_anomaly=True,
            score=5.0,
            confidence=0.9,
        )
        severity = agent._determine_severity(metric, high_result)
        assert severity in (SeverityLevel.CRITICAL, SeverityLevel.HIGH)

        # 低风险场景
        low_result = AnomalyDetectionResult(
            is_anomaly=True,
            score=0.5,
            confidence=0.3,
        )
        severity = agent._determine_severity(metric, low_result)
        assert severity in (SeverityLevel.LOW, SeverityLevel.INFO, SeverityLevel.MEDIUM)

    @pytest.mark.asyncio
    async def test_process_full_flow(
        self,
        agent: MonitorAgent,
        anomaly_metric: MetricInput,
        agent_context: AgentExecutionContext,
    ) -> None:
        """测试完整处理流程"""
        result = await agent.process(anomaly_metric, agent_context)

        assert result.success is True
        assert result.agent_name == "monitor_agent"
        assert "detection_result" in result.output_data
        assert "alert_generated" in result.output_data

    def test_skewness_kurtosis(self, agent: MonitorAgent) -> None:
        """测试偏度和峰度计算"""
        import numpy as np

        # 正态分布数据
        normal_data = np.random.normal(0, 1, 1000)
        skew = agent._calculate_skewness(normal_data)
        kurt = agent._calculate_kurtosis(normal_data)

        # 正态分布偏度应接近 0，峰度接近 3
        assert abs(skew) < 1.0
        assert 1.5 < kurt < 6.0

    def test_3sigma_detection(self, agent: MonitorAgent) -> None:
        """测试 3-Sigma 检测"""
        history = [45.0, 47.0, 46.0, 48.0, 44.0, 49.0, 45.0, 47.0, 46.0, 48.0,
                   45.0, 47.0, 46.0, 48.0, 44.0, 49.0, 45.0, 47.0, 46.0, 48.0]
        vote = agent._detect_3sigma(95.0, history)

        assert isinstance(vote, AlgorithmVote)
        assert vote.algorithm == "3-sigma"
        assert vote.voted_anomaly is True
        assert vote.score > 1.0

    def test_ewma_detection(self, agent: MonitorAgent) -> None:
        """测试 EWMA 检测"""
        key = "test:cpu"
        agent._adaptive_thresholds[key] = {
            "ewma_value": 50.0,
            "ewma_std": 5.0,
        }

        vote = agent._detect_ewma(80.0, key)
        assert isinstance(vote, AlgorithmVote)
        assert vote.algorithm == "ewma"
        assert vote.voted_anomaly is True

    def test_isolation_forest_detection(self, agent: MonitorAgent) -> None:
        """测试 Isolation Forest 检测"""
        history = [45.0, 46.0, 44.0, 47.0, 45.0, 46.0, 44.0, 45.0, 46.0, 45.0]
        vote = agent._detect_isolation_forest(95.0, history)

        assert isinstance(vote, AlgorithmVote)
        assert vote.algorithm == "isolation_forest"
        assert vote.voted_anomaly is True


# ============================================================
# RCAAgent Tests
# ============================================================

class TestRCAAgent:
    """RCAAgent 测试套件"""

    @pytest.fixture
    def agent(self) -> RCAAgent:
        return RCAAgent()

    def test_agent_metadata(self, agent: RCAAgent) -> None:
        """测试 Agent 元数据"""
        assert agent.get_name() == "rca_agent"
        assert "根因分析" in agent.get_description()

    def test_bfs_traverse(self, agent: RCAAgent) -> None:
        """测试 BFS 遍历"""
        results = agent.bfs_traverse("order-service", max_hops=2)

        assert len(results) > 0
        assert results[0].service == "order-service"
        assert results[0].hop_distance == 0

        # 检查是否发现了依赖服务
        services = [r.service for r in results]
        assert "payment-service" in services or "inventory-service" in services or "user-service" in services

    def test_bfs_traverse_unknown_service(self, agent: RCAAgent) -> None:
        """测试 BFS 遍历未知服务"""
        results = agent.bfs_traverse("unknown-service", max_hops=2)
        assert len(results) == 1
        assert results[0].service == "unknown-service"

    def test_bayesian_inference(self, agent: RCAAgent) -> None:
        """测试贝叶斯推理"""
        symptoms = ["high_cpu", "increased_latency"]
        results = agent.bayesian_inference(symptoms)

        assert len(results) > 0
        assert isinstance(results[0], BayesianNode)

        # 按后验概率排序
        for i in range(len(results) - 1):
            assert results[i].posterior >= results[i + 1].posterior

        # 后验概率应在 [0, 1] 范围内
        for r in results:
            assert 0.0 <= r.posterior <= 1.0
            assert r.prior > 0
            assert r.likelihood > 0

    def test_bayesian_empty_symptoms(self, agent: RCAAgent) -> None:
        """测试空症状的贝叶斯推理"""
        results = agent.bayesian_inference([])
        assert len(results) > 0
        # 空症状时，后验应接近先验
        for r in results:
            assert abs(r.posterior - r.prior) < 0.5

    def test_symptom_extraction(self, agent: RCAAgent) -> None:
        """测试症状提取"""
        alert = AlertEvent(
            service="order-service",
            metric="cpu_usage_percent",
            value=95.0,
            threshold=80.0,
        )
        symptoms = agent._extract_symptoms(alert)
        assert "high_cpu" in symptoms

    def test_rag_retrieve(self, agent: RCAAgent) -> None:
        """测试 RAG 检索"""
        alert = AlertEvent(
            service="order-service",
            metric="cpu_usage_percent",
            value=95.0,
            threshold=80.0,
        )
        symptoms = ["high_cpu", "increased_latency"]
        results = agent._rag_retrieve(alert, symptoms)

        assert isinstance(results, list)
        if results:
            assert "id" in results[0]
            assert "category" in results[0]

    def test_rag_match_score(self, agent: RCAAgent) -> None:
        """测试知识库匹配得分计算"""
        kb_entry = {
            "id": "kb_001",
            "category": "deployment_issue",
            "symptoms": ["high_cpu", "increased_latency"],
            "confidence_boost": 0.2,
        }
        alert = AlertEvent(
            service="order-service",
            metric="cpu_usage_percent",
            value=95.0,
            threshold=80.0,
        )
        symptoms = ["high_cpu", "increased_latency"]
        score = agent._calculate_kb_match_score(kb_entry, alert, symptoms)

        assert score > 0.3  # 应该有较好的匹配

    def test_synthesize_analysis(self, agent: RCAAgent) -> None:
        """测试综合分析"""
        bayesian_results = [
            BayesianNode(name="traffic_spike", prior=0.18, likelihood=0.8, posterior=0.85, evidence_strength=0.9),
            BayesianNode(name="recent_deployment", prior=0.15, likelihood=0.6, posterior=0.5, evidence_strength=0.7),
        ]
        rag_results = [
            {"id": "kb_001", "category": "deployment_issue", "match_score": 0.8, "root_causes": ["recent_deployment"], "confidence_boost": 0.2},
        ]
        impact_chain = [
            ServiceImpact(service="order-service", hop_distance=0, tier="critical"),
        ]
        alert = AlertEvent(service="order-service", metric="cpu_usage_percent", value=95.0, threshold=80.0)

        root_cause, confidence, evidence = agent._synthesize_analysis(
            bayesian_results, rag_results, impact_chain, alert
        )

        assert root_cause != ""
        assert 0.0 <= confidence <= 1.0
        assert "bayesian_top" in evidence

    def test_generate_suggested_actions(self, agent: RCAAgent) -> None:
        """测试建议操作生成"""
        actions = agent._generate_suggested_actions(
            "resource_exhaustion",
            [ServiceImpact(service="order-service", hop_distance=0, tier="critical")],
            [{"solutions": ["scale_up_resources"]}],
        )
        assert len(actions) > 0
        assert "scale_up_resources" in actions

    @pytest.mark.asyncio
    async def test_process_full_flow(
        self,
        agent: RCAAgent,
        sample_alert: AlertEvent,
        agent_context: AgentExecutionContext,
    ) -> None:
        """测试完整处理流程"""
        rca_input = RCAInput(
            alert=sample_alert,
            incident_id="test-incident-001",
        )
        result = await agent.process(rca_input, agent_context)

        assert result.success is True
        assert result.agent_name == "rca_agent"
        assert "rca_event" in result.output_data
        assert "root_cause" in result.output_data
        assert "confidence" in result.output_data


# ============================================================
# HealAgent Tests
# ============================================================

class TestHealAgent:
    """HealAgent 测试套件"""

    @pytest.fixture
    def agent(self) -> HealAgent:
        return HealAgent()

    def test_agent_metadata(self, agent: HealAgent) -> None:
        """测试 Agent 元数据"""
        assert agent.get_name() == "heal_agent"
        assert "故障自愈" in agent.get_description()

    def test_circuit_breaker_closed(self) -> None:
        """测试熔断器关闭状态"""
        cb = CircuitBreaker()
        assert cb.state.value == "closed"
        assert cb.can_execute() is True

    def test_circuit_breaker_open(self) -> None:
        """测试熔断器打开状态"""
        cb = CircuitBreaker()
        # 连续失败 5 次
        for _ in range(5):
            cb.record_failure()

        assert cb.state.value == "open"
        assert cb.can_execute() is False

    def test_circuit_breaker_half_open(self) -> None:
        """测试熔断器半开状态"""
        cb = CircuitBreaker()
        # 连续失败 5 次
        for _ in range(5):
            cb.record_failure()

        assert cb.state.value == "open"

        # 模拟时间流逝（简化：直接设置半开状态）
        cb._transition_to(CircuitBreakerState.HALF_OPEN)
        assert cb.state.value == "half_open"
        assert cb.can_execute() is True

        # 半开状态下连续成功恢复关闭
        for _ in range(3):
            cb.record_success()
        assert cb.state.value == "closed"

    def test_match_playbook(self, agent: HealAgent, sample_rca_event: RCAEvent) -> None:
        """测试 Playbook 匹配"""
        playbook = agent._match_playbook(sample_rca_event)
        assert playbook is not None
        assert "id" in playbook
        assert "actions" in playbook

    def test_evaluate_blast_radius(self, agent: HealAgent, sample_rca_event: RCAEvent) -> None:
        """测试爆炸半径评估"""
        result = agent._evaluate_blast_radius(sample_rca_event)

        assert isinstance(result, BlastRadiusResult)
        assert result.total_service_count == 8  # SERVICE_TOPOLOGY 中的服务数
        assert 0.0 <= result.blast_radius_ratio <= 1.0
        assert result.risk_level in ["low", "medium", "high", "critical"]

    def test_determine_heal_level(self, agent: HealAgent) -> None:
        """测试自愈级别判定"""
        # 低风险 -> L0
        low_radius = BlastRadiusResult(
            affected_service_count=1,
            total_service_count=20,
            blast_radius_ratio=0.02,
            risk_level="low",
        )
        level = agent._determine_heal_level(low_radius)
        assert level == HealLevel.L0_AUTO

        # 中风险 -> L1
        medium_radius = BlastRadiusResult(
            affected_service_count=2,
            total_service_count=20,
            blast_radius_ratio=0.10,
            risk_level="medium",
        )
        level = agent._determine_heal_level(medium_radius)
        assert level == HealLevel.L1_CONFIRM

        # 高风险 -> L2
        high_radius = BlastRadiusResult(
            affected_service_count=10,
            total_service_count=20,
            blast_radius_ratio=0.50,
            risk_level="high",
        )
        level = agent._determine_heal_level(high_radius)
        assert level == HealLevel.L2_APPROVE

    def test_dry_run_valid_action(self, agent: HealAgent, sample_rca_event: RCAEvent) -> None:
        """测试有效操作的 dry-run"""
        action = {"type": "scale_up", "target": "deployment", "replicas": "+2"}
        blast_radius = BlastRadiusResult(
            affected_service_count=1,
            total_service_count=20,
            blast_radius_ratio=0.05,
            risk_level="low",
        )
        result = agent._dry_run_action(action, sample_rca_event, blast_radius)

        assert result.executable is True
        assert result.syntax_valid is True
        assert result.permission_check is True
        assert len(result.command_preview) > 0

    def test_dry_run_invalid_action(self, agent: HealAgent, sample_rca_event: RCAEvent) -> None:
        """测试无效操作的 dry-run"""
        action = {"type": "invalid_action_type"}
        blast_radius = BlastRadiusResult(
            affected_service_count=1,
            total_service_count=20,
            blast_radius_ratio=0.05,
            risk_level="low",
        )
        result = agent._dry_run_action(action, sample_rca_event, blast_radius)

        assert result.executable is False
        assert result.syntax_valid is False

    def test_build_rollback_plan(self, agent: HealAgent) -> None:
        """测试回滚计划生成"""
        playbook = {
            "id": "pb_high_cpu",
            "rollback": {"type": "scale_down", "replicas": "-2"},
        }
        actions = [{"type": "scale_up"}]
        plan = agent._build_rollback_plan(playbook, actions)

        assert len(plan) > 0
        assert plan[0]["type"] == "scale_down"

    def test_build_rollback_plan_auto(self, agent: HealAgent) -> None:
        """测试自动生成回滚计划"""
        actions = [{"type": "scale_up"}]
        plan = agent._build_rollback_plan(None, actions)

        assert len(plan) > 0

    @pytest.mark.asyncio
    async def test_process_full_flow(
        self,
        agent: HealAgent,
        sample_rca_event: RCAEvent,
        agent_context: AgentExecutionContext,
    ) -> None:
        """测试完整处理流程"""
        heal_input = HealInput(
            rca_event=sample_rca_event,
            incident_id="test-incident-001",
        )
        result = await agent.process(heal_input, agent_context)

        assert result.success is True
        assert result.agent_name == "heal_agent"
        assert "heal_event" in result.output_data
        assert "heal_level" in result.output_data
        assert "blast_radius" in result.output_data


# ============================================================
# ChangeAgent Tests
# ============================================================

class TestChangeAgent:
    """ChangeAgent 测试套件"""

    @pytest.fixture
    def agent(self) -> ChangeAgent:
        return ChangeAgent()

    def test_agent_metadata(self, agent: ChangeAgent) -> None:
        """测试 Agent 元数据"""
        assert agent.get_name() == "change_agent"
        assert "变更审批" in agent.get_description()

    def test_risk_weights_sum(self, agent: ChangeAgent) -> None:
        """测试风险权重之和为 1"""
        total = sum(agent.RISK_WEIGHTS.values())
        assert abs(total - 1.0) < 0.001

    def test_calculate_time_risk(self, agent: ChangeAgent) -> None:
        """测试时间风险计算"""
        risk = agent._calculate_time_risk()
        assert 0.0 <= risk <= 1.0

    def test_get_service_tier(self, agent: ChangeAgent) -> None:
        """测试服务等级获取"""
        assert agent._get_service_tier("order-service") == "critical"
        assert agent._get_service_tier("inventory-service") == "standard"
        assert agent._get_service_tier("unknown-service") == "standard"

    def test_calculate_change_type_risk(self, agent: ChangeAgent) -> None:
        """测试变更类型风险"""
        assert agent._calculate_change_type_risk("database") == 0.9
        assert agent._calculate_change_type_risk("restart") == 0.2
        assert agent._calculate_change_type_risk("unknown") == 0.5

    def test_determine_risk_level(self, agent: ChangeAgent) -> None:
        """测试风险等级判定"""
        assert agent._determine_risk_level(0.1) == "low"
        assert agent._determine_risk_level(0.4) == "medium"
        assert agent._determine_risk_level(0.7) == "high"
        assert agent._determine_risk_level(0.9) == "critical"

    def test_make_decision_low_risk(self, agent: ChangeAgent) -> None:
        """测试低风险决策"""
        decision = agent._make_decision(
            None,  # type: ignore
            risk_score=0.1,
            risk_level="low",
            context={},
        )
        assert decision["status"] == ApprovalStatus.AUTO_APPROVED.value
        assert decision["auto_decision"] is True

    def test_make_decision_high_risk(self, agent: ChangeAgent) -> None:
        """测试高风险决策"""
        decision = agent._make_decision(
            None,  # type: ignore
            risk_score=0.85,
            risk_level="critical",
            context={},
        )
        assert decision["status"] == ApprovalStatus.PENDING.value
        assert "sre_manager" in decision.get("approvers", [])
        assert decision["auto_decision"] is False

    def test_audit_log(self, agent: ChangeAgent) -> None:
        """测试审计日志"""
        initial_count = len(agent._audit_log)

        agent._record_audit_log(
            ChangeInput(
                heal_event=HealEvent(),
                change_id="test-001",
                incident_id="test-incident",
            ),
            {"status": ApprovalStatus.AUTO_APPROVED.value},
            risk_score=0.2,
            risk_factors=[],
        )

        assert len(agent._audit_log) == initial_count + 1

    def test_audit_log_query(self, agent: ChangeAgent) -> None:
        """测试审计日志查询"""
        agent._record_audit_log(
            ChangeInput(
                heal_event=HealEvent(),
                change_id="test-query-001",
                incident_id="test-query-incident",
            ),
            {"status": ApprovalStatus.PENDING.value},
            risk_score=0.5,
            risk_factors=[],
        )

        results = agent.get_audit_log(incident_id="test-query-incident")
        assert len(results) >= 1

    def test_change_outcome_record(self, agent: ChangeAgent) -> None:
        """测试变更结果记录"""
        agent.record_change_outcome("test-change-001", "order-service", True)
        history = agent._change_success_history.get("order-service", {})
        assert history.get("total", 0) >= 1

    @pytest.mark.asyncio
    async def test_process_full_flow(
        self,
        agent: ChangeAgent,
        sample_heal_event: HealEvent,
        agent_context: AgentExecutionContext,
    ) -> None:
        """测试完整处理流程"""
        change_input = ChangeInput(
            heal_event=sample_heal_event,
            incident_id="test-incident-001",
            change_id="test-change-001",
        )
        result = await agent.process(change_input, agent_context)

        assert result.success is True
        assert result.agent_name == "change_agent"
        assert "risk_score" in result.output_data
        assert "risk_level" in result.output_data
        assert "approval_status" in result.output_data


# ============================================================
# MemoryAgent Tests
# ============================================================

class TestMemoryAgent:
    """MemoryAgent 测试套件"""

    @pytest.fixture
    def agent(self) -> MemoryAgent:
        return MemoryAgent()

    def test_agent_metadata(self, agent: MemoryAgent) -> None:
        """测试 Agent 元数据"""
        assert agent.get_name() == "memory_agent"
        assert "记忆管理" in agent.get_description()

    @pytest.mark.asyncio
    async def test_store_memory(
        self,
        agent: MemoryAgent,
        agent_context: AgentExecutionContext,
    ) -> None:
        """测试存储记忆"""
        from app.models.memory import MemoryType

        store_input = MemoryInput(
            operation="store",
            content="CPU usage anomaly detected on order-service at 95%",
            memory_type=MemoryType.OBSERVATION,
            incident_id="test-incident-001",
            agent_name="monitor_agent",
            tags=["cpu", "anomaly", "order-service"],
        )
        result = await agent.process(store_input, agent_context)

        assert result.success is True
        assert result.output_data["operation"] == "store"
        assert "memory_id" in result.output_data
        assert "memory_type" in result.output_data

    @pytest.mark.asyncio
    async def test_retrieve_memory(
        self,
        agent: MemoryAgent,
        agent_context: AgentExecutionContext,
    ) -> None:
        """测试检索记忆（先存储后检索）"""
        from app.models.memory import MemoryType

        # 先存储
        store_input = MemoryInput(
            operation="store",
            content="Database connection timeout on mysql-primary",
            memory_type=MemoryType.OBSERVATION,
            incident_id="test-incident-002",
            agent_name="rca_agent",
            tags=["database", "timeout"],
        )
        await agent.process(store_input, agent_context)

        # 再检索
        retrieve_input = MemoryInput(
            operation="retrieve",
            query="database timeout mysql",
            incident_id="test-incident-002",
            top_k=5,
        )
        result = await agent.process(retrieve_input, agent_context)

        assert result.success is True
        assert "total_found" in result.output_data
        assert "results" in result.output_data

    @pytest.mark.asyncio
    async def test_search_memory(
        self,
        agent: MemoryAgent,
        agent_context: AgentExecutionContext,
    ) -> None:
        """测试搜索记忆"""
        search_input = MemoryInput(
            operation="search",
            query="kubernetes pod restart",
            top_k=5,
        )
        result = await agent.process(search_input, agent_context)

        assert result.success is True
        assert "total_found" in result.output_data
        assert "results" in result.output_data

    @pytest.mark.asyncio
    async def test_unknown_operation(
        self,
        agent: MemoryAgent,
        agent_context: AgentExecutionContext,
    ) -> None:
        """测试未知操作"""
        bad_input = MemoryInput(operation="nonexistent_operation")
        result = await agent.process(bad_input, agent_context)

        assert result.success is False
        assert "Unknown operation" in result.error_message

    @pytest.mark.asyncio
    async def test_consolidate_memory(
        self,
        agent: MemoryAgent,
        agent_context: AgentExecutionContext,
    ) -> None:
        """测试记忆整理"""
        from app.models.memory import MemoryType

        # 存储多条记忆
        for i in range(3):
            store_input = MemoryInput(
                operation="store",
                content=f"CPU high usage detected on order-service iteration {i}",
                memory_type=MemoryType.OBSERVATION,
                incident_id="test-incident-003",
                tags=["cpu", "order-service"],
            )
            await agent.process(store_input, agent_context)

        # 整理记忆
        consolidate_input = MemoryInput(operation="consolidate")
        result = await agent.process(consolidate_input, agent_context)

        assert result.success is True

    @pytest.mark.asyncio
    async def test_archive_memory(
        self,
        agent: MemoryAgent,
        agent_context: AgentExecutionContext,
    ) -> None:
        """测试归档记忆"""
        archive_input = MemoryInput(operation="archive")
        result = await agent.process(archive_input, agent_context)

        assert result.success is True


# ============================================================
# EvalAgent Tests
# ============================================================

class TestEvalAgent:
    """EvalAgent 测试套件"""

    @pytest.fixture
    def agent(self) -> EvalAgent:
        return EvalAgent()

    def test_agent_metadata(self, agent: EvalAgent) -> None:
        """测试 Agent 元数据"""
        assert agent.get_name() == "eval_agent"
        assert "评估" in agent.get_description()

    def test_calibration_error(self, agent: EvalAgent) -> None:
        """测试校准误差计算"""
        from app.evaluation.metrics import confidence_calibration

        # 每个置信度分组的真实正确率都与置信度一致。
        confidences = [level for level in (0.1, 0.3, 0.5, 0.7, 0.9) for _ in range(10)]
        correct = [
            outcome
            for correct_count in (1, 3, 5, 7, 9)
            for outcome in ([True] * correct_count + [False] * (10 - correct_count))
        ]
        ece = confidence_calibration(confidences, correct)
        assert ece < 0.01  # 完美校准时 ECE 应接近 0

        # 只有 10% 的预测正确，却统一给出 90% 置信度。
        overconfident = [0.9] * 10
        mostly_incorrect = [True] + [False] * 9
        ece = confidence_calibration(overconfident, mostly_incorrect)
        assert ece > 0.5  # 过度自信时 ECE 应较大

    def test_default_test_cases(self, agent: EvalAgent) -> None:
        """测试默认测试用例"""
        cases = agent._get_default_test_cases()
        assert len(cases) > 0
        for case in cases:
            assert "name" in case
            assert "expected_result" in case
            assert "actual_result" in case

    def test_eval_end_to_end(self, agent: EvalAgent) -> None:
        """测试端到端评估"""
        from app.agents.eval_agent import EndToEndMetrics

        eval_input = EvalInput(
            eval_type=EvalType.END_TO_END,
        )
        metrics = agent._eval_end_to_end(eval_input)
        assert isinstance(metrics, EndToEndMetrics)
        assert metrics.total_test_cases > 0
        assert 0.0 <= metrics.task_success_rate <= 1.0

    def test_eval_reasoning_no_data_raises(self, agent: EvalAgent) -> None:
        """测试推理评估无数据时抛 ValueError（Phase 4 删除了 mock data fallback）"""
        import pytest
        from app.agents.eval_agent import EvalInput
        with pytest.raises(ValueError, match="Cannot evaluate reasoning"):
            agent._eval_reasoning(EvalInput(eval_type="reasoning", ground_truth={}, agent_results=[]))

    def test_eval_tool_call_empty(self, agent: EvalAgent) -> None:
        """测试空工具调用评估"""
        eval_input = EvalInput(eval_type=EvalType.TOOL_CALL)
        metrics = agent._eval_tool_call(eval_input)
        assert metrics.tool_selection_accuracy == 0.0

    def test_calculate_overall_score(self, agent: EvalAgent) -> None:
        """测试综合得分计算"""
        from app.agents.eval_agent import EndToEndMetrics, ReasoningMetrics, ToolCallMetrics, RAGMetrics, EvalReport

        report = EvalReport(
            end_to_end=EndToEndMetrics(
                task_success_rate=0.8,
                detection_accuracy=0.9,
                false_positive_rate=0.1,
            ),
            reasoning=ReasoningMetrics(
                root_cause_accuracy=0.75,
                confidence_calibration_error=0.1,
                suggested_action_accuracy=0.8,
            ),
            tool_call=ToolCallMetrics(
                tool_selection_accuracy=0.85,
                parameter_accuracy=0.8,
                execution_success_rate=0.9,
            ),
            rag=RAGMetrics(
                retrieval_precision=0.7,
                retrieval_recall=0.65,
                context_relevance=0.75,
            ),
        )
        score = agent._calculate_overall_score(report)
        assert 0.0 <= score <= 1.0

    def test_generate_recommendations(self, agent: EvalAgent) -> None:
        """测试改进建议生成"""
        from app.agents.eval_agent import EndToEndMetrics, ReasoningMetrics, ToolCallMetrics, RAGMetrics, EvalReport

        # 低分场景
        report = EvalReport(
            end_to_end=EndToEndMetrics(task_success_rate=0.5, false_positive_rate=0.3),
            reasoning=ReasoningMetrics(root_cause_accuracy=0.5, confidence_calibration_error=0.4),
            tool_call=ToolCallMetrics(tool_selection_accuracy=0.5),
            rag=RAGMetrics(retrieval_precision=0.4),
        )
        recommendations = agent._generate_recommendations(report)
        assert len(recommendations) > 0

        # 高分场景
        good_report = EvalReport(
            end_to_end=EndToEndMetrics(task_success_rate=0.95, false_positive_rate=0.02),
            reasoning=ReasoningMetrics(root_cause_accuracy=0.95, confidence_calibration_error=0.02),
            tool_call=ToolCallMetrics(tool_selection_accuracy=0.95),
            rag=RAGMetrics(retrieval_precision=0.95),
        )
        good_recommendations = agent._generate_recommendations(good_report)
        assert len(good_recommendations) > 0

    @pytest.mark.asyncio
    async def test_process_full_flow(
        self,
        agent: EvalAgent,
        agent_context: AgentExecutionContext,
    ) -> None:
        """测试完整评估流程（Phase 4 起要求真实数据，无数据时不再 mock 返回）"""
        # 无数据时应该失败（不再是 mock fallback）
        empty_input = EvalInput(
            eval_type=EvalType.FULL,
            ground_truth={},
            agent_results=[],
        )
        result = await agent.process(empty_input, agent_context)
        assert result.success is False
        assert "Cannot evaluate reasoning" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_process_end_to_end(
        self,
        agent: EvalAgent,
        agent_context: AgentExecutionContext,
    ) -> None:
        """测试端到端评估流程"""
        eval_input = EvalInput(
            eval_type=EvalType.END_TO_END,
        )
        result = await agent.process(eval_input, agent_context)

        assert result.success is True
        assert "end_to_end" in result.output_data.get("report", {})

    def test_eval_history(self, agent: EvalAgent) -> None:
        """测试评估历史"""
        history = agent.get_eval_history(limit=5)
        assert isinstance(history, list)


# ============================================================
# Run Tests
# ============================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
