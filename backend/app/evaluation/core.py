"""
AIOps Agent Platform - Evaluation Framework Core

评估框架核心，协调各类评估的执行和报告生成。
参考 Agent-World, VitaBench, DoVer 三篇论文的评估思路。

功能:
- 统一管理各类评估器
- 统一评估入口: evaluate()
- 评估报告生成: generate_report()
- 历史评估对比: compare_runs()
- 趋势分析: analyze_trends()
- Langfuse 集成: 将评估结果上报到 Langfuse (可选)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, ClassVar

import numpy as np

from app.evaluation.end_to_end import EndToEndEvaluator
from app.evaluation.rag_eval import RAGEvaluator
from app.evaluation.reasoning_eval import ReasoningEvaluator
from app.evaluation.tool_call_eval import ToolCallEvaluator
from app.models.evaluation import (
    BenchmarkReport,
    EvaluationResult,
    EvaluationStatus,
    EvaluationType,
    MetricScore,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)


class EvaluationFramework:
    """
    评估框架核心

    统一管理各类评估的执行和报告生成。
    作为单例模式使用（通过模块级实例）。

    示例:
        framework = get_evaluation_framework()
        result = await framework.evaluate(EvaluationType.END_TO_END)
        report = await framework.generate_report([result])
    """

    _instance: ClassVar[Any | None] = None

    def __new__(cls, *args: Any, **kwargs: Any) -> "EvaluationFramework":
        """单例模式"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return

        self._evaluators: dict[EvaluationType, Any] = {
            EvaluationType.END_TO_END: EndToEndEvaluator(),
            EvaluationType.REASONING: ReasoningEvaluator(),
            EvaluationType.TOOL_CALL: ToolCallEvaluator(),
            EvaluationType.RAG: RAGEvaluator(),
        }
        self._run_history: list[dict[str, Any]] = []
        self._initialized = True

        logger.info("EvaluationFramework initialized")

    async def evaluate(
        self,
        eval_type: EvaluationType,
        target_agent: str = "",
        dataset_name: str = "",
        samples: list[dict[str, Any]] | None = None,
    ) -> EvaluationResult:
        """
        统一评估入口

        执行指定类型的评估。

        Args:
            eval_type: 评估类型
            target_agent: 目标 Agent
            dataset_name: 数据集名称
            samples: 自定义样本

        Returns:
            EvaluationResult: 评估结果
        """
        evaluator = self._evaluators.get(eval_type)
        if evaluator is None:
            logger.error("No evaluator found for type", eval_type=eval_type.value)
            return EvaluationResult(
                evaluation_type=eval_type,
                eval_name=f"{eval_type.value}_error",
                description=f"No evaluator available for {eval_type.value}",
                agent_type=target_agent,
                status=EvaluationStatus.FAILED,
                started_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
                errors=[f"No evaluator found for type: {eval_type.value}"],
            )

        logger.info(
            "Running evaluation",
            eval_type=eval_type.value,
            target_agent=target_agent,
        )

        try:
            result = await evaluator.evaluate(
                target_agent=target_agent,
                dataset_name=dataset_name,
                samples=samples,
            )

            # 记录到历史
            self._record_run(eval_type, result)

            return result

        except Exception as e:
            logger.error(
                "Evaluation failed",
                eval_type=eval_type.value,
                error=str(e),
                exc_info=True,
            )
            return EvaluationResult(
                evaluation_type=eval_type,
                eval_name=f"{eval_type.value}_error",
                description=f"Evaluation failed: {str(e)}",
                agent_type=target_agent,
                status=EvaluationStatus.FAILED,
                started_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
                errors=[str(e)],
            )

    async def evaluate_all(
        self,
        target_agent: str = "",
        samples: dict[str, list[dict[str, Any]]] | None = None,
    ) -> list[EvaluationResult]:
        """
        执行所有类型的评估

        Args:
            target_agent: 目标 Agent
            samples: 各类型的自定义样本 {eval_type: samples}

        Returns:
            list[EvaluationResult]: 所有评估结果
        """
        results = []
        for eval_type in [
            EvaluationType.END_TO_END,
            EvaluationType.REASONING,
            EvaluationType.TOOL_CALL,
            EvaluationType.RAG,
        ]:
            type_samples = samples.get(eval_type.value) if samples else None
            result = await self.evaluate(eval_type, target_agent, samples=type_samples)
            results.append(result)

        logger.info(
            "All evaluations completed",
            total=len(results),
            completed=sum(1 for r in results if r.status == EvaluationStatus.COMPLETED),
            failed=sum(1 for r in results if r.status == EvaluationStatus.FAILED),
        )

        return results

    async def generate_report(
        self,
        results: list[EvaluationResult],
        report_name: str = "AIOps Agent Platform Evaluation Report",
    ) -> BenchmarkReport:
        """
        生成评估报告

        汇总多次评估的结果，生成综合报告。

        Args:
            results: 评估结果列表
            report_name: 报告名称

        Returns:
            BenchmarkReport: 基准测试报告
        """
        report = BenchmarkReport(
            report_name=report_name,
            results=results,
        )

        # 计算汇总统计
        report.summary = self._compute_summary(results)

        # 生成改进建议
        report.recommendations = self._generate_recommendations(results)

        logger.info("Benchmark report generated", report_name=report_name)
        return report

    async def compare_runs(
        self,
        run_id_a: str,
        run_id_b: str,
    ) -> dict[str, Any]:
        """
        对比两次评估运行

        比较两个不同运行之间的差异。

        Args:
            run_id_a: 运行A的ID
            run_id_b: 运行B的ID

        Returns:
            dict: 对比结果
        """
        try:
            run_a = self._find_run(run_id_a)
            run_b = self._find_run(run_id_b)

            if not run_a or not run_b:
                return {
                    "error": "One or both runs not found",
                    "run_a_found": run_a is not None,
                    "run_b_found": run_b is not None,
                }

            comparison = {
                "run_a": {
                    "id": run_id_a,
                    "eval_type": run_a.get("eval_type", ""),
                    "overall_score": run_a.get("overall_score", 0.0),
                    "timestamp": run_a.get("timestamp", ""),
                },
                "run_b": {
                    "id": run_id_b,
                    "eval_type": run_b.get("eval_type", ""),
                    "overall_score": run_b.get("overall_score", 0.0),
                    "timestamp": run_b.get("timestamp", ""),
                },
                "score_diff": round(
                    run_b.get("overall_score", 0.0)
                    - run_a.get("overall_score", 0.0),
                    4,
                ),
                "improvement_pct": round(
                    (
                        run_b.get("overall_score", 0.0)
                        - run_a.get("overall_score", 0.0)
                    )
                    / max(run_a.get("overall_score", 0.001), 0.001)
                    * 100,
                    2,
                ),
            }

            # 判断是否有显著改进
            comparison["is_improved"] = comparison["score_diff"] > 0
            comparison["is_significant"] = abs(comparison["score_diff"]) > 0.05

            return comparison

        except Exception as e:
            logger.error("Run comparison failed", error=str(e))
            return {"error": str(e)}

    async def analyze_trends(
        self,
        eval_type: EvaluationType | None = None,
        window_size: int = 10,
    ) -> dict[str, Any]:
        """
        趋势分析

        分析指定类型评估的历史趋势。

        Args:
            eval_type: 评估类型（None则分析所有类型）
            window_size: 滑动窗口大小

        Returns:
            dict: 趋势分析结果
        """
        try:
            # 筛选历史记录
            if eval_type:
                history = [
                    h for h in self._run_history
                    if h.get("eval_type") == eval_type.value
                ]
            else:
                history = self._run_history

            if len(history) < 2:
                return {
                    "message": "Not enough data for trend analysis",
                    "data_points": len(history),
                }

            scores = [h.get("overall_score", 0.0) for h in history]

            # 计算趋势
            if len(scores) >= window_size:
                recent_scores = scores[-window_size:]
            else:
                recent_scores = scores

            trend = {
                "total_runs": len(history),
                "overall_scores": [round(s, 4) for s in scores],
                "latest_score": round(scores[-1], 4),
                "avg_score": round(float(np.mean(scores)), 4),
                "max_score": round(max(scores), 4),
                "min_score": round(min(scores), 4),
                "std_dev": round(float(np.std(scores)), 4),
                "trend_direction": (
                    "improving"
                    if len(scores) > 1 and scores[-1] > scores[0]
                    else "declining"
                    if len(scores) > 1 and scores[-1] < scores[0]
                    else "stable"
                ),
                "recent_avg": round(float(np.mean(recent_scores)), 4),
                "window_size": min(window_size, len(scores)),
            }

            # 计算移动平均
            if len(scores) >= 3:
                ma_window = min(3, len(scores))
                moving_avg = [
                    round(float(np.mean(scores[i : i + ma_window])), 4)
                    for i in range(len(scores) - ma_window + 1)
                ]
                trend["moving_average"] = moving_avg

            return trend

        except Exception as e:
            logger.error("Trend analysis failed", error=str(e))
            return {"error": str(e)}

    async def save_report(
        self,
        report: BenchmarkReport,
        output_dir: str = "/tmp/eval_reports",
    ) -> str:
        """
        保存评估报告到文件

        Args:
            report: 评估报告
            output_dir: 输出目录

        Returns:
            str: 保存的文件路径
        """
        try:
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)

            filename = (
                f"benchmark_report_{report.generated_at.strftime('%Y%m%d_%H%M%S')}.json"
            )
            filepath = output_path / filename

            data = {
                "report_id": report.report_id,
                "report_name": report.report_name,
                "generated_at": report.generated_at.isoformat(),
                "summary": report.summary,
                "recommendations": report.recommendations,
                "results": [
                    {
                        "result_id": r.result_id,
                        "eval_type": r.evaluation_type.value,
                        "eval_name": r.eval_name,
                        "overall_score": r.overall_score,
                        "status": r.status.value,
                        "metrics": [
                            {
                                "metric_name": m.metric_name,
                                "score": m.score,
                                "weight": m.weight,
                            }
                            for m in r.metric_scores
                        ],
                    }
                    for r in report.results
                ],
            }

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            logger.info("Report saved", filepath=str(filepath))
            return str(filepath)

        except Exception as e:
            logger.error("Failed to save report", error=str(e))
            return ""

    async def send_to_langfuse(
        self,
        result: EvaluationResult,
        trace_id: str = "",
    ) -> bool:
        """
        将评估结果上报到 Langfuse (可选集成)

        Args:
            result: 评估结果
            trace_id: Langfuse trace ID

        Returns:
            bool: 是否成功
        """
        try:
            # 注意: 这是 Langfuse 集成的占位实现
            # 实际集成需要安装 langfuse 包并配置 API key
            #
            # 示例代码:
            # from langfuse import Langfuse
            # langfuse = Langfuse()
            # trace = langfuse.trace(
            #     id=trace_id or result.result_id,
            #     name=f"eval_{result.evaluation_type.value}",
            #     metadata={
            #         "overall_score": result.overall_score,
            #         "eval_type": result.evaluation_type.value,
            #     }
            # )
            # for metric in result.metric_scores:
            #     trace.score(
            #         name=metric.metric_name,
            #         value=metric.score,
            #         comment=metric.details.get("description", ""),
            #     )

            logger.info(
                "Langfuse integration placeholder",
                result_id=result.result_id,
                eval_type=result.evaluation_type.value,
                overall_score=result.overall_score,
                trace_id=trace_id,
            )
            return True

        except Exception as e:
            logger.error("Langfuse integration failed", error=str(e))
            return False

    # ==================== 内部方法 ====================

    def _record_run(self, eval_type: EvaluationType, result: EvaluationResult) -> None:
        """记录评估运行到历史"""
        self._run_history.append(
            {
                "run_id": result.result_id,
                "eval_type": eval_type.value,
                "eval_name": result.eval_name,
                "overall_score": result.overall_score,
                "status": result.status.value,
                "timestamp": (
                    result.completed_at.isoformat()
                    if result.completed_at
                    else datetime.now(timezone.utc).isoformat()
                ),
            }
        )

        # 限制历史记录大小
        max_history = 1000
        if len(self._run_history) > max_history:
            self._run_history = self._run_history[-max_history:]

    def _find_run(self, run_id: str) -> dict[str, Any] | None:
        """根据ID查找历史运行记录"""
        for run in self._run_history:
            if run.get("run_id") == run_id:
                return run
        return None

    @staticmethod
    def _compute_summary(results: list[EvaluationResult]) -> dict[str, Any]:
        """
        计算汇总统计

        Args:
            results: 评估结果列表

        Returns:
            dict: 汇总统计
        """
        if not results:
            return {}

        summary: dict[str, Any] = {
            "total_evaluations": len(results),
            "completed": sum(
                1 for r in results if r.status == EvaluationStatus.COMPLETED
            ),
            "failed": sum(1 for r in results if r.status == EvaluationStatus.FAILED),
            "evaluations": {},
        }

        # 各维度得分
        for result in results:
            eval_key = result.evaluation_type.value
            summary["evaluations"][eval_key] = {
                "overall_score": round(result.overall_score, 4),
                "status": result.status.value,
                "total_samples": result.total_samples,
                "passed_samples": result.passed_samples,
                "failed_samples": result.failed_samples,
                "duration_seconds": round(result.duration_seconds, 2),
                "metrics": {
                    m.metric_name: {
                        "score": round(m.score, 4),
                        "weight": m.weight,
                        "weighted_score": round(m.weighted_score, 4),
                    }
                    for m in result.metric_scores
                },
            }

        # 计算加权总分
        completed_results = [
            r for r in results if r.status == EvaluationStatus.COMPLETED
        ]
        if completed_results:
            summary["avg_overall_score"] = round(
                float(np.mean([r.overall_score for r in completed_results])), 4
            )
            summary["min_score"] = round(
                min(r.overall_score for r in completed_results), 4
            )
            summary["max_score"] = round(
                max(r.overall_score for r in completed_results), 4
            )

        return summary

    @staticmethod
    def _generate_recommendations(
        results: list[EvaluationResult],
    ) -> list[str]:
        """
        生成改进建议

        根据评估结果生成针对性的改进建议。

        Args:
            results: 评估结果列表

        Returns:
            list[str]: 改进建议列表
        """
        recommendations: list[str] = []

        for result in results:
            if result.status != EvaluationStatus.COMPLETED:
                recommendations.append(
                    f"[{result.evaluation_type.value}] "
                    f"Evaluation failed: {', '.join(result.errors)}"
                )
                continue

            # 端到端建议
            if result.evaluation_type == EvaluationType.END_TO_END:
                for metric in result.metric_scores:
                    if metric.metric_name == "task_success_rate" and metric.score < 0.8:
                        recommendations.append(
                            f"[End-to-End] Task success rate ({metric.score:.1%}) "
                            f"is below target (80%). Consider improving playbook "
                            f"coverage, enhancing error handling, and adding more "
                            f"self-healing scenarios."
                        )
                    elif (
                        metric.metric_name == "rca_accuracy" and metric.score < 0.7
                    ):
                        recommendations.append(
                            f"[End-to-End] RCA accuracy ({metric.score:.1%}) "
                            f"needs improvement. Expand the knowledge base with "
                            f"more historical incident data and refine causal models."
                        )
                    elif (
                        metric.metric_name == "automation_rate"
                        and metric.score < 0.6
                    ):
                        recommendations.append(
                            f"[End-to-End] Automation rate ({metric.score:.1%}) "
                            f"is low. Review manual intervention points and "
                            f"automate common recovery procedures."
                        )

            # 推理建议
            elif result.evaluation_type == EvaluationType.REASONING:
                for metric in result.metric_scores:
                    if (
                        metric.metric_name == "root_cause_accuracy"
                        and metric.score < 0.7
                    ):
                        recommendations.append(
                            f"[Reasoning] Root cause accuracy ({metric.score:.1%}) "
                            f"needs improvement. Expand knowledge base, refine "
                            f"Bayesian priors, and add more training examples."
                        )
                    elif (
                        metric.metric_name == "confidence_calibration"
                        and metric.score < 0.7
                    ):
                        recommendations.append(
                            f"[Reasoning] Confidence calibration "
                            f"({metric.score:.2f}) is suboptimal. Implement "
                            f"temperature scaling or Platt scaling for better "
                            f"confidence calibration."
                        )
                    elif (
                        metric.metric_name == "reasoning_chain_quality"
                        and metric.score < 0.6
                    ):
                        recommendations.append(
                            f"[Reasoning] Reasoning chain quality "
                            f"({metric.score:.1%}) is low. Improve CoT prompting "
                            f"and add validation steps to the reasoning process."
                        )

            # 工具调用建议
            elif result.evaluation_type == EvaluationType.TOOL_CALL:
                for metric in result.metric_scores:
                    if (
                        metric.metric_name == "tool_selection_accuracy"
                        and metric.score < 0.8
                    ):
                        recommendations.append(
                            f"[Tool Call] Tool selection accuracy "
                            f"({metric.score:.1%}) can be improved. Add more "
                            f"training examples for tool selection decisions."
                        )
                    elif (
                        metric.metric_name == "parameter_accuracy"
                        and metric.score < 0.8
                    ):
                        recommendations.append(
                            f"[Tool Call] Parameter accuracy ({metric.score:.1%}) "
                            f"needs work. Improve parameter schema understanding "
                            f"and validation."
                        )
                    elif (
                        metric.metric_name == "tool_call_efficiency"
                        and metric.score < 0.7
                    ):
                        recommendations.append(
                            f"[Tool Call] Tool call efficiency "
                            f"({metric.score:.1%}) is low. Optimize tool selection "
                            f"to reduce unnecessary calls."
                        )

            # RAG 建议
            elif result.evaluation_type == EvaluationType.RAG:
                for metric in result.metric_scores:
                    if (
                        metric.metric_name == "retrieval_precision"
                        and metric.score < 0.7
                    ):
                        recommendations.append(
                            f"[RAG] Retrieval precision ({metric.score:.1%}) "
                            f"is low. Consider using better embedding models, "
                            f"adding metadata filters, or implementing re-ranking."
                        )
                    elif (
                        metric.metric_name == "retrieval_recall"
                        and metric.score < 0.6
                    ):
                        recommendations.append(
                            f"[RAG] Retrieval recall ({metric.score:.1%}) "
                            f"is low. Increase top-k values, use hybrid search "
                            f"(dense + sparse), or expand the knowledge base."
                        )
                    elif (
                        metric.metric_name == "answer_faithfulness"
                        and metric.score < 0.7
                    ):
                        recommendations.append(
                            f"[RAG] Answer faithfulness ({metric.score:.1%}) "
                            f"needs improvement. Implement groundedness checks "
                            f"and citation mechanisms."
                        )

        if not recommendations:
            recommendations.append(
                "All metrics are within acceptable ranges. System is performing well."
            )

        return recommendations

    def get_history(self) -> list[dict[str, Any]]:
        """获取评估历史"""
        return self._run_history.copy()

    def get_evaluator(self, eval_type: EvaluationType) -> Any | None:
        """获取指定类型的评估器"""
        return self._evaluators.get(eval_type)

    def reset_history(self) -> None:
        """重置评估历史"""
        self._run_history.clear()
        logger.info("Evaluation history reset")


# =============================================================================
# 模块级函数
# =============================================================================

_framework: EvaluationFramework | None = None


def get_evaluation_framework() -> EvaluationFramework:
    """
    获取评估框架实例（单例）

    Returns:
        EvaluationFramework: 评估框架实例
    """
    global _framework
    if _framework is None:
        _framework = EvaluationFramework()
    return _framework


async def run_full_evaluation(
    target_agent: str = "",
    samples: dict[str, list[dict[str, Any]]] | None = None,
) -> BenchmarkReport:
    """
    运行完整评估流程

    便捷的顶层函数，执行所有评估并生成报告。

    Args:
        target_agent: 目标 Agent
        samples: 各类型的自定义样本

    Returns:
        BenchmarkReport: 完整评估报告
    """
    framework = get_evaluation_framework()
    results = await framework.evaluate_all(target_agent, samples)
    report = await framework.generate_report(results)
    return report
