"""
AIOps Agent Platform - HealAgent Integration Tests

测试HealAgent的Playbook匹配、爆炸半径计算、熔断器状态机和分级分类。
"""

from __future__ import annotations

import pytest

from app.agents.heal_agent import (
    BlastRadiusResult,
    CircuitBreaker,
    CircuitBreakerState,
    DryRunResult,
    HealInput,
    HealAgent,
    HealLevel,
)
from app.models.agent import AgentExecutionContext
from app.models.events import RCAEvent


class TestHealAgent:
    """HealAgent集成测试套件"""

    @pytest.fixture
    def agent(self) -> HealAgent:
        """创建HealAgent实例"""
        return HealAgent()

    @pytest.fixture
    def context(self) -> AgentExecutionContext:
        """创建Agent执行上下文"""
        return AgentExecutionContext(
            incident_id="test-incident-001",
            input_data={"test": True},
        )

    @pytest.fixture
    def sample_rca(self) -> RCAEvent:
        """创建测试用RCAEvent"""
        return RCAEvent(
            incident_id="test-incident-001",
            root_cause="traffic_spike",
            confidence=0.85,
            impact_chain=["order-service"],
            evidence={
                "alert_metric": "cpu_usage_percent",
                "alert_value": 95.0,
                "affected_services": ["order-service"],
                "critical_services_affected": [],
            },
            recommended_actions=["scale_up_resources"],
        )

    # ========================================================================
    # Playbook Matching Tests
    # ========================================================================

    def test_playbook_matching(self, agent: HealAgent, sample_rca: RCAEvent) -> None:
        """
        测试Playbook匹配

        确保能根据RCA结果匹配到合适的Playbook。
        """
        playbook = agent._match_playbook(sample_rca)

        assert playbook is not None
        assert "id" in playbook
        assert "actions" in playbook
        assert len(playbook["actions"]) > 0

    def test_playbook_matching_with_metric(self, agent: HealAgent) -> None:
        """测试基于指标的Playbook匹配"""
        rca = RCAEvent(
            incident_id="test-002",
            root_cause="resource_exhaustion",
            confidence=0.8,
            evidence={
                "alert_metric": "cpu_usage_percent",
                "affected_services": ["order-service"],
            },
        )
        playbook = agent._match_playbook(rca)
        assert playbook is not None
        assert "id" in playbook

    # ========================================================================
    # Blast Radius Calculation Tests
    # ========================================================================

    def test_blast_radius_calc(self, agent: HealAgent, sample_rca: RCAEvent) -> None:
        """
        测试爆炸半径计算

        确保能正确计算影响范围。
        """
        result = agent._evaluate_blast_radius(sample_rca)

        assert isinstance(result, BlastRadiusResult)
        assert result.total_service_count > 0
        assert result.affected_service_count >= 1
        assert 0.0 <= result.blast_radius_ratio <= 1.0
        assert result.risk_level in ["low", "medium", "high", "critical"]

    def test_blast_radius_empty(self, agent: HealAgent) -> None:
        """测试空影响范围的爆炸半径"""
        rca = RCAEvent(
            incident_id="test-empty",
            root_cause="unknown",
            evidence={},
        )
        result = agent._evaluate_blast_radius(rca)

        assert isinstance(result, BlastRadiusResult)
        assert result.blast_radius_ratio >= 0.0

    def test_blast_radius_critical_services(self, agent: HealAgent) -> None:
        """测试关键服务影响的爆炸半径"""
        rca = RCAEvent(
            incident_id="test-critical",
            root_cause="dependency_failure",
            evidence={
                "affected_services": ["order-service", "payment-service"],
                "critical_services_affected": ["order-service"],
            },
        )
        result = agent._evaluate_blast_radius(rca)
        assert result.risk_level in ["medium", "high", "critical"]

    # ========================================================================
    # Circuit Breaker Tests
    # ========================================================================

    def test_circuit_breaker_closed(self) -> None:
        """
        测试熔断器关闭状态

        初始状态应为CLOSED，允许执行。
        """
        cb = CircuitBreaker()
        assert cb.state == CircuitBreakerState.CLOSED
        assert cb.can_execute() is True
        assert cb.failure_count == 0

    def test_circuit_breaker_open(self) -> None:
        """
        测试熔断器打开状态

        连续失败5次后应打开。
        """
        cb = CircuitBreaker()
        for _ in range(5):
            cb.record_failure()

        assert cb.state == CircuitBreakerState.OPEN
        assert cb.can_execute() is False
        assert cb.failure_count >= 5

    def test_circuit_breaker_half_open(self) -> None:
        """
        测试熔断器半开状态

        OPEN状态下经过冷却时间应转为HALF_OPEN。
        """
        cb = CircuitBreaker()
        for _ in range(5):
            cb.record_failure()

        assert cb.state == CircuitBreakerState.OPEN

        # 手动转为半开（模拟时间流逝）
        cb._transition_to(CircuitBreakerState.HALF_OPEN)
        assert cb.state == CircuitBreakerState.HALF_OPEN
        assert cb.can_execute() is True

    def test_circuit_breaker_recovery(self) -> None:
        """
        测试熔断器恢复

        HALF_OPEN状态下连续成功3次应恢复CLOSED。
        """
        cb = CircuitBreaker()
        cb._transition_to(CircuitBreakerState.HALF_OPEN)

        for _ in range(3):
            cb.record_success()

        assert cb.state == CircuitBreakerState.CLOSED
        assert cb.failure_count == 0

    def test_circuit_breaker_half_open_failure(self) -> None:
        """
        测试半开状态下再次失败

        HALF_OPEN状态下失败应回到OPEN。
        """
        cb = CircuitBreaker()
        cb._transition_to(CircuitBreakerState.HALF_OPEN)

        cb.record_failure()
        assert cb.state == CircuitBreakerState.OPEN

    def test_circuit_breaker_success_reset(self) -> None:
        """测试成功重置失败计数"""
        cb = CircuitBreaker()
        cb.record_failure()
        cb.record_failure()
        assert cb.failure_count == 2

        cb.record_success()
        assert cb.failure_count == 0

    # ========================================================================
    # Level Classification Tests
    # ========================================================================

    def test_level_classification_l0(self, agent: HealAgent) -> None:
        """
        测试L0分级 - 低风险自动执行

        影响范围<5%应归为L0。
        """
        radius = BlastRadiusResult(
            affected_service_count=1,
            total_service_count=50,
            blast_radius_ratio=0.02,
            risk_level="low",
        )
        level = agent._determine_heal_level(radius)
        assert level == HealLevel.L0_AUTO

    def test_level_classification_l1(self, agent: HealAgent) -> None:
        """
        测试L1分级 - 中风险需确认

        影响范围5-20%应归为L1。
        """
        radius = BlastRadiusResult(
            affected_service_count=5,
            total_service_count=50,
            blast_radius_ratio=0.10,
            risk_level="medium",
        )
        level = agent._determine_heal_level(radius)
        assert level == HealLevel.L1_CONFIRM

    def test_level_classification_l2(self, agent: HealAgent) -> None:
        """
        测试L2分级 - 高风险需审批

        影响范围>20%应归为L2。
        """
        radius = BlastRadiusResult(
            affected_service_count=15,
            total_service_count=50,
            blast_radius_ratio=0.50,
            risk_level="high",
        )
        level = agent._determine_heal_level(radius)
        assert level == HealLevel.L2_APPROVE

    # ========================================================================
    # Dry-Run Tests
    # ========================================================================

    def test_dry_run_valid(self, agent: HealAgent, sample_rca: RCAEvent) -> None:
        """测试有效操作的dry-run"""
        action = {"type": "scale_up", "replicas": "+2"}
        radius = BlastRadiusResult(
            affected_service_count=1,
            total_service_count=20,
            blast_radius_ratio=0.05,
            risk_level="low",
        )
        result = agent._dry_run_action(action, sample_rca, radius)

        assert isinstance(result, DryRunResult)
        assert result.executable is True
        assert result.syntax_valid is True
        assert len(result.command_preview) > 0

    def test_dry_run_invalid(self, agent: HealAgent, sample_rca: RCAEvent) -> None:
        """测试无效操作的dry-run"""
        action = {"type": "invalid_action_xyz"}
        radius = BlastRadiusResult(
            affected_service_count=1,
            total_service_count=20,
            blast_radius_ratio=0.05,
            risk_level="low",
        )
        result = agent._dry_run_action(action, sample_rca, radius)

        assert isinstance(result, DryRunResult)
        assert result.executable is False
        assert result.syntax_valid is False

    # ========================================================================
    # Rollback Plan Tests
    # ========================================================================

    def test_rollback_plan(self, agent: HealAgent) -> None:
        """测试回滚计划生成"""
        actions = [{"type": "scale_up"}]
        plan = agent._build_rollback_plan(None, actions)

        assert len(plan) > 0
        assert plan[0]["type"] == "scale_down"

    def test_rollback_plan_restart(self, agent: HealAgent) -> None:
        """测试重启操作的回滚计划"""
        actions = [{"type": "restart"}]
        plan = agent._build_rollback_plan(None, actions)

        assert len(plan) > 0
        # restart通常没有自动回滚

    # ========================================================================
    # Full Process Integration Tests
    # ========================================================================

    @pytest.mark.asyncio
    async def test_full_heal_process(
        self,
        agent: HealAgent,
        sample_rca: RCAEvent,
        context: AgentExecutionContext,
    ) -> None:
        """
        测试完整自愈流程

        端到端测试HealAgent的完整处理流程。
        """
        heal_input = HealInput(
            rca_event=sample_rca,
            incident_id="test-incident-001",
            dry_run=True,
        )
        result = await agent.process(heal_input, context)

        assert result.success is True
        assert result.agent_name == "heal_agent"
        assert "heal_event" in result.output_data
        assert "heal_level" in result.output_data
        assert "blast_radius" in result.output_data
        assert "playbook_matched" in result.output_data

    @pytest.mark.asyncio
    async def test_heal_circuit_breaker_open(
        self,
        agent: HealAgent,
        sample_rca: RCAEvent,
        context: AgentExecutionContext,
    ) -> None:
        """测试熔断器打开时的自愈流程"""
        # 强制打开熔断器
        for _ in range(5):
            agent._circuit_breaker.record_failure()

        assert agent._circuit_breaker.state == CircuitBreakerState.OPEN

        heal_input = HealInput(
            rca_event=sample_rca,
            incident_id="test-incident-001",
        )
        result = await agent.process(heal_input, context)

        # 熔断器打开时应返回失败
        assert result.success is False
        assert "circuit breaker" in result.error_message.lower()
