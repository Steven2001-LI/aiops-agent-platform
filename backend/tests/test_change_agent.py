"""
AIOps Agent Platform - ChangeAgent Integration Tests

测试ChangeAgent的风险评分、审批流程和审计日志。
"""

from __future__ import annotations

import pytest

from app.agents.change_agent import (
    AuditLogEntry,
    ChangeAgent,
    ChangeInput,
    RiskFactor,
)
from app.models.agent import AgentExecutionContext
from app.models.events import ApprovalStatus, HealEvent


class TestChangeAgent:
    """ChangeAgent集成测试套件"""

    @pytest.fixture
    def agent(self) -> ChangeAgent:
        """创建ChangeAgent实例"""
        return ChangeAgent()

    @pytest.fixture
    def context(self) -> AgentExecutionContext:
        """创建Agent执行上下文"""
        return AgentExecutionContext(
            incident_id="test-incident-001",
            input_data={"test": True},
        )

    @pytest.fixture
    def sample_heal_event(self) -> HealEvent:
        """创建测试用HealEvent"""
        return HealEvent(
            incident_id="test-incident-001",
            action="scale_up",
            action_category="auto_scale",
            level="L0",
            target_resource="order-service",
            dry_run=True,
            dry_run_result={
                "all_executable": True,
                "blast_radius_info": {
                    "blast_radius_ratio": 0.05,
                    "affected_service_count": 1,
                    "total_service_count": 20,
                },
            },
            requires_approval=False,
        )

    # ========================================================================
    # Risk Scoring Tests
    # ========================================================================

    def test_risk_weights_sum(self, agent: ChangeAgent) -> None:
        """
        测试风险权重之和为1

        确保所有风险因子权重之和为1.0。
        """
        total = sum(agent.RISK_WEIGHTS.values())
        assert abs(total - 1.0) < 0.001

    @pytest.mark.asyncio
    async def test_risk_scoring(self, agent: ChangeAgent, sample_heal_event: HealEvent) -> None:
        """
        测试风险评分计算

        确保能正确计算综合风险评分。
        """
        change_input = ChangeInput(
            heal_event=sample_heal_event,
            incident_id="test-incident-001",
            change_type="auto_heal",
        )
        context = await agent._gather_context(change_input)
        factors = agent._calculate_risk_factors(change_input, context)

        assert len(factors) > 0
        assert all(isinstance(f, RiskFactor) for f in factors)

        # 计算总分
        total_score = sum(f.weighted_score for f in factors)
        assert 0.0 <= total_score <= 1.0

    def test_time_risk_calculation(self, agent: ChangeAgent) -> None:
        """测试时间风险计算"""
        risk = agent._calculate_time_risk()
        assert 0.0 <= risk <= 1.0

    def test_service_tier(self, agent: ChangeAgent) -> None:
        """测试服务等级获取"""
        # order-service在拓扑中定义为critical
        tier = agent._get_service_tier("order-service")
        assert tier in ["critical", "standard", "low"]

    def test_change_type_risk(self, agent: ChangeAgent) -> None:
        """测试变更类型风险评分"""
        assert agent._calculate_change_type_risk("database") == 0.9
        assert agent._calculate_change_type_risk("restart") == 0.2
        assert agent._calculate_change_type_risk("auto_heal") == 0.3
        assert agent._calculate_change_type_risk("unknown") == 0.5

    def test_risk_level_determination(self, agent: ChangeAgent) -> None:
        """测试风险等级判定"""
        assert agent._determine_risk_level(0.1) == "low"
        assert agent._determine_risk_level(0.4) == "medium"
        assert agent._determine_risk_level(0.7) == "high"
        assert agent._determine_risk_level(0.9) == "critical"

    # ========================================================================
    # Approval Flow Tests
    # ========================================================================

    def test_approval_flow_low_risk(self, agent: ChangeAgent, sample_heal_event: HealEvent) -> None:
        """
        测试低风险审批流程

        低风险应自动批准。
        """
        change_input = ChangeInput(heal_event=sample_heal_event)
        decision = agent._make_decision(
            change_input,
            risk_score=0.1,
            risk_level="low",
            context={},
        )
        assert decision["status"] == ApprovalStatus.AUTO_APPROVED.value
        assert decision["auto_decision"] is True
        assert decision.get("approvers", []) == []

    def test_approval_flow_medium_risk(self, agent: ChangeAgent, sample_heal_event: HealEvent) -> None:
        """
        测试中风险审批流程

        中风险需要oncall确认。
        """
        change_input = ChangeInput(heal_event=sample_heal_event)
        decision = agent._make_decision(
            change_input,
            risk_score=0.4,
            risk_level="medium",
            context={},
        )
        assert decision["status"] == ApprovalStatus.PENDING.value
        assert decision["auto_decision"] is False
        assert "oncall" in decision.get("approvers", [])

    def test_approval_flow_high_risk(self, agent: ChangeAgent, sample_heal_event: HealEvent) -> None:
        """
        测试高风险审批流程

        高风险需要TL审批。
        """
        change_input = ChangeInput(heal_event=sample_heal_event)
        decision = agent._make_decision(
            change_input,
            risk_score=0.7,
            risk_level="high",
            context={},
        )
        assert decision["status"] == ApprovalStatus.PENDING.value
        assert "team_lead" in decision.get("approvers", [])

    def test_approval_flow_critical_risk(self, agent: ChangeAgent, sample_heal_event: HealEvent) -> None:
        """
        测试关键风险审批流程

        关键风险需要SRE Manager审批。
        """
        change_input = ChangeInput(heal_event=sample_heal_event)
        decision = agent._make_decision(
            change_input,
            risk_score=0.9,
            risk_level="critical",
            context={},
        )
        assert decision["status"] == ApprovalStatus.PENDING.value
        assert "sre_manager" in decision.get("approvers", [])

    # ========================================================================
    # Audit Logging Tests
    # ========================================================================

    def test_audit_logging(self, agent: ChangeAgent) -> None:
        """
        测试审计日志记录

        确保审批决策被正确记录到审计日志。
        """
        initial_count = len(agent._audit_log)

        agent._record_audit_log(
            ChangeInput(
                heal_event=sample_heal_event(),
                change_id="test-001",
                incident_id="test-incident",
            ),
            {"status": ApprovalStatus.AUTO_APPROVED.value},
            risk_score=0.2,
            risk_factors=[],
        )

        assert len(agent._audit_log) == initial_count + 1
        latest = agent._audit_log[-1]
        assert isinstance(latest, AuditLogEntry)
        assert latest.action == "change_decision"

    def test_audit_log_query(self, agent: ChangeAgent) -> None:
        """测试审计日志查询"""
        # 先记录几条日志
        for i in range(3):
            agent._record_audit_log(
                ChangeInput(
                    heal_event=sample_heal_event(),
                    change_id=f"test-query-{i}",
                    incident_id="test-query-incident",
                ),
                {"status": ApprovalStatus.PENDING.value},
                risk_score=0.5,
                risk_factors=[],
            )

        # 按incident查询
        results = agent.get_audit_log(incident_id="test-query-incident")
        assert len(results) >= 3

        # 按change_id查询
        results = agent.get_audit_log(change_id="test-query-0")
        assert len(results) >= 1

    def test_change_outcome_record(self, agent: ChangeAgent) -> None:
        """测试变更结果记录"""
        agent.record_change_outcome("test-change-001", "order-service", True)
        history = agent._change_success_history.get("order-service", {})
        assert history.get("total", 0) >= 1

        agent.record_change_outcome("test-change-002", "order-service", False)
        history = agent._change_success_history.get("order-service", {})
        assert history.get("total", 0) >= 2

    # ========================================================================
    # Escalation Tests
    # ========================================================================

    def test_escalation_check(self, agent: ChangeAgent) -> None:
        """测试超时升级检查"""
        change_input = ChangeInput(
            heal_event=sample_heal_event(),
            timeout_minutes=30,
        )
        decision = {
            "status": ApprovalStatus.PENDING.value,
            "approvers": ["oncall"],
        }
        escalation = agent._check_escalation(change_input, decision)

        assert escalation is not None
        assert escalation["escalation_enabled"] is True
        assert escalation["timeout_minutes"] == 30

    def test_no_escalation_for_approved(self, agent: ChangeAgent) -> None:
        """测试已批准的变更不升级"""
        change_input = ChangeInput(
            heal_event=sample_heal_event(),
        )
        decision = {
            "status": ApprovalStatus.AUTO_APPROVED.value,
        }
        escalation = agent._check_escalation(change_input, decision)
        assert escalation is None

    # ========================================================================
    # Full Process Integration Tests
    # ========================================================================

    @pytest.mark.asyncio
    async def test_full_change_process(
        self,
        agent: ChangeAgent,
        sample_heal_event: HealEvent,
        context: AgentExecutionContext,
    ) -> None:
        """
        测试完整变更审批流程

        端到端测试ChangeAgent的完整处理流程。
        """
        change_input = ChangeInput(
            heal_event=sample_heal_event,
            incident_id="test-incident-001",
            change_id="test-change-001",
            change_type="auto_heal",
        )
        result = await agent.process(change_input, context)

        assert result.success is True
        assert result.agent_name == "change_agent"
        assert "change_event" in result.output_data
        assert "risk_score" in result.output_data
        assert "risk_level" in result.output_data
        assert "approval_status" in result.output_data
        assert 0.0 <= result.output_data["risk_score"] <= 1.0

    @pytest.mark.asyncio
    async def test_change_process_high_risk(
        self,
        agent: ChangeAgent,
        context: AgentExecutionContext,
    ) -> None:
        """测试高风险变更流程"""
        high_risk_heal = HealEvent(
            incident_id="test-incident-high",
            action="rollback_deployment",
            action_category="rollback",
            level="L2",
            target_resource="order-service",
            dry_run=True,
            dry_run_result={
                "all_executable": True,
                "blast_radius_info": {
                    "blast_radius_ratio": 0.5,
                    "affected_service_count": 10,
                    "total_service_count": 20,
                },
            },
            requires_approval=True,
        )
        change_input = ChangeInput(
            heal_event=high_risk_heal,
            incident_id="test-incident-high",
            change_id="test-change-high",
            change_type="rollback",
        )
        result = await agent.process(change_input, context)

        assert result.success is True
        # 高风险应需要审批
        assert result.output_data["approval_status"] == ApprovalStatus.PENDING.value


# Helper to avoid pytest fixture issue

def sample_heal_event() -> HealEvent:
    """创建测试用HealEvent"""
    return HealEvent(
        incident_id="test-incident-001",
        action="scale_up",
        action_category="auto_scale",
        level="L0",
        target_resource="order-service",
        dry_run=True,
        dry_run_result={
            "all_executable": True,
            "blast_radius_info": {
                "blast_radius_ratio": 0.05,
                "affected_service_count": 1,
                "total_service_count": 20,
            },
        },
        requires_approval=False,
    )

