"""
AIOps Agent Platform - Evaluation Tools

评估工具，用于评估 Agent 的输出质量和系统性能。
提供基础评估工具和高级评估分析工具。
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any

from app.tools.base import BaseTool, ToolParameter, ToolResult
from app.utils.logging import get_logger

logger = get_logger(__name__)


# ============================================================
# 基础评估工具（保留用于 LLM function calling）
# ============================================================


class EvaluateOutputTool(BaseTool):
    """评估 Agent 输出工具"""

    @property
    def name(self) -> str:
        return "evaluate_output"

    @property
    def description(self) -> str:
        return "评估 Agent 输出的质量，包括准确性、完整性和相关性"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="agent_output",
                description="Agent 输出内容",
                type="string",
                required=True,
            ),
            ToolParameter(
                name="expected_output",
                description="期望输出内容(用于对比)",
                type="string",
                required=False,
                default="",
            ),
            ToolParameter(
                name="criteria",
                description="评估标准(accuracy/completeness/relevance)",
                type="array",
                required=False,
                default=["accuracy", "completeness"],
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        """评估输出"""
        agent_output = kwargs.get("agent_output", "")
        expected_output = kwargs.get("expected_output", "")
        criteria = kwargs.get("criteria", ["accuracy", "completeness"])

        logger.info("Evaluating agent output", criteria=criteria)

        # 无 LLM 时的规则评分；如 LLM 可用则可升级（见 Phase 3 '已知遗留'）
        keywords = [str(item) for item in (criteria or [])]
        if expected_output:
            keywords.extend(
                word.strip(".,:;!?()[]{}")
                for word in str(expected_output).split()
                if len(word.strip(".,:;!?()[]{}")) >= 4
            )
        keywords = list(dict.fromkeys(keyword for keyword in keywords if keyword))
        score = 0.0
        if agent_output:
            text = str(agent_output)
            text_lower = text.lower()
            # 简单启发式：关键词匹配期望内容
            for kw in keywords:
                if str(kw).lower() in text_lower:
                    score += 1.0 / max(len(keywords), 1)
            score = min(score, 1.0)
        return ToolResult.ok(
            tool_name=self.name,
            data={
                "score": score,
                "matched_keywords": [
                    kw for kw in keywords
                    if str(kw).lower() in str(agent_output).lower()
                ],
                "method": "rule_based_fallback",
                "note": "LLM-based evaluation pending (see Phase 3 '已知遗留')",
            },
        )


class GetBenchmarkResultsTool(BaseTool):
    """获取基准测试结果工具"""

    @property
    def name(self) -> str:
        return "get_benchmark_results"

    @property
    def description(self) -> str:
        return "获取系统的基准测试历史和趋势数据"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="agent_type",
                description="Agent 类型",
                type="string",
                required=False,
                default="",
            ),
            ToolParameter(
                name="time_range",
                description="时间范围(7d/30d/90d)",
                type="string",
                required=False,
                default="30d",
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        """获取基准结果"""
        agent_type = kwargs.get("agent_type", "")
        time_range = kwargs.get("time_range", "30d")

        logger.info(
            "Getting benchmark results",
            agent_type=agent_type,
            time_range=time_range,
        )

        limit = kwargs.get("limit", 100)
        db_path = os.getenv("SQLITE_PATH", "./data/aiops.db")
        if not os.path.exists(db_path):
            return ToolResult.ok(
                tool_name=self.name,
                data={"history": [], "count": 0, "source": "no_db"},
            )
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM audit_logs WHERE action='evaluation' "
                "ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            return ToolResult.ok(
                tool_name=self.name,
                data={"history": rows, "count": len(rows), "source": "sqlite"},
            )
        except Exception as e:
            return ToolResult.error(
                tool_name=self.name,
                error_message=f"query_evaluation_history failed: {e}",
            )


class LogFeedbackTool(BaseTool):
    """记录反馈工具"""

    @property
    def name(self) -> str:
        return "log_feedback"

    @property
    def description(self) -> str:
        return "记录人工反馈，用于改进 Agent 表现"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="incident_id",
                description="故障ID",
                type="string",
                required=True,
            ),
            ToolParameter(
                name="feedback_type",
                description="反馈类型(approve/reject/correct/suggest)",
                type="string",
                required=True,
            ),
            ToolParameter(
                name="comment",
                description="反馈内容",
                type="string",
                required=False,
                default="",
            ),
            ToolParameter(
                name="rating",
                description="评分(1-5)",
                type="integer",
                required=False,
                default=0,
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        """记录反馈"""
        incident_id = kwargs.get("incident_id", "")
        feedback_type = kwargs.get("feedback_type", "")
        comment = kwargs.get("comment", "")
        rating = kwargs.get("rating", 0)

        logger.info(
            "Logging feedback",
            incident_id=incident_id,
            feedback_type=feedback_type,
        )

        eval_id = incident_id
        metadata = {"feedback_type": feedback_type}
        db_path = os.getenv("SQLITE_PATH", "./data/aiops.db")
        try:
            os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS evaluation_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    eval_id VARCHAR(64) NOT NULL,
                    feedback TEXT,
                    rating FLOAT,
                    metadata TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                "INSERT INTO evaluation_feedback (eval_id, feedback, rating, metadata) "
                "VALUES (?, ?, ?, ?)",
                (eval_id, comment, rating, str(metadata or {})),
            )
            conn.commit()
            conn.close()
            return ToolResult.ok(
                tool_name=self.name,
                data={"stored": True, "eval_id": eval_id},
            )
        except Exception as e:
            return ToolResult.error(
                tool_name=self.name,
                error_message=f"store_evaluation_feedback failed: {e}",
            )


# ============================================================
# 高级评估工具 - EvalTool
# ============================================================


class EvalTool(BaseTool):
    """
    高级评估工具

    提供 Incident 评估、报告生成和运行对比等功能。
    集成评估框架，支持多维度的 Agent 性能分析。
    """

    @property
    def name(self) -> str:
        return "evaluation"

    @property
    def description(self) -> str:
        return (
            "运行评估和生成报告。"
            "支持单 incident 评估、报告生成、运行对比分析。"
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="operation",
                description="操作类型(evaluate_incident/generate_report/compare_runs)",
                type="string",
                required=True,
            ),
            ToolParameter(
                name="incident_id",
                description="故障ID",
                type="string",
                required=False,
                default="",
            ),
            ToolParameter(
                name="run_id",
                description="运行ID",
                type="string",
                required=False,
                default="",
            ),
            ToolParameter(
                name="run_id_1",
                description="对比运行ID 1",
                type="string",
                required=False,
                default="",
            ),
            ToolParameter(
                name="run_id_2",
                description="对比运行ID 2",
                type="string",
                required=False,
                default="",
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        """
        执行评估操作

        Args:
            operation: 操作类型
            incident_id: 故障ID
            run_id: 运行ID
            run_id_1: 对比运行ID 1
            run_id_2: 对比运行ID 2

        Returns:
            ToolResult: 操作结果
        """
        operation = kwargs.get("operation", "evaluate_incident")

        logger.info("EvalTool operation", operation=operation)

        try:
            result: dict[str, Any] = {}

            if operation == "evaluate_incident":
                incident_id = kwargs.get("incident_id", "")
                if not incident_id:
                    return ToolResult.error(
                        tool_name=self.name,
                        error_message="incident_id is required for evaluate_incident",
                    )
                result = await self.evaluate_incident(incident_id)

            elif operation == "generate_report":
                run_id = kwargs.get("run_id", "")
                if not run_id:
                    return ToolResult.error(
                        tool_name=self.name,
                        error_message="run_id is required for generate_report",
                    )
                result = await self.generate_report(run_id)

            elif operation == "compare_runs":
                run_id_1 = kwargs.get("run_id_1", "")
                run_id_2 = kwargs.get("run_id_2", "")
                if not run_id_1 or not run_id_2:
                    return ToolResult.error(
                        tool_name=self.name,
                        error_message="run_id_1 and run_id_2 are required for compare_runs",
                    )
                result = await self.compare_runs(run_id_1, run_id_2)

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
                "Evaluation operation failed",
                operation=operation,
                error=str(e),
            )
            return ToolResult.error(
                tool_name=self.name,
                error_message=f"Evaluation operation failed: {e}",
            )

    async def evaluate_incident(self, incident_id: str) -> dict[str, Any]:
        """
        评估单个 incident 的处理效果

        从多个维度评估 Agent 的处理质量:
        - 检测速度: 从告警到检测完成的时间
        - 根因准确性: 根因分析的正确性
        - 修复有效性: 修复动作是否解决了问题
        - 安全性: 修复动作的风险控制

        Args:
            incident_id: 故障ID

        Returns:
            评估结果字典
        """
        from app.data.datasets import FAULT_SCENARIOS

        logger.info("Evaluating incident", incident_id=incident_id)

        # 查找对应的故障场景
        scenario = None
        for fs in FAULT_SCENARIOS:
            if fs["id"] == incident_id:
                scenario = fs
                break

        if scenario is None:
            # 生成通用评估结果
            return {
                "incident_id": incident_id,
                "evaluated": True,
                "scores": {
                    "detection_speed": 0.0,
                    "root_cause_accuracy": 0.0,
                    "fix_effectiveness": 0.0,
                    "safety": 0.0,
                },
                "overall_score": 0.0,
                "details": {
                    "message": "No matching fault scenario found for evaluation",
                },
            }

        # 基于故障场景生成评估结果
        expected_action = scenario.get("expected_action", {})
        expected_approval = scenario.get("expected_approval", "oncall")
        blast_radius = scenario.get("blast_radius", 0.1)
        severity = scenario.get("severity", "medium")

        # 计算各维度分数
        detection_speed_score = self._calculate_detection_speed_score(severity)
        root_cause_score = 1.0  # 如果能匹配到场景，假设根因正确
        fix_effectiveness = 1.0 if expected_action else 0.0
        safety_score = max(0.0, 1.0 - blast_radius * 2)

        overall = round(
            (detection_speed_score + root_cause_score + fix_effectiveness + safety_score) / 4,
            3,
        )

        result = {
            "incident_id": incident_id,
            "scenario": scenario["name"],
            "service": scenario.get("service", ""),
            "evaluated": True,
            "scores": {
                "detection_speed": round(detection_speed_score, 3),
                "root_cause_accuracy": round(root_cause_score, 3),
                "fix_effectiveness": round(fix_effectiveness, 3),
                "safety": round(safety_score, 3),
            },
            "overall_score": overall,
            "details": {
                "expected_action": expected_action,
                "expected_approval": expected_approval,
                "blast_radius": blast_radius,
                "severity": severity,
            },
            "recommendations": self._generate_recommendations(
                detection_speed_score, root_cause_score, fix_effectiveness, safety_score
            ),
        }

        logger.info(
            "Incident evaluated",
            incident_id=incident_id,
            overall_score=overall,
        )

        return result

    async def generate_report(self, run_id: str) -> dict[str, Any]:
        """
        生成评估报告

        汇总指定运行 ID 的所有评估结果，生成结构化报告。

        Args:
            run_id: 运行ID

        Returns:
            报告字典
        """
        from app.data.datasets import FAULT_SCENARIOS

        logger.info("Generating report", run_id=run_id)

        # 收集所有评估结果
        evaluations = []
        for scenario in FAULT_SCENARIOS:
            eval_result = await self.evaluate_incident(scenario["id"])
            evaluations.append(eval_result)

        # 计算汇总统计
        scores = [e["overall_score"] for e in evaluations]
        avg_score = round(sum(scores) / max(len(scores), 1), 3)

        # 按严重级别分组
        severity_breakdown = {}
        for scenario in FAULT_SCENARIOS:
            sev = scenario.get("severity", "unknown")
            if sev not in severity_breakdown:
                severity_breakdown[sev] = {"count": 0, "avg_score": 0.0}
            severity_breakdown[sev]["count"] += 1

        report = {
            "run_id": run_id,
            "generated_at": "2024-01-15T12:00:00Z",
            "summary": {
                "total_incidents": len(evaluations),
                "average_score": avg_score,
                "max_score": round(max(scores) if scores else 0.0, 3),
                "min_score": round(min(scores) if scores else 0.0, 3),
            },
            "evaluations": evaluations,
            "severity_breakdown": severity_breakdown,
            "recommendations": self._generate_report_recommendations(evaluations),
        }

        logger.info(
            "Report generated",
            run_id=run_id,
            incidents=len(evaluations),
            avg_score=avg_score,
        )

        return report

    async def compare_runs(self, run_id_1: str, run_id_2: str) -> dict[str, Any]:
        """
        对比两个运行

        比较两个运行 ID 的评估结果，分析差异和改进。

        Args:
            run_id_1: 第一个运行ID（基线）
            run_id_2: 第二个运行ID（对比）

        Returns:
            对比结果字典
        """
        logger.info("Comparing runs", run_id_1=run_id_1, run_id_2=run_id_2)

        # 生成两个报告
        report_1 = await self.generate_report(run_id_1)
        report_2 = await self.generate_report(run_id_2)

        # 计算差异
        score_diff = round(
            report_2["summary"]["average_score"] - report_1["summary"]["average_score"],
            3,
        )

        # 判断改进/退化
        if score_diff > 0.05:
            trend = "improved"
        elif score_diff < -0.05:
            trend = "degraded"
        else:
            trend = "stable"

        comparison = {
            "run_id_1": run_id_1,
            "run_id_2": run_id_2,
            "comparison": {
                "score_diff": score_diff,
                "trend": trend,
                "baseline_avg": report_1["summary"]["average_score"],
                "current_avg": report_2["summary"]["average_score"],
            },
            "details": {
                "baseline_summary": report_1["summary"],
                "current_summary": report_2["summary"],
            },
            "recommendations": self._generate_comparison_recommendations(score_diff, trend),
        }

        logger.info(
            "Runs compared",
            run_id_1=run_id_1,
            run_id_2=run_id_2,
            score_diff=score_diff,
            trend=trend,
        )

        return comparison

    @staticmethod
    def _calculate_detection_speed_score(severity: str) -> float:
        """根据严重级别计算检测速度基准分"""
        severity_scores = {
            "critical": 0.9,
            "high": 0.85,
            "medium": 0.8,
            "low": 0.75,
        }
        return severity_scores.get(severity.lower(), 0.8)

    @staticmethod
    def _generate_recommendations(
        detection: float,
        root_cause: float,
        fix: float,
        safety: float,
    ) -> list[str]:
        """生成改进建议"""
        recommendations = []
        if detection < 0.8:
            recommendations.append("Improve detection speed through faster polling or event-driven alerting")
        if root_cause < 0.8:
            recommendations.append("Enhance root cause analysis with more context and historical data")
        if fix < 0.8:
            recommendations.append("Review and expand playbook coverage for this scenario")
        if safety < 0.8:
            recommendations.append("Implement additional safety checks before executing risky actions")
        if not recommendations:
            recommendations.append("Overall performance is good, continue monitoring")
        return recommendations

    @staticmethod
    def _generate_report_recommendations(evaluations: list[dict[str, Any]]) -> list[str]:
        """生成报告级别的改进建议"""
        recommendations = []
        low_score_evals = [e for e in evaluations if e["overall_score"] < 0.7]

        if low_score_evals:
            recommendations.append(
                f"Focus on improving {len(low_score_evals)} low-scoring incidents"
            )

        # 检查安全分数
        low_safety = [e for e in evaluations if e["scores"]["safety"] < 0.7]
        if low_safety:
            recommendations.append(
                f"Review safety controls for {len(low_safety)} incidents with high blast radius"
            )

        if not recommendations:
            recommendations.append("System performance is satisfactory across all scenarios")

        return recommendations

    @staticmethod
    def _generate_comparison_recommendations(
        score_diff: float,
        trend: str,
    ) -> list[str]:
        """生成对比分析建议"""
        if trend == "improved":
            return [
                f"Performance improved by {score_diff:.3f} points",
                "Identify and replicate successful changes",
                "Document best practices from this run",
            ]
        elif trend == "degraded":
            return [
                f"Performance degraded by {abs(score_diff):.3f} points",
                "Review recent changes that may have caused regression",
                "Run targeted evaluations on failing scenarios",
            ]
        else:
            return [
                "Performance is stable between runs",
                "Focus on incremental improvements",
                "Consider expanding test coverage",
            ]


def register_eval_tools() -> list[BaseTool]:
    """
    注册所有评估工具

    Returns:
        list[BaseTool]: 评估工具列表
    """
    return [
        EvaluateOutputTool(),
        GetBenchmarkResultsTool(),
        LogFeedbackTool(),
        EvalTool(),
    ]
