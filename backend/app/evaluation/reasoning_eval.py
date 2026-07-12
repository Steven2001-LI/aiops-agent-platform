"""
AIOps Agent Platform - Reasoning Evaluation

推理能力评估，评估 Agent 的逻辑推理和因果分析能力。
参考 VitaBench 的推理复杂度评估思路，支持 Rubric-based 评估。

评估维度:
- 根因识别准确性 (Root Cause Identification)
- 证据质量 (Evidence Quality)
- 置信度校准 (Confidence Calibration)
- 影响范围分析 (Impact Analysis)
- 推理链完整性 (Reasoning Chain Completeness)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.evaluation.metrics import (
    confidence_calibration,
    evidence_completeness,
    impact_analysis_accuracy,
    reasoning_steps_quality,
    root_cause_accuracy,
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


# =============================================================================
# Rubric 评分标准
# =============================================================================

REASONING_RUBRIC: dict[str, dict[str, Any]] = {
    "root_cause_identification": {
        "description": "是否正确识别了根因",
        "criteria": [
            {"score": 1.0, "desc": "准确识别根因"},
            {"score": 0.5, "desc": "识别了相关但未精确"},
            {"score": 0.0, "desc": "完全错误"},
        ],
    },
    "evidence_quality": {
        "description": "证据是否充分且相关",
        "criteria": [
            {"score": 1.0, "desc": "证据充分且高度相关"},
            {"score": 0.5, "desc": "有部分证据但不够充分"},
            {"score": 0.0, "desc": "缺乏有效证据"},
        ],
    },
    "confidence_calibration": {
        "description": "置信度与实际准确率匹配度",
        "criteria": [
            {"score": 1.0, "desc": "置信度与准确率高度一致"},
            {"score": 0.5, "desc": "有一定偏差"},
            {"score": 0.0, "desc": "严重偏差"},
        ],
    },
    "impact_analysis": {
        "description": "影响范围分析是否准确",
        "criteria": [
            {"score": 1.0, "desc": "完整准确"},
            {"score": 0.5, "desc": "部分准确"},
            {"score": 0.0, "desc": "错误"},
        ],
    },
    "reasoning_chain_completeness": {
        "description": "推理链是否完整且逻辑自洽",
        "criteria": [
            {"score": 1.0, "desc": "推理链完整、逻辑自洽"},
            {"score": 0.5, "desc": "推理链基本完整但有跳跃"},
            {"score": 0.0, "desc": "推理链断裂或逻辑矛盾"},
        ],
    },
}


# 默认推理测试数据集
DEFAULT_REASONING_DATASET: list[dict[str, Any]] = [
    {
        "id": "rca_cpu_spike",
        "name": "CPU Spike Root Cause",
        "query": "Why is CPU usage spiking on order-service?",
        "ground_truth": {
            "root_cause": "traffic_spike",
            "confidence": 0.9,
            "evidence": {
                "metrics": ["cpu_usage", "request_rate"],
                "logs": ["rate_limiter_triggered"],
                "correlations": ["cpu_vs_request_rate: 0.92"],
            },
            "impact_chain": [
                "traffic_spike",
                "increased_request_rate",
                "cpu_usage_high",
                "response_time_degradation",
            ],
            "reasoning_steps": [
                {"description": "Observe CPU usage anomaly", "order": 1},
                {"description": "Check request rate correlation", "order": 2},
                {"description": "Identify traffic spike pattern", "order": 3},
                {"description": "Confirm root cause", "order": 4},
            ],
            "suggested_actions": ["scale_up", "enable_rate_limiting"],
        },
    },
    {
        "id": "rca_memory_leak",
        "name": "Memory Leak Analysis",
        "query": "What is causing the memory leak in payment-service?",
        "ground_truth": {
            "root_cause": "unclosed_db_connections",
            "confidence": 0.85,
            "evidence": {
                "metrics": ["memory_usage", "db_connections"],
                "logs": ["connection_pool_exhausted"],
                "correlations": ["memory_vs_connections: 0.88"],
            },
            "impact_chain": [
                "unclosed_db_connections",
                "connection_pool_growth",
                "memory_leak",
                "oom_risk",
            ],
            "reasoning_steps": [
                {"description": "Observe memory growth trend", "order": 1},
                {"description": "Check connection pool status", "order": 2},
                {"description": "Analyze connection lifecycle", "order": 3},
                {"description": "Identify unclosed connections", "order": 4},
            ],
            "suggested_actions": ["restart_service", "fix_connection_leak"],
        },
    },
    {
        "id": "rca_latency_degradation",
        "name": "Latency Degradation",
        "query": "Why is P99 latency increasing?",
        "ground_truth": {
            "root_cause": "cache_miss_spike",
            "confidence": 0.8,
            "evidence": {
                "metrics": ["p99_latency", "cache_hit_rate"],
                "logs": ["cache_invalidated"],
                "correlations": ["latency_vs_cache_miss: -0.85"],
            },
            "impact_chain": [
                "cache_invalidated",
                "cache_miss_increase",
                "database_queries_increase",
                "p99_latency_high",
            ],
            "reasoning_steps": [
                {"description": "Observe latency increase", "order": 1},
                {"description": "Check cache hit rate", "order": 2},
                {"description": "Analyze upstream dependencies", "order": 3},
                {"description": "Confirm cache invalidation event", "order": 4},
            ],
            "suggested_actions": ["warm_cache", "investigate_invalidation"],
        },
    },
]


class ReasoningEvaluator:
    """
    推理能力评估器

    评估 Agent 的逻辑推理、因果分析和决策能力。
    参考 VitaBench 的推理复杂度评估思路。

    支持:
    - 根因分析评估 (evaluate_rca)
    - 置信度校准评估 (evaluate_confidence)
    - 推理链评估 (evaluate_reasoning_chain)
    - Rubric-based 评估 (evaluate_with_rubric)
    """

    def __init__(self) -> None:
        self._rubric = REASONING_RUBRIC
        self._eval_history: list[EvaluationResult] = []

    async def evaluate(
        self,
        target_agent: str = "",
        dataset_name: str = "",
        samples: list[dict[str, Any]] | None = None,
    ) -> EvaluationResult:
        """
        执行推理评估

        Args:
            target_agent: 目标 Agent 名称
            dataset_name: 数据集名称
            samples: 自定义测试样本

        Returns:
            EvaluationResult: 评估结果
        """
        result = EvaluationResult(
            evaluation_type=EvaluationType.REASONING,
            eval_name=f"reasoning_{target_agent or 'all'}",
            description="评估 Agent 的逻辑推理和因果分析能力",
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

            # 评估每个样本
            sample_results: list[dict[str, Any]] = []
            rca_scores: list[float] = []
            calibration_errors: list[float] = []
            evidence_scores: list[float] = []
            impact_scores: list[float] = []
            chain_scores: list[float] = []
            confidences: list[float] = []
            accuracies: list[bool] = []

            for sample in test_samples:
                try:
                    gt = sample.get("ground_truth", {})
                    pred = sample.get("predicted", {})

                    # 根因准确率
                    if pred.get("root_cause") and gt.get("root_cause"):
                        rca_match = pred["root_cause"] == gt["root_cause"]
                        rca_scores.append(1.0 if rca_match else 0.0)

                    # 置信度收集
                    if pred.get("confidence") is not None and gt.get("root_cause"):
                        confidences.append(pred["confidence"])
                        rca_match = pred.get("root_cause") == gt.get("root_cause")
                        accuracies.append(rca_match)

                    # 证据质量
                    if pred.get("evidence") and gt.get("evidence"):
                        ev_score = evidence_completeness(
                            pred["evidence"], gt["evidence"]
                        )
                        evidence_scores.append(ev_score)

                    # 影响链准确性
                    if pred.get("impact_chain") and gt.get("impact_chain"):
                        ia = impact_analysis_accuracy(
                            pred["impact_chain"], gt["impact_chain"]
                        )
                        impact_scores.append(ia["f1"])

                    # 推理链质量
                    if pred.get("reasoning_steps") and gt.get("reasoning_steps"):
                        chain_score = reasoning_steps_quality(
                            pred["reasoning_steps"], gt["reasoning_steps"]
                        )
                        chain_scores.append(chain_score)

                    sample_results.append(
                        {
                            "sample_id": sample.get("id", ""),
                            "rca_match": pred.get("root_cause") == gt.get("root_cause"),
                            "predicted_root_cause": pred.get("root_cause"),
                            "actual_root_cause": gt.get("root_cause"),
                        }
                    )

                except Exception as e:
                    logger.error(
                        "Sample reasoning evaluation failed",
                        sample=sample.get("id", "unknown"),
                        error=str(e),
                    )

            # 置信度校准误差
            if confidences and accuracies:
                calibration_error = confidence_calibration(confidences, accuracies)
            else:
                calibration_error = 0.0

            # 计算各项指标得分
            rca_acc = float(np.mean(rca_scores)) if rca_scores else 0.0
            ev_score = float(np.mean(evidence_scores)) if evidence_scores else 0.0
            imp_score = float(np.mean(impact_scores)) if impact_scores else 0.0
            chain_score = float(np.mean(chain_scores)) if chain_scores else 0.0

            # 校准得分 (校准误差越小越好)
            calibration_score = max(0.0, 1.0 - calibration_error * 3)

            result.metric_scores = [
                MetricScore(
                    metric_name="root_cause_accuracy",
                    score=rca_acc,
                    weight=0.35,
                    details={
                        "description": "根因分析准确性",
                        "samples_evaluated": len(rca_scores),
                    },
                ),
                MetricScore(
                    metric_name="confidence_calibration",
                    score=calibration_score,
                    weight=0.20,
                    details={
                        "description": "置信度校准得分 (1 - ECE*3)",
                        "ece": round(calibration_error, 4),
                    },
                ),
                MetricScore(
                    metric_name="evidence_completeness",
                    score=ev_score,
                    weight=0.15,
                    details={
                        "description": "证据完整性",
                        "samples_evaluated": len(evidence_scores),
                    },
                ),
                MetricScore(
                    metric_name="impact_analysis_f1",
                    score=imp_score,
                    weight=0.15,
                    details={
                        "description": "影响范围分析 F1",
                        "samples_evaluated": len(impact_scores),
                    },
                ),
                MetricScore(
                    metric_name="reasoning_chain_quality",
                    score=chain_score,
                    weight=0.15,
                    details={
                        "description": "推理链质量",
                        "samples_evaluated": len(chain_scores),
                    },
                ),
            ]

            result.overall_score = sum(s.weighted_score for s in result.metric_scores)
            result.passed_samples = sum(
                1 for s in sample_results if s.get("rca_match", False)
            )
            result.failed_samples = result.total_samples - result.passed_samples
            result.sample_results = sample_results
            result.status = EvaluationStatus.COMPLETED

            logger.info(
                "Reasoning evaluation completed",
                overall_score=result.overall_score,
                rca_accuracy=rca_acc,
                calibration_error=calibration_error,
            )

        except Exception as e:
            logger.error("Reasoning evaluation failed", error=str(e), exc_info=True)
            result.status = EvaluationStatus.FAILED
            result.errors.append(str(e))

        finally:
            result.completed_at = datetime.now(timezone.utc)
            self._eval_history.append(result)

        return result

    async def evaluate_rca(
        self,
        predicted_root_causes: list[str],
        actual_root_causes: list[str],
        predicted_confidences: list[float] | None = None,
    ) -> dict[str, Any]:
        """
        评估根因分析质量

        Args:
            predicted_root_causes: 预测根因列表
            actual_root_causes: 实际根因列表
            predicted_confidences: 预测置信度列表（可选）

        Returns:
            dict: RCA 评估结果
        """
        try:
            rca_acc = root_cause_accuracy(
                predicted_root_causes, actual_root_causes
            )

            result = {
                "root_cause_accuracy": rca_acc,
                "total_samples": len(predicted_root_causes),
                "correct_predictions": sum(
                    1 for p, a in zip(predicted_root_causes, actual_root_causes) if p == a
                ),
            }

            # 如果提供了置信度，计算校准误差
            if predicted_confidences and len(predicted_confidences) == len(
                actual_root_causes
            ):
                accuracies = [
                    p == a
                    for p, a in zip(predicted_root_causes, actual_root_causes)
                ]
                ece = confidence_calibration(predicted_confidences, accuracies)
                result["calibration_error_ece"] = ece
                result["calibration_score"] = max(0.0, 1.0 - ece * 3)

            return result

        except Exception as e:
            logger.error("RCA evaluation failed", error=str(e))
            return {"error": str(e), "root_cause_accuracy": 0.0}

    async def evaluate_confidence(
        self,
        confidences: list[float],
        accuracies: list[bool],
    ) -> dict[str, Any]:
        """
        评估置信度校准质量

        计算 ECE 并提供校准分析。

        Args:
            confidences: 置信度列表 (0.0 - 1.0)
            accuracies: 准确率列表 (True/False)

        Returns:
            dict: 置信度校准评估结果
        """
        try:
            ece = confidence_calibration(confidences, accuracies)

            # 按置信度分桶分析
            n_bins = 5
            bin_boundaries = np.linspace(0, 1, n_bins + 1)
            bin_analysis = []

            for i, (lower, upper) in enumerate(zip(bin_boundaries[:-1], bin_boundaries[1:])):
                in_bin = [
                    j
                    for j, c in enumerate(confidences)
                    if lower <= c < upper or (upper == 1.0 and c == 1.0)
                ]

                if in_bin:
                    bin_acc = float(np.mean([1.0 if accuracies[j] else 0.0 for j in in_bin]))
                    bin_conf = float(np.mean([confidences[j] for j in in_bin]))
                else:
                    bin_acc = 0.0
                    bin_conf = 0.0

                bin_analysis.append(
                    {
                        "bin": i,
                        "range": [round(float(lower), 2), round(float(upper), 2)],
                        "count": len(in_bin),
                        "avg_confidence": round(bin_conf, 4),
                        "avg_accuracy": round(bin_acc, 4),
                        "gap": round(abs(bin_conf - bin_acc), 4),
                    }
                )

            return {
                "ece": round(ece, 4),
                "calibration_score": round(max(0.0, 1.0 - ece * 3), 4),
                "mean_confidence": round(float(np.mean(confidences)), 4),
                "mean_accuracy": round(
                    float(np.mean([1.0 if a else 0.0 for a in accuracies])), 4
                ),
                "bin_analysis": bin_analysis,
                "total_samples": len(confidences),
            }

        except Exception as e:
            logger.error("Confidence evaluation failed", error=str(e))
            return {"error": str(e), "ece": 1.0}

    async def evaluate_reasoning_chain(
        self,
        reasoning_steps: list[dict[str, Any]],
        expected_steps: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """
        评估推理链质量

        检查推理链的完整性和逻辑性。

        Args:
            reasoning_steps: 实际推理步骤
            expected_steps: 期望推理步骤

        Returns:
            dict: 推理链评估结果
        """
        try:
            quality_score = reasoning_steps_quality(reasoning_steps, expected_steps)

            # 步骤数量对比
            n_expected = len(expected_steps)
            n_actual = len(reasoning_steps)

            # 步骤覆盖分析
            expected_descriptions = {s.get("description", "") for s in expected_steps}
            actual_descriptions = {s.get("description", "") for s in reasoning_steps}
            covered = len(expected_descriptions & actual_descriptions)

            return {
                "quality_score": round(quality_score, 4),
                "expected_steps": n_expected,
                "actual_steps": n_actual,
                "steps_covered": covered,
                "coverage_rate": round(covered / n_expected, 4) if n_expected > 0 else 0.0,
                "completeness": min(1.0, n_actual / n_expected) if n_expected > 0 else 1.0,
            }

        except Exception as e:
            logger.error("Reasoning chain evaluation failed", error=str(e))
            return {"error": str(e), "quality_score": 0.0}

    async def evaluate_with_rubric(
        self,
        rca_result: dict[str, Any],
        ground_truth: dict[str, Any],
    ) -> dict[str, Any]:
        """
        使用 Rubric 评分标准评估推理质量

        根据预定义的评分标准对推理结果进行多维度评分。

        Args:
            rca_result: RCA 分析结果
            ground_truth: 地面真值

        Returns:
            dict: Rubric 评分结果
        """
        scores: dict[str, Any] = {}

        # 1. 根因识别
        rubric_rc = self._rubric["root_cause_identification"]
        pred_rc = rca_result.get("root_cause", "")
        gt_rc = ground_truth.get("root_cause", "")
        if pred_rc == gt_rc:
            scores["root_cause_identification"] = {
                "score": 1.0,
                "description": rubric_rc["criteria"][0]["desc"],
            }
        elif pred_rc and gt_rc and (
            pred_rc in gt_rc or gt_rc in pred_rc
        ):
            scores["root_cause_identification"] = {
                "score": 0.5,
                "description": rubric_rc["criteria"][1]["desc"],
            }
        else:
            scores["root_cause_identification"] = {
                "score": 0.0,
                "description": rubric_rc["criteria"][2]["desc"],
            }

        # 2. 证据质量
        rubric_ev = self._rubric["evidence_quality"]
        pred_evidence = rca_result.get("evidence", {})
        gt_evidence = ground_truth.get("evidence", {})
        if pred_evidence and gt_evidence:
            ev_score = evidence_completeness(pred_evidence, gt_evidence)
            if ev_score >= 0.7:
                scores["evidence_quality"] = {
                    "score": 1.0,
                    "description": rubric_ev["criteria"][0]["desc"],
                }
            elif ev_score >= 0.3:
                scores["evidence_quality"] = {
                    "score": 0.5,
                    "description": rubric_ev["criteria"][1]["desc"],
                }
            else:
                scores["evidence_quality"] = {
                    "score": 0.0,
                    "description": rubric_ev["criteria"][2]["desc"],
                }
        else:
            scores["evidence_quality"] = {
                "score": 0.0,
                "description": rubric_ev["criteria"][2]["desc"],
            }

        # 3. 置信度校准
        rubric_cal = self._rubric["confidence_calibration"]
        confidence = rca_result.get("confidence", 0.0)
        rca_match = pred_rc == gt_rc
        if rca_match and confidence >= 0.8:
            scores["confidence_calibration"] = {
                "score": 1.0,
                "description": rubric_cal["criteria"][0]["desc"],
            }
        elif (rca_match and confidence >= 0.5) or (not rca_match and confidence < 0.5):
            scores["confidence_calibration"] = {
                "score": 0.5,
                "description": rubric_cal["criteria"][1]["desc"],
            }
        else:
            scores["confidence_calibration"] = {
                "score": 0.0,
                "description": rubric_cal["criteria"][2]["desc"],
            }

        # 4. 影响分析
        rubric_imp = self._rubric["impact_analysis"]
        pred_impact = rca_result.get("impact_chain", [])
        gt_impact = ground_truth.get("impact_chain", [])
        if pred_impact and gt_impact:
            ia = impact_analysis_accuracy(pred_impact, gt_impact)
            if ia["f1"] >= 0.7:
                scores["impact_analysis"] = {
                    "score": 1.0,
                    "description": rubric_imp["criteria"][0]["desc"],
                }
            elif ia["f1"] >= 0.3:
                scores["impact_analysis"] = {
                    "score": 0.5,
                    "description": rubric_imp["criteria"][1]["desc"],
                }
            else:
                scores["impact_analysis"] = {
                    "score": 0.0,
                    "description": rubric_imp["criteria"][2]["desc"],
                }
        else:
            scores["impact_analysis"] = {
                "score": 0.0,
                "description": rubric_imp["criteria"][2]["desc"],
            }

        # 5. 推理链完整性
        rubric_chain = self._rubric["reasoning_chain_completeness"]
        pred_steps = rca_result.get("reasoning_steps", [])
        gt_steps = ground_truth.get("reasoning_steps", [])
        if pred_steps and gt_steps:
            chain_quality = reasoning_steps_quality(pred_steps, gt_steps)
            if chain_quality >= 0.7:
                scores["reasoning_chain_completeness"] = {
                    "score": 1.0,
                    "description": rubric_chain["criteria"][0]["desc"],
                }
            elif chain_quality >= 0.3:
                scores["reasoning_chain_completeness"] = {
                    "score": 0.5,
                    "description": rubric_chain["criteria"][1]["desc"],
                }
            else:
                scores["reasoning_chain_completeness"] = {
                    "score": 0.0,
                    "description": rubric_chain["criteria"][2]["desc"],
                }
        else:
            scores["reasoning_chain_completeness"] = {
                "score": 0.0,
                "description": rubric_chain["criteria"][2]["desc"],
            }

        # 计算平均分
        avg_score = float(np.mean([s["score"] for s in scores.values()]))
        scores["average_score"] = round(avg_score, 4)

        return scores

    async def _load_default_dataset(self) -> list[dict[str, Any]]:
        """加载默认推理测试数据集"""
        return DEFAULT_REASONING_DATASET.copy()

    def get_rubric(self) -> dict[str, dict[str, Any]]:
        """获取评分标准"""
        return self._rubric

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
                f"reasoning_{result.started_at.strftime('%Y%m%d_%H%M%S')}.json"
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

            logger.info("Reasoning evaluation results saved", filepath=str(filepath))
            return str(filepath)

        except Exception as e:
            logger.error("Failed to save reasoning results", error=str(e))
            return ""
