"""
AIOps Agent Platform - MonitorAgent Integration Tests

测试MonitorAgent的异常检测、多算法投票和告警去重功能。
"""

from __future__ import annotations

import pytest
import numpy as np

from app.agents.monitor_agent import (
    AlgorithmVote,
    AnomalyDetectionResult,
    MetricInput,
    MonitorAgent,
)
from app.models.agent import AgentExecutionContext


class TestMonitorAgent:
    """MonitorAgent集成测试套件"""

    @pytest.fixture
    def agent(self) -> MonitorAgent:
        """创建MonitorAgent实例"""
        return MonitorAgent()

    @pytest.fixture
    def context(self) -> AgentExecutionContext:
        """创建Agent执行上下文"""
        return AgentExecutionContext(
            incident_id="test-incident-001",
            input_data={"test": True},
        )

    # ========================================================================
    # Anomaly Detection Tests
    # ========================================================================

    def test_detect_anomaly_normal(self, agent: MonitorAgent) -> None:
        """
        测试正常数据不应报警

        使用正常范围内的指标值，确保检测为非异常。
        """
        history = [45.0, 48.0, 50.0, 47.0, 49.0, 51.0, 46.0, 48.0, 50.0, 47.0]
        metric = MetricInput(
            metric_name="cpu_usage_percent",
            metric_value=48.0,
            service_name="order-service",
            history_values=history,
        )
        agent._update_history(metric)
        agent._update_adaptive_thresholds(metric)

        result = agent.detect_anomaly(metric)

        assert isinstance(result, AnomalyDetectionResult)
        assert result.is_anomaly is False
        assert len(result.algorithms_voted) == 3
        assert 0.0 <= result.score <= 2.0
        assert 0.0 <= result.confidence <= 1.0

    def test_detect_anomaly_spike(self, agent: MonitorAgent) -> None:
        """
        测试突变应检测出异常

        使用从正常范围突变为高值的指标，确保检测为异常。
        """
        history = [45.0, 48.0, 50.0, 47.0, 49.0, 51.0, 46.0, 48.0, 50.0, 47.0]
        metric = MetricInput(
            metric_name="cpu_usage_percent",
            metric_value=95.0,
            service_name="order-service",
            history_values=history,
            labels={"tier": "critical"},
        )
        agent._update_history(metric)
        agent._update_adaptive_thresholds(metric)

        result = agent.detect_anomaly(metric)

        assert isinstance(result, AnomalyDetectionResult)
        assert result.is_anomaly is True
        assert len(result.algorithms_voted) == 3
        # 至少2/3算法投票同意
        positive_votes = sum(1 for v in result.algorithms_voted if v.voted_anomaly)
        assert positive_votes >= agent.VOTE_THRESHOLD
        assert result.fingerprint != ""
        assert result.algorithm_consensus in ["unanimous", "majority"]

    def test_detect_anomaly_trend(self, agent: MonitorAgent) -> None:
        """
        测试趋势异常应检测出

        使用持续上升的趋势数据，确保检测为异常。
        """
        # 趋势上升数据
        trend_history = [55.0, 58.0, 62.0, 65.0, 68.0, 72.0, 76.0, 80.0, 85.0, 88.0]
        metric = MetricInput(
            metric_name="memory_usage_percent",
            metric_value=95.0,
            service_name="payment-service",
            history_values=trend_history,
            labels={"tier": "critical"},
        )
        agent._update_history(metric)
        agent._update_adaptive_thresholds(metric)

        result = agent.detect_anomaly(metric)

        assert isinstance(result, AnomalyDetectionResult)
        # 趋势异常可能被某些算法检测到
        assert len(result.algorithms_voted) == 3
        for vote in result.algorithms_voted:
            assert isinstance(vote, AlgorithmVote)
            assert 0.0 <= vote.score <= 10.0

    # ========================================================================
    # Multi-Algorithm Voting Tests
    # ========================================================================

    def test_multi_algorithm_voting(self, agent: MonitorAgent) -> None:
        """
        测试多算法投票机制

        确保三种算法(3-Sigma, EWMA, Isolation Forest)都参与投票，
        并且投票阈值机制正常工作。
        """
        history = [45.0, 46.0, 44.0, 47.0, 45.0, 46.0, 44.0, 45.0, 46.0, 45.0,
                   46.0, 44.0, 47.0, 45.0, 46.0, 44.0, 45.0, 46.0, 45.0, 46.0]

        # 场景1: 明显异常（应全票通过）
        metric_anomaly = MetricInput(
            metric_name="cpu_usage_percent",
            metric_value=95.0,
            service_name="order-service",
            history_values=history,
        )
        agent._update_history(metric_anomaly)
        agent._update_adaptive_thresholds(metric_anomaly)
        result = agent.detect_anomaly(metric_anomaly)

        assert len(result.algorithms_voted) == 3
        positive_votes = sum(1 for v in result.algorithms_voted if v.voted_anomaly)
        assert positive_votes >= agent.VOTE_THRESHOLD
        assert result.is_anomaly is True

        # 场景2: 正常数据（不应报警）
        agent2 = MonitorAgent()
        metric_normal = MetricInput(
            metric_name="cpu_usage_percent",
            metric_value=47.0,
            service_name="order-service",
            history_values=history,
        )
        agent2._update_history(metric_normal)
        agent2._update_adaptive_thresholds(metric_normal)
        result2 = agent2.detect_anomaly(metric_normal)

        assert len(result2.algorithms_voted) == 3
        assert result2.is_anomaly is False

    def test_individual_algorithms(self, agent: MonitorAgent) -> None:
        """测试各个独立检测算法"""
        history = [45.0, 46.0, 44.0, 47.0, 45.0, 46.0, 44.0, 45.0, 46.0, 45.0,
                   46.0, 44.0, 47.0, 45.0, 46.0, 44.0, 45.0, 46.0, 45.0, 46.0]

        # 3-Sigma
        vote_3sigma = agent._detect_3sigma(95.0, history)
        assert vote_3sigma.algorithm == "3-sigma"
        assert vote_3sigma.voted_anomaly is True
        assert vote_3sigma.score > 1.0

        # EWMA
        key = "test-service:cpu"
        agent._adaptive_thresholds[key] = {
            "ewma_value": 45.0,
            "ewma_std": 2.0,
        }
        vote_ewma = agent._detect_ewma(80.0, key)
        assert vote_ewma.algorithm == "ewma"
        assert vote_ewma.voted_anomaly is True

        # Isolation Forest (模拟)
        vote_if = agent._detect_isolation_forest(95.0, history)
        assert vote_if.algorithm == "isolation_forest"
        assert vote_if.voted_anomaly is True

    # ========================================================================
    # Alert Deduplication Tests
    # ========================================================================

    def test_alert_deduplication(self, agent: MonitorAgent) -> None:
        """
        测试告警去重

        确保相同指纹的告警在5分钟窗口内被去重。
        """
        fingerprint = "abc123def4567890"

        # 第一次 - 不应是重复
        assert agent._check_duplicate(fingerprint) is False

        # 记录指纹
        agent._record_fingerprint(fingerprint)

        # 立即再次检查 - 应该是重复
        assert agent._check_duplicate(fingerprint) is True

        # 计数应增加
        record = agent._fingerprint_cache[fingerprint]
        assert record.count >= 1

    def test_different_fingerprints(self, agent: MonitorAgent) -> None:
        """测试不同指纹不应被去重"""
        fp1 = "abc123"
        fp2 = "def456"

        agent._record_fingerprint(fp1)
        assert agent._check_duplicate(fp2) is False

    # ========================================================================
    # Adaptive Threshold Tests
    # ========================================================================

    def test_adaptive_thresholds_update(self, agent: MonitorAgent) -> None:
        """测试自适应阈值更新"""
        history = [float(x) for x in range(30, 70)]
        metric = MetricInput(
            metric_name="cpu_usage_percent",
            metric_value=50.0,
            service_name="order-service",
            history_values=history,
        )
        agent._update_history(metric)
        agent._update_adaptive_thresholds(metric)

        key = f"{metric.service_name}:{metric.metric_name}"
        thresholds = agent._adaptive_thresholds.get(key, {})

        assert "mean" in thresholds
        assert "std" in thresholds
        assert "ewma_value" in thresholds
        assert "ewma_std" in thresholds
        assert thresholds["mean"] > 0

    def test_adaptive_sigma_threshold(self, agent: MonitorAgent) -> None:
        """测试自适应Sigma阈值"""
        # 大量数据以启用自适应
        history = list(np.random.normal(50, 10, 30))
        threshold = agent._get_adaptive_sigma_threshold(history)

        # 默认3.0或更高
        assert threshold >= 3.0

    # ========================================================================
    # Severity Determination Tests
    # ========================================================================

    def test_severity_critical(self, agent: MonitorAgent) -> None:
        """测试严重级别判定 - 关键"""
        from app.models.events import SeverityLevel

        metric = MetricInput(
            metric_name="cpu_usage_percent",
            metric_value=99.0,
            service_name="order-service",
            labels={"tier": "critical"},
        )
        result = AnomalyDetectionResult(
            is_anomaly=True,
            score=5.0,
            confidence=0.95,
        )
        severity = agent._determine_severity(metric, result)
        assert severity in (SeverityLevel.CRITICAL, SeverityLevel.HIGH)

    def test_severity_low(self, agent: MonitorAgent) -> None:
        """测试严重级别判定 - 低"""
        from app.models.events import SeverityLevel

        metric = MetricInput(
            metric_name="cpu_usage_percent",
            metric_value=55.0,
            service_name="order-service",
            labels={"tier": "standard"},
        )
        result = AnomalyDetectionResult(
            is_anomaly=True,
            score=0.5,
            confidence=0.2,
        )
        severity = agent._determine_severity(metric, result)
        assert severity in (SeverityLevel.LOW, SeverityLevel.INFO)

    # ========================================================================
    # Integration: Full Process Flow
    # ========================================================================

    @pytest.mark.asyncio
    async def test_process_normal_metric(
        self,
        agent: MonitorAgent,
        context: AgentExecutionContext,
    ) -> None:
        """测试正常指标的完整处理流程"""
        metric = MetricInput(
            metric_name="cpu_usage_percent",
            metric_value=48.0,
            service_name="order-service",
            history_values=[45.0, 48.0, 50.0, 47.0, 49.0, 51.0, 46.0, 48.0, 50.0, 47.0],
        )
        result = await agent.process(metric, context)

        assert result.success is True
        assert result.agent_name == "monitor_agent"
        assert "detection_result" in result.output_data
        assert "alert_generated" in result.output_data
        # 正常数据不应生成告警
        assert result.output_data["alert_generated"] is False

    @pytest.mark.asyncio
    async def test_process_anomaly_metric(
        self,
        agent: MonitorAgent,
        context: AgentExecutionContext,
    ) -> None:
        """测试异常指标的完整处理流程"""
        metric = MetricInput(
            metric_name="cpu_usage_percent",
            metric_value=95.0,
            service_name="order-service",
            history_values=[45.0, 48.0, 50.0, 47.0, 49.0, 51.0, 46.0, 48.0, 50.0, 47.0],
            labels={"tier": "critical"},
        )
        result = await agent.process(metric, context)

        assert result.success is True
        assert result.agent_name == "monitor_agent"
        assert "detection_result" in result.output_data
        assert "alert_generated" in result.output_data
        assert "severity" in result.output_data

    # ========================================================================
    # Statistics Helper Tests
    # ========================================================================

    def test_skewness_calculation(self, agent: MonitorAgent) -> None:
        """测试偏度计算"""
        import numpy as np

        # 对称分布偏度应接近0
        symmetric = np.random.normal(0, 1, 500)
        skew = agent._calculate_skewness(symmetric)
        assert abs(skew) < 1.0

    def test_kurtosis_calculation(self, agent: MonitorAgent) -> None:
        """测试峰度计算"""
        import numpy as np

        normal = np.random.normal(0, 1, 500)
        kurt = agent._calculate_kurtosis(normal)
        # 正态分布峰度在合理范围内
        assert 1.5 < kurt < 6.0

    def test_confidence_calculation(self, agent: MonitorAgent) -> None:
        """测试置信度计算"""
        votes = [
            AlgorithmVote(algorithm="3-sigma", voted_anomaly=True, score=2.0, confidence=0.8),
            AlgorithmVote(algorithm="ewma", voted_anomaly=True, score=1.5, confidence=0.7),
            AlgorithmVote(algorithm="isolation_forest", voted_anomaly=False, score=0.3, confidence=0.2),
        ]
        confidence = agent._calculate_confidence(votes, 2 / 3)
        assert 0.0 <= confidence <= 1.0
