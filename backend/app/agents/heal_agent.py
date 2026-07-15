"""
AIOps Agent Platform - Heal Agent

故障自愈 Agent，负责自动化故障修复。
支持 Playbook 匹配、dry-run 模拟、爆炸半径评估和分级自愈策略。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.agents.base import AgentResult, BaseAgent
from app.data.knowledge_base import SERVICE_TOPOLOGY
from app.data.public_playbooks import get_all_playbooks
from app.models.agent import AgentExecutionContext
from app.models.events import HealEvent, RCAEvent
from app.utils.logging import get_logger

logger = get_logger(__name__)


class HealLevel(str, Enum):
    """自愈级别"""
    L0_AUTO = "L0"       # 低风险，自动执行
    L1_CONFIRM = "L1"    # 中风险，需要 oncall 确认
    L2_APPROVE = "L2"    # 高风险，需要 TL 审批


class CircuitBreakerState(str, Enum):
    """熔断器状态"""
    CLOSED = "closed"       # 关闭 - 正常执行
    OPEN = "open"           # 打开 - 拒绝执行
    HALF_OPEN = "half_open"  # 半开 - 试探执行


class CircuitBreaker(BaseModel):
    """熔断器"""
    state: CircuitBreakerState = Field(default=CircuitBreakerState.CLOSED)
    failure_count: int = Field(default=0)
    success_count: int = Field(default=0)
    last_failure_time: datetime | None = Field(default=None)
    last_state_change: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    # 配置参数
    FAILURE_THRESHOLD: int = 5      # 连续失败阈值
    HALF_OPEN_TIMEOUT_MINUTES: int = 10  # 半开等待时间
    SUCCESS_TO_CLOSE: int = 3       # 半开状态成功次数恢复关闭

    def record_success(self) -> None:
        """记录成功"""
        self.success_count += 1
        if self.state == CircuitBreakerState.HALF_OPEN:
            if self.success_count >= self.SUCCESS_TO_CLOSE:
                self._transition_to(CircuitBreakerState.CLOSED)
        else:
            self.failure_count = 0

    def record_failure(self) -> None:
        """记录失败"""
        self.failure_count += 1
        self.last_failure_time = datetime.now(timezone.utc)
        self.success_count = 0

        if self.state == CircuitBreakerState.HALF_OPEN:
            self._transition_to(CircuitBreakerState.OPEN)
        elif (
            self.state == CircuitBreakerState.CLOSED
            and self.failure_count >= self.FAILURE_THRESHOLD
        ):
            self._transition_to(CircuitBreakerState.OPEN)

    def can_execute(self) -> bool:
        """检查是否允许执行"""
        now = datetime.now(timezone.utc)

        if self.state == CircuitBreakerState.CLOSED:
            return True

        elif self.state == CircuitBreakerState.OPEN:
            # 检查是否已过冷却时间
            if self.last_failure_time:
                elapsed = (now - self.last_failure_time).total_seconds() / 60
                if elapsed >= self.HALF_OPEN_TIMEOUT_MINUTES:
                    self._transition_to(CircuitBreakerState.HALF_OPEN)
                    return True
            return False

        elif self.state == CircuitBreakerState.HALF_OPEN:
            return True

        return False

    def _transition_to(self, new_state: CircuitBreakerState) -> None:
        """状态转换"""
        old_state = self.state
        self.state = new_state
        self.last_state_change = datetime.now(timezone.utc)
        if new_state == CircuitBreakerState.CLOSED:
            self.failure_count = 0
            self.success_count = 0
        elif new_state == CircuitBreakerState.HALF_OPEN:
            self.success_count = 0
        logger.info(
            "Circuit breaker state transition",
            old=old_state.value,
            new=new_state.value,
        )


class HealInput(BaseModel):
    """Heal Agent 输入"""
    rca_event: RCAEvent = Field(description="RCA 事件")
    incident_id: str = Field(default="", description="关联故障ID")
    dry_run: bool = Field(default=True, description="是否仅模拟执行")


class DryRunResult(BaseModel):
    """Dry-run 结果"""
    executable: bool = Field(description="是否可执行")
    syntax_valid: bool = Field(default=True, description="语法是否有效")
    permission_check: bool = Field(default=True, description="权限检查是否通过")
    estimated_duration_seconds: int = Field(default=0, description="预计执行时间")
    warnings: list[str] = Field(default_factory=list, description="警告信息")
    errors: list[str] = Field(default_factory=list, description="错误信息")
    command_preview: list[str] = Field(default_factory=list, description="命令预览")


class BlastRadiusResult(BaseModel):
    """爆炸半径评估结果"""
    affected_service_count: int = Field(description="受影响服务数")
    total_service_count: int = Field(description="总服务数")
    blast_radius_ratio: float = Field(description="爆炸半径比例")
    critical_services_affected: list[str] = Field(default_factory=list, description="受影响的关键服务")
    risk_level: str = Field(default="unknown", description="风险级别")


class HealAgent(BaseAgent[HealInput, HealEvent]):
    """
    故障自愈 Agent

    职责：
    - Playbook 匹配（基于 alert_name 和 service 匹配修复剧本）
    - dry-run 模拟执行（验证命令语法和权限）
    - 爆炸半径评估（影响服务数 / 总服务数）
    - 熔断器模式（连续 5 次失败打开，10 分钟后半开）
    - 分级自愈策略（L0/L1/L2）
    """

    def __init__(self) -> None:
        super().__init__()
        self._playbooks = get_all_playbooks()  # 内置 + 社区公开 Playbook 合并
        self._service_topology = SERVICE_TOPOLOGY
        self._circuit_breaker = CircuitBreaker()
        # 自愈历史记录
        self._heal_history: list[dict[str, Any]] = []

    def get_name(self) -> str:
        return "heal_agent"

    def get_description(self) -> str:
        return "故障自愈 Agent - Playbook 匹配、dry-run、爆炸半径评估、分级自愈"

    # ==================== 核心处理 ====================

    async def process(
        self,
        input_data: HealInput,
        context: AgentExecutionContext,
    ) -> AgentResult:
        """
        执行故障自愈流程

        Args:
            input_data: 自愈输入
            context: 执行上下文

        Returns:
            AgentResult: 包含 HealEvent 的结果
        """
        rca = input_data.rca_event
        logger.info(
            "HealAgent processing",
            incident_id=input_data.incident_id,
            root_cause=rca.root_cause,
            confidence=rca.confidence,
        )

        # Step 1: 检查熔断器
        if not self._circuit_breaker.can_execute():
            logger.warning(
                "Circuit breaker is OPEN, rejecting heal request",
                state=self._circuit_breaker.state.value,
            )
            return AgentResult.failure_result(
                agent_name=self.get_name(),
                error_message=f"Circuit breaker is {self._circuit_breaker.state.value}, "
                              f"try again after {self._circuit_breaker.HALF_OPEN_TIMEOUT_MINUTES} minutes",
            )

        # Step 2: Playbook 匹配
        matched_playbook = self._match_playbook(rca)

        # Step 3: 爆炸半径评估
        blast_radius = self._evaluate_blast_radius(rca)

        # Step 4: 确定自愈级别
        heal_level = self._determine_heal_level(blast_radius)

        # Step 5: 构建自愈操作
        actions = self._build_actions(matched_playbook, rca)

        # Step 6: Dry-run 模拟执行
        dry_run_results: list[DryRunResult] = []
        for action in actions:
            dry_result = self._dry_run_action(action, rca, blast_radius)
            dry_run_results.append(dry_result)

        # Step 7: 生成回滚计划
        rollback_plan = self._build_rollback_plan(matched_playbook, actions)

        # Step 8: 记录历史
        self._heal_history.append({
            "incident_id": input_data.incident_id,
            "root_cause": rca.root_cause,
            "level": heal_level.value,
            "actions": [a.model_dump() if hasattr(a, "model_dump") else a for a in actions],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        # 构建 HealEvent
        all_executable = all(d.executable for d in dry_run_results)

        heal_event = HealEvent(
            correlation_id=rca.correlation_id,
            source=self.get_name(),
            incident_id=input_data.incident_id,
            action="; ".join(a.get("type", "unknown") for a in actions),
            action_category=matched_playbook["id"] if matched_playbook else "manual",
            level=heal_level.value,
            target_resource=rca.evidence.get("affected_services", ["unknown"])[0]
            if rca.evidence.get("affected_services") else "unknown",
            dry_run=input_data.dry_run,
            dry_run_result={
                "all_executable": all_executable,
                "results": [d.model_dump() for d in dry_run_results],
                "playbook_matched": matched_playbook["id"] if matched_playbook else None,
            },
            execution_result={},
            status="pending" if heal_level == HealLevel.L0_AUTO and all_executable else "pending",
            execution_logs=[
                f"Playbook matched: {matched_playbook['name'] if matched_playbook else 'None'}",
                f"Blast radius: {blast_radius.blast_radius_ratio:.2%} ({blast_radius.risk_level})",
                f"Heal level: {heal_level.value} ({heal_level.name})",
                f"Circuit breaker: {self._circuit_breaker.state.value}",
                f"Dry-run passed: {all_executable}",
            ],
            requires_approval=heal_level != HealLevel.L0_AUTO,
        )

        # 更新熔断器状态
        if all_executable:
            self._circuit_breaker.record_success()
        else:
            self._circuit_breaker.record_failure()

        output_data = {
            "heal_event": heal_event.model_dump(),
            "heal_level": heal_level.value,
            "blast_radius": blast_radius.model_dump(),
            "playbook_matched": matched_playbook["id"] if matched_playbook else None,
            "actions_count": len(actions),
            "dry_run_passed": all_executable,
            "rollback_plan": rollback_plan,
            "circuit_breaker_state": self._circuit_breaker.state.value,
        }

        return AgentResult.success_result(
            agent_name=self.get_name(),
            output_data=output_data,
        )

    # ==================== Playbook 匹配 ====================

    def _match_playbook(self, rca: RCAEvent) -> dict[str, Any]:
        """
        匹配最佳 Playbook

        基于 RCA 结果中的指标和根因类型匹配 Playbook。
        """
        best_match: dict[str, Any] | None = None
        best_score = 0.0

        # 从证据中提取指标
        alert_metric = rca.evidence.get("alert_metric", "")
        root_cause = rca.root_cause

        for playbook in self._playbooks:
            score = 0.0

            # 指标匹配
            trigger_metric = playbook.get("trigger", {}).get("metric", "")
            if trigger_metric and alert_metric:
                if trigger_metric == alert_metric:
                    score += 0.5
                elif trigger_metric.split("_")[0] == alert_metric.split("_")[0]:
                    score += 0.2

            # 根因类别匹配
            cause_to_metric: dict[str, str] = {
                "resource_exhaustion": "cpu_usage_percent",
                "memory_leak": "memory_usage_percent",
                "dependency_failure": "error_rate_percent",
                "network_issue": "p99_latency_ms",
                "recent_deployment": "error_rate_percent",
                "database_issue": "p99_latency_ms",
            }
            mapped_metric = cause_to_metric.get(root_cause, "")
            if mapped_metric and trigger_metric == mapped_metric:
                score += 0.3

            # 推荐操作匹配
            recommended = rca.recommended_actions
            pb_actions = [a.get("type", "") for a in playbook.get("actions", [])]
            if recommended and pb_actions:
                action_matches = sum(
                    1 for ra in recommended if any(ra.startswith(pa) for pa in pb_actions)
                )
                score += 0.2 * (action_matches / max(len(recommended), len(pb_actions)))

            if score > best_score:
                best_score = score
                best_match = playbook

        # 默认选择第一个
        if best_match is None:
            best_match = self._playbooks[0]

        logger.info(
            "Playbook matched",
            playbook_id=best_match["id"],
            playbook_name=best_match["name"],
            match_score=round(best_score, 4),
        )

        return best_match

    # ==================== 爆炸半径评估 ====================

    def _evaluate_blast_radius(self, rca: RCAEvent) -> BlastRadiusResult:
        """
        评估爆炸半径

        计算影响的服务数量 / 总服务数量。
        """
        total_services = len(self._service_topology)
        affected_services = rca.evidence.get("affected_services", [])
        critical_services = rca.evidence.get("critical_services_affected", [])

        affected_count = len(affected_services) if affected_services else 1

        ratio = affected_count / total_services if total_services > 0 else 0.0

        # 考虑关键服务影响
        critical_ratio = len(critical_services) / total_services if total_services > 0 else 0
        adjusted_ratio = max(ratio, critical_ratio * 1.5)

        # 确定风险级别
        if adjusted_ratio < 0.05:
            risk_level = "low"
        elif adjusted_ratio < 0.20:
            risk_level = "medium"
        elif adjusted_ratio < 0.50:
            risk_level = "high"
        else:
            risk_level = "critical"

        return BlastRadiusResult(
            affected_service_count=affected_count,
            total_service_count=total_services,
            blast_radius_ratio=round(min(adjusted_ratio, 1.0), 4),
            critical_services_affected=critical_services,
            risk_level=risk_level,
        )

    # ==================== 自愈级别判定 ====================

    def _determine_heal_level(self, blast_radius: BlastRadiusResult) -> HealLevel:
        """
        分级自愈策略

        - L0: 低风险(<5%影响)，自动执行
        - L1: 中风险(5-20%)，需要 oncall 确认
        - L2: 高风险(>20%)，需要 TL 审批
        """
        ratio = blast_radius.blast_radius_ratio

        if ratio < 0.05:
            return HealLevel.L0_AUTO
        elif ratio < 0.20:
            return HealLevel.L1_CONFIRM
        else:
            return HealLevel.L2_APPROVE

    # ==================== 操作构建 ====================

    def _build_actions(
        self,
        playbook: dict[str, Any],
        rca: RCAEvent,
    ) -> list[dict[str, Any]]:
        """构建自愈操作列表"""
        actions = playbook.get("actions", [])
        service = rca.evidence.get("affected_services", ["unknown"])[0] \
            if rca.evidence.get("affected_services") else "unknown"

        # 填充模板变量
        resolved_actions: list[dict[str, Any]] = []
        for action in actions:
            resolved = dict(action)
            # 替换 {service} 占位符
            for key, val in resolved.items():
                if isinstance(val, str):
                    resolved[key] = val.replace("{service}", service)
            resolved_actions.append(resolved)

        return resolved_actions

    # ==================== Dry-run ====================

    def _dry_run_action(
        self,
        action: dict[str, Any],
        rca: RCAEvent,
        blast_radius: BlastRadiusResult,
    ) -> DryRunResult:
        """
        Dry-run 模拟执行

        验证命令语法和权限，不实际执行。
        """
        action_type = action.get("type", "")
        warnings: list[str] = []
        errors: list[str] = []
        commands: list[str] = []

        # 语法验证
        valid_action_types = {
            "scale_up", "scale_down", "restart", "rollback_deployment",
            "cleanup_logs", "expand_volume", "circuit_breaker", "rate_limit",
            "alert_oncall", "redeploy", "disable_circuit_breaker",
        }

        if action_type not in valid_action_types:
            errors.append(f"Unknown action type: {action_type}")
            return DryRunResult(
                executable=False,
                syntax_valid=False,
                permission_check=False,
                errors=errors,
            )

        # 构建命令预览
        service = rca.evidence.get("affected_services", ["unknown"])[0] \
            if rca.evidence.get("affected_services") else "unknown"

        command_builders: dict[str, callable] = {
            "scale_up": lambda a: f"kubectl scale deployment/{service} --replicas={a.get('replicas', '+1')}",
            "scale_down": lambda a: f"kubectl scale deployment/{service} --replicas={a.get('replicas', '-1')}",
            "restart": lambda a: f"kubectl rollout restart deployment/{service}",
            "rollback_deployment": lambda a: f"kubectl rollout undo deployment/{service}",
            "cleanup_logs": lambda a: f"find /var/log -name '*.log' -mtime +{a.get('retention_days', 7)} -delete",
            "circuit_breaker": lambda a: f"enable circuit_breaker threshold={a.get('threshold', 0.5)}",
            "rate_limit": lambda a: f"configure rate_limit rps={a.get('rps', 100)}",
            "alert_oncall": lambda a: f"send_alert severity={a.get('severity', 'critical')}",
            "redeploy": lambda a: f"kubectl rollout restart deployment/{service}",
            "disable_circuit_breaker": lambda a: "disable circuit_breaker",
        }

        if action_type in command_builders:
            try:
                cmd = command_builders[action_type](action)
                commands.append(cmd)
            except Exception as e:
                errors.append(f"Failed to build command: {e}")

        # 权限检查
        high_risk_actions = {"rollback_deployment", "cleanup_logs", "redeploy"}
        if action_type in high_risk_actions:
            if blast_radius.risk_level in ("high", "critical"):
                warnings.append(
                    f"High-risk action '{action_type}' on high-blast-radius scenario"
                )

        # 检查是否在 L0 允许范围内
        if blast_radius.blast_radius_ratio > 0.05:
            auto_actions = {"scale_up", "restart", "circuit_breaker", "rate_limit", "alert_oncall"}
            if action_type not in auto_actions:
                warnings.append(
                    f"Action '{action_type}' requires approval for blast_radius > 5%"
                )

        executable = len(errors) == 0

        # 预计执行时间
        estimated_duration = {
            "scale_up": 30,
            "scale_down": 30,
            "restart": 60,
            "rollback_deployment": 45,
            "cleanup_logs": 120,
            "expand_volume": 300,
            "circuit_breaker": 10,
            "rate_limit": 15,
            "alert_oncall": 5,
            "redeploy": 120,
            "disable_circuit_breaker": 10,
        }.get(action_type, 30)

        return DryRunResult(
            executable=executable,
            syntax_valid=len(errors) == 0,
            permission_check=len(errors) == 0,
            estimated_duration_seconds=estimated_duration,
            warnings=warnings,
            errors=errors,
            command_preview=commands,
        )

    # ==================== 回滚计划 ====================

    def _build_rollback_plan(
        self,
        playbook: dict[str, Any] | None,
        actions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """构建回滚计划"""
        rollback_plan: list[dict[str, Any]] = []

        # 使用 Playbook 中定义的 rollback
        if playbook and playbook.get("rollback"):
            rollback_plan.append(playbook["rollback"])
            return rollback_plan

        # 根据操作自动生成回滚
        action_rollback_map: dict[str, dict[str, Any]] = {
            "scale_up": {"type": "scale_down", "description": "Revert scaling"},
            "scale_down": {"type": "scale_up", "description": "Restore original scale"},
            "restart": {"type": "none", "description": "Restart cannot be rolled back automatically"},
            "rollback_deployment": {"type": "redeploy", "description": "Redeploy current version"},
            "circuit_breaker": {"type": "disable_circuit_breaker", "description": "Disable circuit breaker"},
            "rate_limit": {"type": "disable_rate_limit", "description": "Remove rate limit"},
        }

        for action in actions:
            action_type = action.get("type", "")
            if action_type in action_rollback_map:
                rollback = dict(action_rollback_map[action_type])
                rollback["original_action"] = action_type
                rollback_plan.append(rollback)

        if not rollback_plan:
            rollback_plan.append({
                "type": "manual",
                "description": "No automatic rollback available, manual intervention required",
            })

        return rollback_plan

    # ==================== 熔断器接口 ====================

    def get_circuit_breaker_status(self) -> dict[str, Any]:
        """获取熔断器状态"""
        return {
            "state": self._circuit_breaker.state.value,
            "failure_count": self._circuit_breaker.failure_count,
            "success_count": self._circuit_breaker.success_count,
            "last_failure_time": self._circuit_breaker.last_failure_time.isoformat()
            if self._circuit_breaker.last_failure_time else None,
            "can_execute": self._circuit_breaker.can_execute(),
        }

    def reset_circuit_breaker(self) -> None:
        """重置熔断器"""
        self._circuit_breaker = CircuitBreaker()
        logger.info("Circuit breaker reset")
