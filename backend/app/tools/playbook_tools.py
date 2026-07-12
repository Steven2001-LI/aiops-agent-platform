"""
AIOps Agent Platform - Playbook Tools

Playbook 工具，用于执行运维手册中定义的标准操作流程。
提供基础 Playbook 工具和高级 Playbook 匹配执行工具。
"""

from __future__ import annotations

from typing import Any

from app.data.playbooks import PLAYBOOKS
try:
    from app.data.public_playbooks import PUBLIC_PLAYBOOKS
except ImportError:
    PUBLIC_PLAYBOOKS = []

from app.tools.base import BaseTool, ToolParameter, ToolResult
from app.utils.logging import get_logger

logger = get_logger(__name__)


# ============================================================
# 基础 Playbook 工具（保留用于 LLM function calling）
# ============================================================


class GetPlaybookTool(BaseTool):
    """获取 Playbook 工具"""

    @property
    def name(self) -> str:
        return "get_playbook"

    @property
    def description(self) -> str:
        return "根据故障类型获取对应的标准处理手册(Playbook)"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="fault_type",
                description="故障类型",
                type="string",
                required=True,
            ),
            ToolParameter(
                name="service",
                description="服务名称",
                type="string",
                required=False,
                default="",
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        """获取 Playbook"""
        fault_type = kwargs.get("fault_type", "")
        service = kwargs.get("service", "")

        logger.info("Getting playbook", fault_type=fault_type, service=service)

        all_pbs = list(PLAYBOOKS) + list(PUBLIC_PLAYBOOKS)
        # 按 fault_type 过滤（匹配 category 或 applicable_root_causes）
        matched = [
            pb for pb in all_pbs
            if fault_type and (fault_type in str(pb.get("category", ""))
                               or fault_type in str(pb.get("applicable_root_causes", [])))
        ]
        if not matched and all_pbs:
            matched = all_pbs[:1]  # fallback: 返回第一个
        pb = matched[0] if matched else {}
        return ToolResult.ok(
            tool_name=self.name,
            data={
                "playbook_id": pb.get("id", ""),
                "name": pb.get("name", ""),
                "description": pb.get("description", ""),
                "steps": pb.get("actions", pb.get("steps", [])),
                "fault_type": fault_type,
                "service": service,
                "source": "playbooks_db",
            },
        )


class ExecutePlaybookStepTool(BaseTool):
    """执行 Playbook 步骤工具"""

    @property
    def name(self) -> str:
        return "execute_playbook_step"

    @property
    def description(self) -> str:
        return "执行 Playbook 中的单个步骤，支持命令执行和 API 调用"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="playbook_id",
                description="Playbook ID",
                type="string",
                required=True,
            ),
            ToolParameter(
                name="step_index",
                description="步骤索引",
                type="integer",
                required=True,
            ),
            ToolParameter(
                name="parameters",
                description="步骤参数",
                type="object",
                required=False,
                default={},
            ),
            ToolParameter(
                name="dry_run",
                description="是否仅模拟执行",
                type="boolean",
                required=False,
                default=True,
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        """执行 Playbook 步骤"""
        playbook_id = kwargs.get("playbook_id", "")
        step_index = kwargs.get("step_index", 0)
        parameters = kwargs.get("parameters", {})
        dry_run = kwargs.get("dry_run", True)

        logger.info(
            "Executing playbook step",
            playbook_id=playbook_id,
            step_index=step_index,
            dry_run=dry_run,
        )

        pb = None
        for p in list(PLAYBOOKS) + list(PUBLIC_PLAYBOOKS):
            if p.get("id") == playbook_id:
                pb = p
                break
        if not pb:
            return ToolResult.error(
                tool_name=self.name,
                error_message=f"Playbook {playbook_id} not found",
            )
        steps = pb.get("actions", pb.get("steps", []))
        if step_index >= len(steps):
            return ToolResult.error(
                tool_name=self.name,
                error_message=f"Step index {step_index} out of range",
            )
        step = steps[step_index]
        # dry_run=True 时仅生成命令不执行
        if dry_run:
            return ToolResult.ok(
                tool_name=self.name,
                data={
                    "playbook_id": playbook_id,
                    "step_index": step_index,
                    "step": step,
                    "dry_run": True,
                    "executed": False,
                    "simulated_command": step.get("type", ""),
                },
            )
        # 真执行：当前留空，标注"需对接 K8s/Ansible"
        return ToolResult.ok(
            tool_name=self.name,
            data={
                "playbook_id": playbook_id,
                "step_index": step_index,
                "step": step,
                "dry_run": False,
                "executed": False,
                "note": "Real execution requires K8s/Ansible integration (see Phase 3 spec '已知遗留')",
            },
        )


class ListPlaybooksTool(BaseTool):
    """列出 Playbook 工具"""

    @property
    def name(self) -> str:
        return "list_playbooks"

    @property
    def description(self) -> str:
        return "列出所有可用的运维手册(Playbook)"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="service",
                description="按服务过滤",
                type="string",
                required=False,
                default="",
            ),
            ToolParameter(
                name="category",
                description="按类别过滤",
                type="string",
                required=False,
                default="",
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        """列出 Playbook"""
        service = kwargs.get("service", "")
        category = kwargs.get("category", "")

        logger.info("Listing playbooks", service=service, category=category)

        all_pbs = list(PLAYBOOKS) + list(PUBLIC_PLAYBOOKS)
        return ToolResult.ok(
            tool_name=self.name,
            data={
                "playbooks": [
                    {"id": pb.get("id"), "name": pb.get("name"), "category": pb.get("category", "")}
                    for pb in all_pbs
                ],
                "count": len(all_pbs),
                "source": "playbooks_db",
            },
        )


# ============================================================
# 高级 Playbook 工具 - PlaybookTool
# ============================================================


class PlaybookTool(BaseTool):
    """
    高级 Playbook 匹配和执行工具

    提供 Playbook 的智能匹配、Dry-Run 模拟执行、
    动作安全性验证和回滚计划获取等功能。
    """

    @property
    def name(self) -> str:
        return "playbook"

    @property
    def description(self) -> str:
        return (
            "匹配故障修复剧本并执行dry-run。"
            "支持智能匹配、安全验证、回滚计划获取。"
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="operation",
                description="操作类型(match/dry_run/validate_action/get_rollback_plan)",
                type="string",
                required=True,
            ),
            ToolParameter(
                name="alert_name",
                description="告警名称（用于匹配）",
                type="string",
                required=False,
                default="",
            ),
            ToolParameter(
                name="service",
                description="服务名称",
                type="string",
                required=False,
                default="",
            ),
            ToolParameter(
                name="severity",
                description="严重级别(low/medium/high/critical)",
                type="string",
                required=False,
                default="medium",
            ),
            ToolParameter(
                name="playbook_id",
                description="Playbook ID",
                type="string",
                required=False,
                default="",
            ),
            ToolParameter(
                name="action",
                description="动作描述（用于验证）",
                type="object",
                required=False,
                default={},
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        """
        执行 Playbook 操作

        Args:
            operation: 操作类型
            alert_name: 告警名称
            service: 服务名称
            severity: 严重级别
            playbook_id: Playbook ID
            action: 动作描述

        Returns:
            ToolResult: 操作结果
        """
        operation = kwargs.get("operation", "match")

        logger.info("Playbook operation", operation=operation)

        try:
            result: dict[str, Any] = {}

            if operation == "match":
                matched = await self.match_playbook(
                    alert_name=kwargs.get("alert_name", ""),
                    service=kwargs.get("service", ""),
                    severity=kwargs.get("severity", "medium"),
                )
                result = {
                    "matched": matched is not None,
                    "playbook": matched,
                }
            elif operation == "dry_run":
                result = await self.dry_run(
                    playbook_id=kwargs.get("playbook_id", ""),
                    service=kwargs.get("service", ""),
                )
            elif operation == "validate_action":
                action = kwargs.get("action", {})
                if isinstance(action, dict):
                    result = await self.validate_action(
                        action=action,
                        service=kwargs.get("service", ""),
                    )
                else:
                    result = {"valid": False, "reason": "action must be a dict"}
            elif operation == "get_rollback_plan":
                plan = await self.get_rollback_plan(
                    playbook_id=kwargs.get("playbook_id", ""),
                )
                result = {"rollback_plan": plan}
            else:
                return ToolResult.error(
                    tool_name=self.name,
                    error_message=f"Unknown operation: {operation}",
                )

            return ToolResult.ok(
                tool_name=self.name,
                data=result,
            )

        except Exception as e:
            logger.error(
                "Playbook operation failed",
                operation=operation,
                error=str(e),
            )
            return ToolResult.error(
                tool_name=self.name,
                error_message=f"Playbook operation failed: {e}",
            )

    async def match_playbook(
        self,
        alert_name: str,
        service: str,
        severity: str,
    ) -> dict[str, Any] | None:
        """
        根据告警匹配合适的 Playbook

        使用告警名称、服务名称和严重级别进行多维度匹配。

        Args:
            alert_name: 告警名称
            service: 服务名称
            severity: 严重级别

        Returns:
            匹配的 Playbook 字典，未匹配返回 None
        """
        from app.data.playbooks import PLAYBOOKS
        from app.data.datasets import FAULT_SCENARIOS

        alert_lower = alert_name.lower()
        best_match: dict[str, Any] | None = None
        best_score = 0.0

        # 1. 从预定义 Playbook 中匹配
        for pb in PLAYBOOKS:
            score = 0.0
            pb_name = pb.get("name", "").lower()
            trigger = pb.get("trigger", {})
            trigger_metric = trigger.get("metric", "").lower()

            # 名称关键词匹配
            alert_words = set(alert_lower.split("_") + alert_lower.split())
            pb_words = set(pb_name.split())
            trigger_words = set(trigger_metric.split("_"))

            name_overlap = len(alert_words & pb_words)
            trigger_overlap = len(alert_words & trigger_words)
            score += name_overlap * 0.3 + trigger_overlap * 0.5

            # 严重级别权重
            severity_weights = {"critical": 1.0, "high": 0.8, "medium": 0.5, "low": 0.3}
            score += severity_weights.get(severity.lower(), 0.5) * 0.2

            if score > best_score:
                best_score = score
                best_match = {
                    "playbook_id": pb["id"],
                    "name": pb["name"],
                    "actions": pb.get("actions", []),
                    "rollback": pb.get("rollback"),
                    "max_blast_radius": pb.get("max_blast_radius", 0.1),
                    "match_score": round(score, 3),
                    "source": "playbook_db",
                }

        # 2. 从故障场景中补充匹配
        for scenario in FAULT_SCENARIOS:
            scenario_service = scenario.get("service", "")
            scenario_severity = scenario.get("severity", "")

            if scenario_service == service:
                score = 0.5
                if scenario_severity.lower() == severity.lower():
                    score += 0.3

                if score > best_score:
                    best_score = score
                    best_match = {
                        "playbook_id": scenario["id"],
                        "name": scenario["name"],
                        "description": scenario["description"],
                        "expected_action": scenario.get("expected_action", {}),
                        "root_cause": scenario.get("root_cause", ""),
                        "blast_radius": scenario.get("blast_radius", 0.1),
                        "match_score": round(score, 3),
                        "source": "fault_scenario",
                    }

        if best_match:
            logger.info(
                "Playbook matched",
                playbook_id=best_match.get("playbook_id"),
                score=best_match.get("match_score"),
                alert=alert_name,
                service=service,
            )
        else:
            logger.warning(
                "No playbook matched",
                alert=alert_name,
                service=service,
                severity=severity,
            )

        return best_match

    async def dry_run(
        self,
        playbook_id: str,
        service: str,
    ) -> dict[str, Any]:
        """
        模拟执行 Playbook

        验证 Playbook 步骤的可执行性，模拟每一步的执行结果，
        估算整体影响范围。

        Args:
            playbook_id: Playbook ID
            service: 目标服务

        Returns:
            Dry-Run 结果，包含模拟步骤和预计影响
        """
        from app.data.playbooks import PLAYBOOKS

        # 查找 Playbook
        playbook = None
        for pb in PLAYBOOKS:
            if pb["id"] == playbook_id:
                playbook = pb
                break

        if playbook is None:
            # 尝试从故障场景查找
            from app.data.datasets import FAULT_SCENARIOS
            for scenario in FAULT_SCENARIOS:
                if scenario["id"] == playbook_id:
                    action = scenario.get("expected_action", {})
                    playbook = {
                        "id": scenario["id"],
                        "name": scenario["name"],
                        "actions": [action] if action else [],
                        "max_blast_radius": scenario.get("blast_radius", 0.1),
                    }
                    break

        if playbook is None:
            return {
                "success": False,
                "error": f"Playbook not found: {playbook_id}",
                "simulated_steps": [],
                "estimated_impact": {},
            }

        # 模拟执行每一步
        simulated_steps = []
        total_risk = 0.0

        for i, action in enumerate(playbook.get("actions", [])):
            action_type = action.get("type", "unknown")
            step_result = {
                "step_index": i,
                "action": action,
                "simulated": True,
                "executable": True,
                "risk_level": "low",
                "estimated_duration_seconds": 30,
            }

            # 评估动作风险
            risk_scores = {
                "restart": 0.3,
                "rollback_deployment": 0.4,
                "scale_up": 0.1,
                "scale_down": 0.2,
                "circuit_breaker": 0.2,
                "alert_oncall": 0.0,
                "cleanup_logs": 0.05,
                "expand_volume": 0.1,
            }
            step_risk = risk_scores.get(action_type, 0.5)
            total_risk += step_risk

            if step_risk > 0.4:
                step_result["risk_level"] = "high"
            elif step_risk > 0.2:
                step_result["risk_level"] = "medium"

            simulated_steps.append(step_result)

        # 计算整体影响
        blast_radius = playbook.get("max_blast_radius", 0.1)
        avg_risk = total_risk / max(len(simulated_steps), 1)

        result = {
            "success": True,
            "playbook_id": playbook_id,
            "service": service,
            "playbook_name": playbook.get("name", ""),
            "simulated_steps": simulated_steps,
            "estimated_impact": {
                "total_steps": len(simulated_steps),
                "average_risk": round(avg_risk, 3),
                "max_blast_radius": blast_radius,
                "estimated_total_duration_seconds": sum(
                    s.get("estimated_duration_seconds", 30)
                    for s in simulated_steps
                ),
                "approval_required": avg_risk > 0.3 or blast_radius > 0.1,
                "risk_summary": self._risk_summary(avg_risk, blast_radius),
            },
        }

        logger.info(
            "Playbook dry-run completed",
            playbook_id=playbook_id,
            service=service,
            steps=len(simulated_steps),
            avg_risk=round(avg_risk, 3),
        )

        return result

    async def validate_action(
        self,
        action: dict[str, Any],
        service: str,
    ) -> dict[str, Any]:
        """
        验证修复动作的安全性

        检查动作是否在白名单中，评估对服务的影响。

        Args:
            action: 动作描述字典
            service: 目标服务

        Returns:
            验证结果，包含是否安全、原因和建议
        """
        action_type = action.get("type", "")
        target = action.get("target", "")

        # 安全动作白名单
        safe_actions = {
            "scale_up", "scale_down", "cleanup_logs", "alert_oncall",
            "rate_limit", "disable_circuit_breaker",
        }
        # 需要审批的动作
        risky_actions = {
            "restart", "rollback_deployment", "rollback_config",
            "circuit_breaker", "expand_volume",
        }
        # 禁止的动作
        forbidden_actions = {
            "delete_data", "drop_table", "remove_namespace",
        }

        validation_result = {
            "action": action,
            "service": service,
            "valid": False,
            "safe": False,
            "approval_required": True,
            "reason": "",
            "suggestions": [],
        }

        # 检查禁止动作
        if action_type in forbidden_actions:
            validation_result["reason"] = f"Action '{action_type}' is forbidden"
            validation_result["suggestions"] = [
                "Contact platform team for manual intervention",
                "Escalate to oncall engineer",
            ]
            logger.warning(
                "Forbidden action detected",
                action_type=action_type,
                service=service,
            )
            return validation_result

        # 检查安全动作
        if action_type in safe_actions:
            validation_result["valid"] = True
            validation_result["safe"] = True
            validation_result["approval_required"] = False
            validation_result["reason"] = f"Action '{action_type}' is in safe whitelist"
            return validation_result

        # 检查风险动作
        if action_type in risky_actions:
            validation_result["valid"] = True
            validation_result["safe"] = False
            validation_result["approval_required"] = True
            validation_result["reason"] = f"Action '{action_type}' requires approval"

            # 提供建议
            if action_type == "restart":
                validation_result["suggestions"] = [
                    "Ensure graceful shutdown before restart",
                    "Check if rolling restart is available",
                    "Verify minimum replica count is maintained",
                ]
            elif action_type == "rollback_deployment":
                validation_result["suggestions"] = [
                    "Verify previous deployment version is healthy",
                    "Check database migration compatibility",
                    "Ensure rollback plan is tested",
                ]

            return validation_result

        # 未知动作
        validation_result["reason"] = f"Unknown action type: '{action_type}'"
        validation_result["suggestions"] = [
            "Review action type spelling",
            "Consult playbook documentation",
        ]
        return validation_result

    async def get_rollback_plan(
        self,
        playbook_id: str,
    ) -> dict[str, Any] | None:
        """
        获取回滚计划

        查找 Playbook 对应的回滚步骤。

        Args:
            playbook_id: Playbook ID

        Returns:
            回滚计划字典，未找到返回 None
        """
        from app.data.playbooks import PLAYBOOKS

        playbook = None
        for pb in PLAYBOOKS:
            if pb["id"] == playbook_id:
                playbook = pb
                break

        if playbook is None:
            return None

        rollback = playbook.get("rollback")
        if rollback is None:
            return {
                "playbook_id": playbook_id,
                "available": False,
                "reason": "No rollback defined for this playbook",
                "rollback_action": None,
            }

        return {
            "playbook_id": playbook_id,
            "available": True,
            "rollback_action": rollback,
            "estimated_rollback_time_seconds": 60,
            "risk_level": "medium",
        }

    @staticmethod
    def _risk_summary(avg_risk: float, blast_radius: float) -> str:
        """生成风险摘要"""
        if avg_risk > 0.4 or blast_radius > 0.2:
            return "HIGH - Approval required before execution"
        elif avg_risk > 0.2 or blast_radius > 0.1:
            return "MEDIUM - Recommend oncall review"
        else:
            return "LOW - Safe for auto-execution"


def register_playbook_tools() -> list[BaseTool]:
    """
    注册所有 Playbook 工具

    Returns:
        list[BaseTool]: Playbook 工具列表
    """
    return [
        GetPlaybookTool(),
        ExecutePlaybookStepTool(),
        ListPlaybooksTool(),
        PlaybookTool(),
    ]
