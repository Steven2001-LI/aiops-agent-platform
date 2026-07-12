"""
AIOps Agent Platform - Change Agent

变更审批 Agent，负责自动化变更风险评估和审批决策。
实现风险评分模型、分级审批、审计日志和超时自动升级机制。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.agents.base import AgentResult, BaseAgent
from app.data.knowledge_base import SERVICE_TOPOLOGY
from app.models.agent import AgentExecutionContext
from app.models.events import ApprovalStatus, ChangeEvent, HealEvent
from app.utils.logging import get_logger

logger = get_logger(__name__)


class ChangeInput(BaseModel):
    """Change Agent 输入"""
    heal_event: HealEvent = Field(description="Heal 事件")
    incident_id: str = Field(default="", description="关联故障ID")
    change_id: str = Field(default="", description="变更单ID")
    change_type: str = Field(default="auto_heal", description="变更类型")
    requester: str = Field(default="heal_agent", description="请求者")
    change_details: dict[str, Any] = Field(default_factory=dict, description="变更详情")
    timeout_minutes: int = Field(default=30, description="超时时间(分钟)")


class RiskFactor(BaseModel):
    """风险因子"""
    name: str = Field(description="因子名称")
    weight: float = Field(description="权重")
    score: float = Field(description="得分")
    weighted_score: float = Field(description="加权得分")
    description: str = Field(default="", description="描述")


class AuditLogEntry(BaseModel):
    """审计日志条目"""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    incident_id: str = Field(default="")
    change_id: str = Field(default="")
    action: str = Field(default="")
    actor: str = Field(default="")
    details: dict[str, Any] = Field(default_factory=dict)
    risk_score: float = Field(default=0.0)
    approval_status: str = Field(default="")


class ChangeAgent(BaseAgent[ChangeInput, ChangeEvent]):
    """
    变更审批 Agent

    职责：
    - 风险评分模型（爆炸半径 0.3 + 历史成功率 0.2 + 时间因素 0.15 + 服务等级 0.2 + 变更类型 0.15）
    - 分级审批：L0 自动 / L1 oncall 确认 / L2 TL 审批
    - 审计日志记录（所有决策记录到 audit topic）
    - 超时自动升级机制
    """

    # 风险权重配置
    RISK_WEIGHTS = {
        "blast_radius": 0.30,
        "historical_success_rate": 0.20,
        "time_factor": 0.15,
        "service_tier": 0.20,
        "change_type": 0.15,
    }

    # 风险等级阈值
    RISK_LEVEL_LOW = 0.30
    RISK_LEVEL_MEDIUM = 0.60
    RISK_LEVEL_HIGH = 0.80

    # 审批超时时间（分钟）
    DEFAULT_TIMEOUT_MINUTES = 30

    # 变更成功率历史记录
    _change_success_history: dict[str, dict[str, Any]] = {}

    # 审计日志
    _audit_log: list[AuditLogEntry] = []

    def __init__(self) -> None:
        super().__init__()

    def get_name(self) -> str:
        return "change_agent"

    def get_description(self) -> str:
        return "变更审批 Agent - 风险评估、分级审批、审计日志、超时升级"

    # ==================== 核心处理 ====================

    async def process(
        self,
        input_data: ChangeInput,
        context: AgentExecutionContext,
    ) -> AgentResult:
        """
        处理变更审批

        Args:
            input_data: 变更输入
            context: 执行上下文

        Returns:
            AgentResult: 包含 ChangeEvent 的结果
        """
        logger.info(
            "ChangeAgent processing",
            change_id=input_data.change_id,
            change_type=input_data.change_type,
            incident_id=input_data.incident_id,
        )

        # Step 1: 收集变更上下文
        change_context = await self._gather_context(input_data)

        # Step 2: 风险评分
        risk_factors = self._calculate_risk_factors(input_data, change_context)
        risk_score = sum(f.weighted_score for f in risk_factors)
        risk_score = round(min(max(risk_score, 0.0), 1.0), 4)

        # Step 3: 风险等级
        risk_level = self._determine_risk_level(risk_score)

        # Step 4: 做出审批决策
        decision = self._make_decision(input_data, risk_score, risk_level, change_context)

        # Step 5: 生成审计日志
        self._record_audit_log(input_data, decision, risk_score, risk_factors)

        # Step 6: 检查超时升级
        escalation = self._check_escalation(input_data, decision)

        # 构建 ChangeEvent
        change_event = ChangeEvent(
            correlation_id=input_data.heal_event.correlation_id,
            source=self.get_name(),
            incident_id=input_data.incident_id,
            change_id=input_data.change_id or self._generate_change_id(input_data),
            change_type=input_data.change_type,
            approval_status=ApprovalStatus(decision["status"]),
            risk_score=risk_score,
            risk_level=risk_level,
            approvers=decision.get("approvers", []),
            change_details={
                "heal_action": input_data.heal_event.action,
                "heal_level": input_data.heal_event.level,
                "dry_run_passed": input_data.heal_event.dry_run_result.get("all_executable", False),
                "risk_factors": [f.model_dump() for f in risk_factors],
                "blast_radius": input_data.heal_event.dry_run_result.get("blast_radius_info", {}),
                "context": change_context,
            },
            automated_decision_reason=decision.get("reason", ""),
        )

        output_data = {
            "change_event": change_event.model_dump(),
            "risk_score": risk_score,
            "risk_level": risk_level,
            "risk_factors": [f.model_dump() for f in risk_factors],
            "approval_status": decision["status"],
            "approvers": decision.get("approvers", []),
            "auto_decision": decision.get("auto_decision", False),
            "escalation": escalation,
            "audit_log_id": self._audit_log[-1].timestamp.isoformat() if self._audit_log else None,
        }

        return AgentResult.success_result(
            agent_name=self.get_name(),
            output_data=output_data,
        )

    # ==================== 风险评分 ====================

    def _calculate_risk_factors(
        self,
        input_data: ChangeInput,
        context: dict[str, Any],
    ) -> list[RiskFactor]:
        """
        计算各风险因子得分

        总分 = 爆炸半径*0.3 + 历史成功率*0.2 + 时间因素*0.15 + 服务等级*0.2 + 变更类型*0.15
        """
        factors: list[RiskFactor] = []
        heal_event = input_data.heal_event

        # 因子 1: 爆炸半径
        blast_radius_info = heal_event.dry_run_result.get("blast_radius_info", {})
        blast_radius = blast_radius_info.get("blast_radius_ratio", 0.05)
        blast_radius_score = min(blast_radius * 5, 1.0)  # 归一化
        factors.append(RiskFactor(
            name="blast_radius",
            weight=self.RISK_WEIGHTS["blast_radius"],
            score=round(blast_radius_score, 4),
            weighted_score=round(blast_radius_score * self.RISK_WEIGHTS["blast_radius"], 4),
            description=f"Blast radius: {blast_radius:.2%}",
        ))

        # 因子 2: 历史成功率
        service = heal_event.target_resource
        hist = self._change_success_history.get(service, {})
        total = hist.get("total", 0)
        success = hist.get("success", 0)
        if total > 0:
            success_rate = success / total
        else:
            success_rate = 0.8  # 默认 80%
        # 成功率越低，风险越高
        historical_risk = 1.0 - success_rate
        factors.append(RiskFactor(
            name="historical_success_rate",
            weight=self.RISK_WEIGHTS["historical_success_rate"],
            score=round(historical_risk, 4),
            weighted_score=round(historical_risk * self.RISK_WEIGHTS["historical_success_rate"], 4),
            description=f"Historical success rate: {success_rate:.1%} ({success}/{total})",
        ))

        # 因子 3: 时间因素
        time_risk = self._calculate_time_risk()
        factors.append(RiskFactor(
            name="time_factor",
            weight=self.RISK_WEIGHTS["time_factor"],
            score=round(time_risk, 4),
            weighted_score=round(time_risk * self.RISK_WEIGHTS["time_factor"], 4),
            description="Time-based risk factor",
        ))

        # 因子 4: 服务等级
        service_tier = self._get_service_tier(service)
        tier_risk_map = {"critical": 1.0, "standard": 0.5, "low": 0.2}
        tier_risk = tier_risk_map.get(service_tier, 0.5)
        factors.append(RiskFactor(
            name="service_tier",
            weight=self.RISK_WEIGHTS["service_tier"],
            score=round(tier_risk, 4),
            weighted_score=round(tier_risk * self.RISK_WEIGHTS["service_tier"], 4),
            description=f"Service tier: {service_tier}",
        ))

        # 因子 5: 变更类型
        change_type_risk = self._calculate_change_type_risk(input_data.change_type)
        factors.append(RiskFactor(
            name="change_type",
            weight=self.RISK_WEIGHTS["change_type"],
            score=round(change_type_risk, 4),
            weighted_score=round(change_type_risk * self.RISK_WEIGHTS["change_type"], 4),
            description=f"Change type: {input_data.change_type}",
        ))

        return factors

    def _calculate_time_risk(self) -> float:
        """
        时间因素风险

        业务高峰期变更风险更高。
        简化版：工作日 9-18 点高风险，周末/夜间低风险。
        """
        now = datetime.now(timezone.utc)
        hour = now.hour
        weekday = now.weekday()  # 0=Monday, 6=Sunday

        # 周末
        if weekday >= 5:
            return 0.2

        # 夜间
        if hour < 8 or hour > 20:
            return 0.3

        # 工作日上午
        if 9 <= hour < 12:
            return 0.9

        # 工作日下午
        if 12 <= hour < 18:
            return 0.7

        # 晚间
        return 0.4

    def _get_service_tier(self, service: str) -> str:
        """获取服务等级"""
        topology = SERVICE_TOPOLOGY.get(service, {})
        return topology.get("tier", "standard")

    def _calculate_change_type_risk(self, change_type: str) -> float:
        """变更类型风险评分"""
        risk_map: dict[str, float] = {
            "auto_heal": 0.3,
            "deployment": 0.7,
            "config": 0.5,
            "infra": 0.8,
            "database": 0.9,
            "rollback": 0.4,
            "restart": 0.2,
            "scale": 0.3,
        }
        return risk_map.get(change_type, 0.5)

    # ==================== 风险等级判定 ====================

    def _determine_risk_level(self, risk_score: float) -> str:
        """确定风险等级"""
        if risk_score >= self.RISK_LEVEL_HIGH:
            return "critical"
        elif risk_score >= self.RISK_LEVEL_MEDIUM:
            return "high"
        elif risk_score >= self.RISK_LEVEL_LOW:
            return "medium"
        else:
            return "low"

    # ==================== 审批决策 ====================

    def _make_decision(
        self,
        input_data: ChangeInput,
        risk_score: float,
        risk_level: str,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """
        做出审批决策

        - 低风险 -> 自动批准
        - 中风险 -> 需要 oncall 确认
        - 高风险 -> 需要 TL 审批
        """
        if risk_score < self.RISK_LEVEL_LOW:
            return {
                "status": ApprovalStatus.AUTO_APPROVED.value,
                "risk_level": risk_level,
                "reason": f"Low risk (score={risk_score:.4f}), auto-approved",
                "auto_decision": True,
                "approvers": [],
            }
        elif risk_score < self.RISK_LEVEL_MEDIUM:
            return {
                "status": ApprovalStatus.PENDING.value,
                "risk_level": risk_level,
                "reason": f"Medium risk (score={risk_score:.4f}), awaiting oncall confirmation",
                "auto_decision": False,
                "approvers": ["oncall"],
                "conditions": ["require_oncall_ack"],
            }
        elif risk_score < self.RISK_LEVEL_HIGH:
            return {
                "status": ApprovalStatus.PENDING.value,
                "risk_level": risk_level,
                "reason": f"High risk (score={risk_score:.4f}), requires team lead approval",
                "auto_decision": False,
                "approvers": ["oncall", "team_lead"],
                "conditions": ["require_dry_run", "require_rollback_plan", "require_team_lead_approval"],
            }
        else:
            return {
                "status": ApprovalStatus.PENDING.value,
                "risk_level": "critical",
                "reason": f"Critical risk (score={risk_score:.4f}), requires senior approval",
                "auto_decision": False,
                "approvers": ["oncall", "team_lead", "sre_manager"],
                "conditions": [
                    "require_dry_run",
                    "require_rollback_plan",
                    "require_senior_approval",
                    "require_incident_review",
                ],
            }

    # ==================== 审计日志 ====================

    def _record_audit_log(
        self,
        input_data: ChangeInput,
        decision: dict[str, Any],
        risk_score: float,
        risk_factors: list[RiskFactor],
    ) -> None:
        """记录审计日志"""
        entry = AuditLogEntry(
            timestamp=datetime.now(timezone.utc),
            incident_id=input_data.incident_id,
            change_id=input_data.change_id,
            action="change_decision",
            actor=self.get_name(),
            details={
                "change_type": input_data.change_type,
                "heal_action": input_data.heal_event.action,
                "risk_score": risk_score,
                "risk_factors": [
                    {"name": f.name, "score": f.score, "weighted": f.weighted_score}
                    for f in risk_factors
                ],
                "decision": decision,
                "requester": input_data.requester,
            },
            risk_score=risk_score,
            approval_status=decision["status"],
        )
        self._audit_log.append(entry)

        logger.info(
            "Audit log recorded",
            change_id=input_data.change_id,
            action=entry.action,
            risk_score=risk_score,
            status=decision["status"],
        )

    def get_audit_log(
        self,
        incident_id: str | None = None,
        change_id: str | None = None,
        limit: int = 100,
    ) -> list[AuditLogEntry]:
        """查询审计日志"""
        results = self._audit_log

        if incident_id:
            results = [e for e in results if e.incident_id == incident_id]

        if change_id:
            results = [e for e in results if e.change_id == change_id]

        return results[-limit:]

    # ==================== 超时升级 ====================

    def _check_escalation(
        self,
        input_data: ChangeInput,
        decision: dict[str, Any],
    ) -> dict[str, Any] | None:
        """
        检查是否需要超时升级

        如果审批长时间未响应，自动升级。
        """
        # 只有 PENDING 状态的才需要检查升级
        if decision["status"] != ApprovalStatus.PENDING.value:
            return None

        # 实际实现中，这里应该查询审批系统的状态
        # 简化版：直接返回升级配置
        return {
            "escalation_enabled": True,
            "timeout_minutes": input_data.timeout_minutes,
            "escalation_path": decision.get("approvers", []) + ["sre_manager"],
            "auto_reject_after_timeout": False,
            "message": f"Will escalate after {input_data.timeout_minutes} minutes of inactivity",
        }

    # ==================== 辅助方法 ====================

    async def _gather_context(self, input_data: ChangeInput) -> dict[str, Any]:
        """收集变更上下文"""
        context: dict[str, Any] = {
            "service": input_data.heal_event.target_resource,
            "heal_level": input_data.heal_event.level,
            "dry_run_result": input_data.heal_event.dry_run_result,
            "actions": input_data.heal_event.action,
            "requires_approval": input_data.heal_event.requires_approval,
        }

        # 检查是否有正在进行的变更
        pending_changes = [
            log for log in self._audit_log
            if log.approval_status == ApprovalStatus.PENDING.value
            and log.timestamp > datetime.now(timezone.utc) - timedelta(hours=1)
        ]
        context["concurrent_pending_changes"] = len(pending_changes)

        return context

    @staticmethod
    def _generate_change_id(input_data: ChangeInput) -> str:
        """生成变更单ID"""
        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y%m%d%H%M%S")
        return f"CHG-{input_data.change_type.upper()}-{timestamp}-{input_data.incident_id[:8]}"

    def record_change_outcome(self, change_id: str, service: str, success: bool) -> None:
        """记录变更结果，用于更新历史成功率"""
        if service not in self._change_success_history:
            self._change_success_history[service] = {"total": 0, "success": 0}

        self._change_success_history[service]["total"] += 1
        if success:
            self._change_success_history[service]["success"] += 1

        # 同时记录审计日志
        entry = AuditLogEntry(
            timestamp=datetime.now(timezone.utc),
            change_id=change_id,
            action="change_outcome",
            actor="system",
            details={"success": success, "service": service},
        )
        self._audit_log.append(entry)

        logger.info(
            "Change outcome recorded",
            change_id=change_id,
            service=service,
            success=success,
        )
