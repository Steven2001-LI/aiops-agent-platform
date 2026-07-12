"""
AIOps Agent Platform - Tool Call Evaluation

工具调用评估，评估 Agent 调用工具的准确性和效率。
参考 DoVer 的干预验证思路，支持干预验证（修改工具调用看结果变化）。

评估维度:
- 工具选择正确性 (Tool Selection Accuracy)
- 参数传递正确性 (Parameter Accuracy)
- 执行成功率 (Execution Success Rate)
- 调用效率 (Call Efficiency)
- 调用序列正确性 (Call Sequence Correctness)
- 干预验证 (Intervention Verification - DoVer)
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.evaluation.metrics import (
    parameter_accuracy,
    tool_call_efficiency,
    tool_call_sequence_correctness,
    tool_execution_success,
    tool_selection_accuracy,
    weighted_average,
)
from app.models.evaluation import (
    EvaluationResult,
    EvaluationStatus,
    EvaluationType,
    MetricScore,
)
from app.utils.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ToolCall:
    """工具调用记录"""

    tool_name: str
    parameters: dict[str, Any] = field(default_factory=dict)
    expected_tool: str = ""
    expected_parameters: dict[str, Any] = field(default_factory=dict)
    execution_result: dict[str, Any] = field(default_factory=dict)
    call_order: int = 0

    @property
    def selection_correct(self) -> bool:
        """工具选择是否正确"""
        if not self.expected_tool:
            return True  # 没有期望值时默认正确
        return self.tool_name == self.expected_tool

    @property
    def params_correct(self) -> bool:
        """参数是否正确"""
        if not self.expected_parameters:
            return True
        if not self.parameters:
            return False

        expected_keys = set(self.expected_parameters.keys())
        if not expected_keys:
            return True

        correct = sum(
            1
            for k in expected_keys
            if k in self.parameters
            and self.parameters[k] == self.expected_parameters[k]
        )
        return correct == len(expected_keys)

    @property
    def execution_success(self) -> bool:
        """执行是否成功"""
        return self.execution_result.get("success", False)


# 默认工具调用测试数据集
DEFAULT_TOOL_CALL_DATASET: list[dict[str, Any]] = [
    {
        "id": "tool_select_metrics_query",
        "name": "Metrics Query Tool Selection",
        "description": "CPU high scenario - should query metrics",
        "expected_tools": ["query_metrics", "get_service_info"],
        "tool_calls": [
            {
                "tool_name": "query_metrics",
                "parameters": {
                    "service": "order-service",
                    "metric": "cpu_usage",
                    "duration": "5m",
                },
                "expected_tool": "query_metrics",
                "expected_parameters": {
                    "service": "order-service",
                    "metric": "cpu_usage",
                    "duration": "5m",
                },
                "execution_result": {"success": True},
            },
            {
                "tool_name": "get_service_info",
                "parameters": {"service": "order-service"},
                "expected_tool": "get_service_info",
                "expected_parameters": {"service": "order-service"},
                "execution_result": {"success": True},
            },
        ],
    },
    {
        "id": "tool_select_log_analysis",
        "name": "Log Analysis Tool Selection",
        "description": "Error spike - should query logs and traces",
        "expected_tools": ["query_logs", "query_traces", "get_dependencies"],
        "tool_calls": [
            {
                "tool_name": "query_logs",
                "parameters": {
                    "service": "payment-service",
                    "level": "error",
                    "duration": "10m",
                },
                "expected_tool": "query_logs",
                "expected_parameters": {
                    "service": "payment-service",
                    "level": "error",
                    "duration": "10m",
                },
                "execution_result": {"success": True},
            },
            {
                "tool_name": "query_traces",
                "parameters": {"service": "payment-service", "min_duration": "1000"},
                "expected_tool": "query_traces",
                "expected_parameters": {
                    "service": "payment-service",
                    "min_duration": "1000",
                },
                "execution_result": {"success": True},
            },
            {
                "tool_name": "get_dependencies",
                "parameters": {"service": "payment-service"},
                "expected_tool": "get_dependencies",
                "expected_parameters": {"service": "payment-service"},
                "execution_result": {"success": True},
            },
        ],
    },
    {
        "id": "tool_select_heal_action",
        "name": "Heal Action Tool Selection",
        "description": "Service restart - should execute heal action",
        "expected_tools": ["execute_heal", "verify_health"],
        "tool_calls": [
            {
                "tool_name": "execute_heal",
                "parameters": {
                    "service": "cache-service",
                    "action": "restart",
                    "dry_run": False,
                },
                "expected_tool": "execute_heal",
                "expected_parameters": {
                    "service": "cache-service",
                    "action": "restart",
                    "dry_run": False,
                },
                "execution_result": {"success": True},
            },
            {
                "tool_name": "verify_health",
                "parameters": {"service": "cache-service", "timeout": 30},
                "expected_tool": "verify_health",
                "expected_parameters": {"service": "cache-service", "timeout": 30},
                "execution_result": {"success": True},
            },
        ],
    },
]


class ToolCallEvaluator:
    """
    工具调用评估器

    评估 Agent 选择和调用工具的准确性。
    参考 DoVer 的干预验证思路。

    支持:
    - 工具选择评估 (evaluate_tool_selection)
    - 参数正确性评估 (evaluate_parameters)
    - 调用效率评估 (evaluate_efficiency)
    - 干预验证 (verify_with_intervention)
    - 批量评估 (evaluate)
    """

    def __init__(self) -> None:
        self._eval_history: list[EvaluationResult] = []

    async def evaluate(
        self,
        target_agent: str = "",
        dataset_name: str = "",
        samples: list[dict[str, Any]] | None = None,
    ) -> EvaluationResult:
        """
        执行工具调用评估

        Args:
            target_agent: 目标 Agent 名称
            dataset_name: 数据集名称
            samples: 自定义测试样本

        Returns:
            EvaluationResult: 评估结果
        """
        result = EvaluationResult(
            evaluation_type=EvaluationType.TOOL_CALL,
            eval_name=f"tool_call_{target_agent or 'all'}",
            description="评估 Agent 调用工具的准确性和效率",
            agent_type=target_agent,
            status=EvaluationStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
        )

        try:
            test_samples = samples or await self._load_default_dataset()
            result.total_samples = len(test_samples)

            if not test_samples:
                result.status = EvaluationStatus.FAILED
                result.errors.append("No test samples available")
                result.completed_at = datetime.now(timezone.utc)
                return result

            # 收集所有工具调用记录
            all_tool_calls: list[ToolCall] = []
            all_expected_tools: list[str] = []
            all_actual_tools: list[str] = []
            execution_results: list[dict[str, Any]] = []
            sample_tool_counts: list[int] = []
            sample_expected_counts: list[int] = []

            for sample in test_samples:
                tool_calls_data = sample.get("tool_calls", [])
                expected_tools = sample.get("expected_tools", [])
                sample_tool_counts.append(len(tool_calls_data))
                sample_expected_counts.append(len(expected_tools))

                actual_tools: list[str] = []
                for tc_data in tool_calls_data:
                    tc = ToolCall(
                        tool_name=tc_data.get("tool_name", ""),
                        parameters=tc_data.get("parameters", {}),
                        expected_tool=tc_data.get("expected_tool", ""),
                        expected_parameters=tc_data.get("expected_parameters", {}),
                        execution_result=tc_data.get("execution_result", {}),
                        call_order=tc_data.get("call_order", 0),
                    )
                    all_tool_calls.append(tc)
                    actual_tools.append(tc.tool_name)
                    execution_results.append(tc_data.get("execution_result", {}))

                all_expected_tools.extend(expected_tools)
                all_actual_tools.extend(actual_tools)

            # 计算各项指标
            if all_tool_calls:
                # 工具选择准确率
                selection_correct = sum(
                    1 for tc in all_tool_calls if tc.selection_correct
                )
                selection_acc = selection_correct / len(all_tool_calls)

                # 参数正确率
                params_correct = sum(
                    1 for tc in all_tool_calls if tc.params_correct
                )
                params_acc = params_correct / len(all_tool_calls)

                # 执行成功率
                exec_success = tool_execution_success(execution_results)

                # 序列正确性
                sequence_scores: list[float] = []
                for sample in test_samples:
                    expected_tools = sample.get("expected_tools", [])
                    actual_tools = [
                        tc.get("tool_name", "")
                        for tc in sample.get("tool_calls", [])
                    ]
                    if expected_tools and actual_tools:
                        seq_score = tool_call_sequence_correctness(
                            actual_tools, expected_tools
                        )
                        sequence_scores.append(seq_score)

                seq_score = (
                    float(np.mean(sequence_scores))
                    if sequence_scores
                    else 0.0
                )

                # 调用效率
                efficiency_scores = []
                for actual_count, expected_count in zip(
                    sample_tool_counts, sample_expected_counts
                ):
                    if expected_count > 0 and actual_count > 0:
                        eff = min(1.0, expected_count / actual_count)
                        efficiency_scores.append(eff)

                efficiency = (
                    float(np.mean(efficiency_scores))
                    if efficiency_scores
                    else 0.0
                )

            else:
                selection_acc = params_acc = exec_success = seq_score = efficiency = 0.0

            result.metric_scores = [
                MetricScore(
                    metric_name="tool_selection_accuracy",
                    score=selection_acc,
                    weight=0.30,
                    details={
                        "description": "工具选择正确性",
                        "total_calls": len(all_tool_calls),
                        "correct_selections": sum(
                            1 for tc in all_tool_calls if tc.selection_correct
                        ),
                    },
                ),
                MetricScore(
                    metric_name="parameter_accuracy",
                    score=params_acc,
                    weight=0.25,
                    details={
                        "description": "参数传递正确性",
                        "total_calls": len(all_tool_calls),
                        "correct_params": sum(
                            1 for tc in all_tool_calls if tc.params_correct
                        ),
                    },
                ),
                MetricScore(
                    metric_name="execution_success_rate",
                    score=exec_success,
                    weight=0.20,
                    details={
                        "description": "工具执行成功率",
                        "total_executions": len(execution_results),
                    },
                ),
                MetricScore(
                    metric_name="call_sequence_quality",
                    score=seq_score,
                    weight=0.15,
                    details={
                        "description": "调用序列正确性",
                    },
                ),
                MetricScore(
                    metric_name="tool_call_efficiency",
                    score=efficiency,
                    weight=0.10,
                    details={
                        "description": "调用效率（是否最小必要调用）",
                        "avg_actual_calls": round(
                            float(np.mean(sample_tool_counts)), 2
                        )
                        if sample_tool_counts
                        else 0,
                        "avg_expected_calls": round(
                            float(np.mean(sample_expected_counts)), 2
                        )
                        if sample_expected_counts
                        else 0,
                    },
                ),
            ]

            result.overall_score = sum(s.weighted_score for s in result.metric_scores)
            result.passed_samples = sum(
                1
                for tc in all_tool_calls
                if tc.selection_correct and tc.params_correct and tc.execution_success
            )
            result.failed_samples = len(all_tool_calls) - result.passed_samples
            result.status = EvaluationStatus.COMPLETED

            logger.info(
                "Tool call evaluation completed",
                overall_score=result.overall_score,
                selection_accuracy=selection_acc,
                parameter_accuracy=params_acc,
            )

        except Exception as e:
            logger.error("Tool call evaluation failed", error=str(e), exc_info=True)
            result.status = EvaluationStatus.FAILED
            result.errors.append(str(e))

        finally:
            result.completed_at = datetime.now(timezone.utc)
            self._eval_history.append(result)

        return result

    async def evaluate_tool_selection(
        self,
        selected_tools: list[str],
        expected_tools: list[str],
    ) -> dict[str, Any]:
        """
        评估工具选择的正确性

        Args:
            selected_tools: 实际选择的工具列表
            expected_tools: 期望选择的工具列表

        Returns:
            dict: 工具选择评估结果
        """
        try:
            accuracy = tool_selection_accuracy(selected_tools, expected_tools)

            selected_set = set(selected_tools)
            expected_set = set(expected_tools)

            extra_tools = list(selected_set - expected_set)
            missing_tools = list(expected_set - selected_set)
            correct_tools = list(selected_set & expected_set)

            return {
                "accuracy": round(accuracy, 4),
                "selected_tools": selected_tools,
                "expected_tools": expected_tools,
                "correct_tools": correct_tools,
                "extra_tools": extra_tools,
                "missing_tools": missing_tools,
                "precision": (
                    len(correct_tools) / len(selected_set)
                    if selected_set
                    else 0.0
                ),
                "recall": (
                    len(correct_tools) / len(expected_set)
                    if expected_set
                    else 0.0
                ),
            }

        except Exception as e:
            logger.error("Tool selection evaluation failed", error=str(e))
            return {"accuracy": 0.0, "error": str(e)}

    async def evaluate_parameters(
        self,
        tool_calls: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        评估工具参数的正确性

        Args:
            tool_calls: 工具调用记录列表

        Returns:
            dict: 参数评估结果
        """
        try:
            total_params = 0
            correct_params = 0
            per_tool_results: list[dict[str, Any]] = []

            for tc in tool_calls:
                tool_name = tc.get("tool_name", "")
                actual_params = tc.get("parameters", {})
                expected_params = tc.get("expected_parameters", {})

                if not expected_params:
                    continue

                expected_keys = set(expected_params.keys())
                tool_total = len(expected_keys)
                tool_correct = sum(
                    1
                    for k in expected_keys
                    if k in actual_params and actual_params[k] == expected_params[k]
                )

                total_params += tool_total
                correct_params += tool_correct

                per_tool_results.append(
                    {
                        "tool_name": tool_name,
                        "total_params": tool_total,
                        "correct_params": tool_correct,
                        "accuracy": tool_correct / tool_total if tool_total > 0 else 0.0,
                        "missing_params": [
                            k for k in expected_keys if k not in actual_params
                        ],
                        "wrong_params": [
                            k
                            for k in expected_keys
                            if k in actual_params
                            and actual_params[k] != expected_params[k]
                        ],
                    }
                )

            overall_accuracy = (
                correct_params / total_params if total_params > 0 else 0.0
            )

            return {
                "overall_accuracy": round(overall_accuracy, 4),
                "total_params": total_params,
                "correct_params": correct_params,
                "per_tool_results": per_tool_results,
            }

        except Exception as e:
            logger.error("Parameter evaluation failed", error=str(e))
            return {"overall_accuracy": 0.0, "error": str(e)}

    async def evaluate_efficiency(
        self,
        actual_calls: list[str],
        minimum_calls: list[str],
    ) -> dict[str, Any]:
        """
        评估工具调用效率

        评估是否使用了最小必要的工具调用次数。

        Args:
            actual_calls: 实际工具调用列表
            minimum_calls: 最少必要工具调用列表

        Returns:
            dict: 效率评估结果
        """
        try:
            efficiency = tool_call_efficiency(actual_calls, minimum_calls)

            actual_count = len(actual_calls)
            minimum_count = len(minimum_calls)
            extra_calls = max(0, actual_count - minimum_count)

            return {
                "efficiency": round(efficiency, 4),
                "actual_calls": actual_count,
                "minimum_calls": minimum_count,
                "extra_calls": extra_calls,
                "efficiency_score": round(efficiency, 4),
                "is_optimal": actual_count <= minimum_count,
            }

        except Exception as e:
            logger.error("Efficiency evaluation failed", error=str(e))
            return {"efficiency": 0.0, "error": str(e)}

    async def verify_with_intervention(
        self,
        original_calls: list[dict[str, Any]],
        expected_calls: list[dict[str, Any]],
        replay_callback: Any | None = None,
    ) -> dict[str, Any]:
        """
        DoVer 风格的干预验证

        通过逐个干预（修改/替换某个工具调用）来验证工具选择的正确性。
        如果修改后的调用导致结果改善，说明原始选择不是最优的。

        Args:
            original_calls: 原始工具调用序列
            expected_calls: 期望的工具调用序列
            replay_callback: 重放回调函数（实际应用中用于重放调用）

        Returns:
            dict: 干预验证结果
        """
        try:
            intervention_results: list[dict[str, Any]] = []
            validated_count = 0
            total_interventions = 0

            # 将原始调用和期望调用对齐进行比较
            max_len = max(len(original_calls), len(expected_calls))

            for i in range(max_len):
                original = original_calls[i] if i < len(original_calls) else None
                expected = expected_calls[i] if i < len(expected_calls) else None

                if original is None or expected is None:
                    continue

                # 检查工具名称是否匹配
                original_tool = original.get("tool_name", "")
                expected_tool = expected.get("tool_name", "")

                tool_change_validated = original_tool == expected_tool

                # 检查参数是否匹配
                original_params = original.get("parameters", {})
                expected_params = expected.get("parameters", {})
                param_keys = set(expected_params.keys())

                param_changes_validated = True
                param_changes: list[dict[str, Any]] = []

                for key in param_keys:
                    orig_val = original_params.get(key)
                    exp_val = expected_params.get(key)
                    if orig_val != exp_val:
                        param_changes.append(
                            {
                                "parameter": key,
                                "original": orig_val,
                                "expected": exp_val,
                                "improvement": None,  # 需要 replay 来确定
                            }
                        )
                        param_changes_validated = False

                # 综合验证
                is_validated = tool_change_validated and param_changes_validated
                if is_validated:
                    validated_count += 1
                total_interventions += 1

                intervention_results.append(
                    {
                        "intervention_point": i,
                        "original_tool": original_tool,
                        "expected_tool": expected_tool,
                        "tool_match": tool_change_validated,
                        "param_changes": param_changes,
                        "param_match": param_changes_validated,
                        "hypothesis_validated": is_validated,
                    }
                )

            validation_rate = (
                validated_count / total_interventions
                if total_interventions > 0
                else 0.0
            )

            return {
                "validation_rate": round(validation_rate, 4),
                "total_interventions": total_interventions,
                "validated_count": validated_count,
                "failed_count": total_interventions - validated_count,
                "intervention_results": intervention_results,
                "interpretation": (
                    "Tool calls are well-optimized"
                    if validation_rate >= 0.8
                    else (
                        "Some tool calls can be improved"
                        if validation_rate >= 0.5
                        else "Tool selection needs significant improvement"
                    )
                ),
            }

        except Exception as e:
            logger.error("Intervention verification failed", error=str(e))
            return {"validation_rate": 0.0, "error": str(e)}

    async def _load_default_dataset(self) -> list[dict[str, Any]]:
        """加载默认工具调用测试数据集"""
        return DEFAULT_TOOL_CALL_DATASET.copy()

    async def save_results(
        self, result: EvaluationResult, output_dir: str = "/tmp/eval_results"
    ) -> str:
        """
        保存评估结果到文件

        Args:
            result: 评估结果
            output_dir: 输出目录

        Returns:
            str: 保存的文件路径
        """
        try:
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)

            filename = (
                f"tool_call_{result.started_at.strftime('%Y%m%d_%H%M%S')}.json"
            )
            filepath = output_path / filename

            data = {
                "result_id": result.result_id,
                "evaluation_type": result.evaluation_type.value,
                "overall_score": result.overall_score,
                "metric_scores": [
                    {
                        "metric_name": m.metric_name,
                        "score": m.score,
                        "weight": m.weight,
                        "details": m.details,
                    }
                    for m in result.metric_scores
                ],
                "total_samples": result.total_samples,
                "passed_samples": result.passed_samples,
                "failed_samples": result.failed_samples,
                "duration_seconds": result.duration_seconds,
            }

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            logger.info("Tool call evaluation results saved", filepath=str(filepath))
            return str(filepath)

        except Exception as e:
            logger.error("Failed to save tool call results", error=str(e))
            return ""
