"""
AIOps Agent Platform - End-to-End Evaluation

端到端评估，评估完整故障处理流程的效果。
参考 Agent-World 的持续自进化评估思路，支持 A/B 测试对比不同策略。

评估维度:
- 任务成功率 (Task Success Rate)
- 模拟 MTTR (Mean Time To Repair)
- 自动化率 (Automation Rate)
- 升级率 (Escalation Rate)
- 检测准确率 (Detection Accuracy)
- 误报率 (False Positive Rate)
- 解决率 (Resolution Rate)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np

from app.evaluation.metrics import (
    automation_rate,
    escalation_rate,
    task_success_rate,
    weighted_average,
)
from app.models.evaluation import (
    EvaluationResult,
    EvaluationStatus,
    EvaluationType,
    MetricScore,
)
from app.models.incident import Incident, IncidentState
from app.utils.logging import get_logger

logger = get_logger(__name__)


class EndToEndEvaluator:
    """
    端到端评估器

    评估从告警接收、根因分析到故障修复的完整流程。
    参考 Agent-World 的持续自进化评估思路。

    支持:
    - 单个故障评估 (evaluate_incident)
    - 批量评估 (evaluate_batch)
    - A/B 测试对比 (compare_strategies)
    - 策略对比分析 (analyze_strategy_comparison)
    """

    def __init__(self) -> None:
        self._dataset: list[dict[str, Any]] = []
        self._eval_history: list[EvaluationResult] = []

    async def evaluate(
        self,
        target_agent: str = "",
        dataset_name: str = "",
        samples: list[dict[str, Any]] | None = None,
    ) -> EvaluationResult:
        """
        执行端到端评估

        Args:
            target_agent: 目标 Agent 名称
            dataset_name: 数据集名称
            samples: 自定义测试样本

        Returns:
            EvaluationResult: 评估结果
        """
        result = EvaluationResult(
            evaluation_type=EvaluationType.END_TO_END,
            eval_name=f"end_to_end_{target_agent or 'all'}",
            description="评估完整故障处理流程的效果",
            agent_type=target_agent,
            status=EvaluationStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
        )

        try:
            # 只吃显式样本:此前空样本会回落预烤 actual_result 的内置默认集,
            # 等于自己评自己;样本构建职责在调用方(端点/EvalAgent 从真实
            # incident 产物重建),评测器保持纯函数化。
            test_samples = samples or []
            result.total_samples = len(test_samples)

            if not test_samples:
                result.status = EvaluationStatus.FAILED
                result.errors.append(
                    "No real evaluation samples provided; build samples from "
                    "real incident results (POST /evaluations/run) or pass "
                    "samples containing actual_result explicitly"
                )
                result.completed_at = datetime.now(timezone.utc)
                return result

            # 执行批量评估
            batch_results = await self.evaluate_batch(test_samples)

            # 计算各项指标
            metric_scores = self._compute_metric_scores(batch_results)
            result.metric_scores = metric_scores
            result.overall_score = sum(s.weighted_score for s in metric_scores)

            # 统计样本结果
            result.passed_samples = sum(
                1 for r in batch_results if r.get("success", False)
            )
            result.failed_samples = result.total_samples - result.passed_samples
            result.sample_results = batch_results

            result.status = EvaluationStatus.COMPLETED

            logger.info(
                "End-to-end evaluation completed",
                overall_score=result.overall_score,
                total_samples=result.total_samples,
                passed=result.passed_samples,
            )

        except Exception as e:
            logger.error("End-to-end evaluation failed", error=str(e), exc_info=True)
            result.status = EvaluationStatus.FAILED
            result.errors.append(str(e))

        finally:
            result.completed_at = datetime.now(timezone.utc)
            self._eval_history.append(result)

        return result

    async def evaluate_incident(
        self,
        incident: Incident,
        expected_result: dict[str, Any],
    ) -> dict[str, Any]:
        """
        评估单个故障的处理结果

        完整的端到端评估流程:
        1. 检查故障是否正确识别
        2. 检查根因是否正确
        3. 检查修复动作是否正确
        4. 检查审批流程是否正确
        5. 计算处理时间
        6. 综合评分

        Args:
            incident: 故障实例
            expected_result: 期望结果

        Returns:
            dict: 评估详情
        """
        try:
            # 1. 检查故障是否正确识别 (detection)
            detection_correct = incident.alert_event is not None

            # 2. 检查根因是否正确 (RCA)
            rca_correct = False
            if incident.rca_event and expected_result.get("root_cause"):
                rca_correct = (
                    incident.rca_event.root_cause == expected_result["root_cause"]
                )

            # 3. 检查修复动作是否正确 (heal)
            heal_correct = False
            if incident.heal_events and expected_result.get("action"):
                for heal in incident.heal_events:
                    if heal.action == expected_result["action"]:
                        heal_correct = True
                        break

            # 4. 检查是否已解决
            resolved = incident.state in (
                IncidentState.RESOLVED,
                IncidentState.CLOSED,
            )

            # 5. 检查是否升级
            escalated = incident.state == IncidentState.ESCALATED

            # 6. 检查自动化（无人工介入）
            automated = not escalated and resolved

            # 7. 计算处理时间
            processing_time = 0.0
            if incident.created_at and incident.resolved_at:
                processing_time = (
                    incident.resolved_at - incident.created_at
                ).total_seconds()
            elif incident.metrics and incident.metrics.total_handling_time_seconds > 0:
                processing_time = incident.metrics.total_handling_time_seconds

            # 8. 时间分数 (目标: 2分钟内解决得满分，超过10分钟得0分)
            time_score = self._compute_time_score(processing_time)

            # 9. 综合评分
            score = weighted_average(
                [
                    float(detection_correct),
                    float(rca_correct),
                    float(heal_correct),
                    float(resolved),
                    time_score,
                ],
                weights=[0.15, 0.30, 0.25, 0.15, 0.15],
            )

            return {
                "incident_id": incident.incident_id,
                "detection_correct": detection_correct,
                "rca_correct": rca_correct,
                "heal_correct": heal_correct,
                "resolved": resolved,
                "escalated": escalated,
                "automated": automated,
                "processing_time_seconds": processing_time,
                "time_score": time_score,
                "overall_score": score,
                "success": score >= 0.6,
            }

        except Exception as e:
            logger.error(
                "Incident evaluation failed",
                incident_id=incident.incident_id,
                error=str(e),
                exc_info=True,
            )
            return {
                "incident_id": incident.incident_id,
                "error": str(e),
                "overall_score": 0.0,
                "success": False,
            }

    async def evaluate_batch(
        self, test_samples: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        批量评估多个测试样本

        Args:
            test_samples: 测试样本列表

        Returns:
            list[dict]: 每个样本的评估结果
        """
        results: list[dict[str, Any]] = []

        for sample in test_samples:
            try:
                # 缺 actual_result 的样本明确记错,不进指标计算——
                # 此前空 dict 会让 None==None 的空值比对得满分
                if "actual_result" not in sample:
                    results.append(
                        {
                            "sample_id": sample.get("id", str(uuid4())),
                            "error": "missing actual_result",
                            "success": False,
                        }
                    )
                    continue

                actual = sample.get("actual_result", {})
                expected = sample.get("expected_result", {})

                result = {
                    "sample_id": sample.get("id", str(uuid4())),
                    "name": sample.get("name", ""),
                    "success": actual.get("resolved", False),
                    "automated": actual.get("automated", False),
                    "escalated": actual.get("escalated", False),
                    "resolved": actual.get("resolved", False),
                    # 双方都必须真给出值才算命中,空对空不算
                    "root_cause_match": bool(
                        actual.get("root_cause")
                        and actual.get("root_cause") == expected.get("root_cause")
                    ),
                    "action_match": bool(
                        actual.get("action")
                        and actual.get("action") == expected.get("action")
                    ),
                    "processing_time": actual.get("time_to_resolve_seconds", 0),
                    "detection_correct": (
                        actual.get("is_anomaly", False)
                        == sample.get("is_anomaly", False)
                    ),
                }
                results.append(result)

            except Exception as e:
                logger.error(
                    "Sample evaluation failed",
                    sample=sample.get("id", "unknown"),
                    error=str(e),
                )
                results.append(
                    {
                        "sample_id": sample.get("id", str(uuid4())),
                        "error": str(e),
                        "success": False,
                    }
                )

        return results

    async def compare_strategies(
        self,
        strategy_a_results: list[dict[str, Any]],
        strategy_b_results: list[dict[str, Any]],
        strategy_a_name: str = "Strategy A",
        strategy_b_name: str = "Strategy B",
    ) -> dict[str, Any]:
        """
        A/B 测试 - 对比两种策略的效果

        参考 Agent-World 的持续自进化评估思路，
        对比不同策略在相同测试集上的表现。

        Args:
            strategy_a_results: 策略A的结果列表
            strategy_b_results: 策略B的结果列表
            strategy_a_name: 策略A名称
            strategy_b_name: 策略B名称

        Returns:
            dict: 对比分析结果
        """
        # 计算各项指标
        a_success = task_success_rate(strategy_a_results)
        b_success = task_success_rate(strategy_b_results)

        a_automation = automation_rate(strategy_a_results)
        b_automation = automation_rate(strategy_b_results)

        a_escalation = escalation_rate(strategy_a_results)
        b_escalation = escalation_rate(strategy_b_results)

        # 处理时间对比
        a_times = [
            r.get("processing_time", 0)
            for r in strategy_a_results
            if r.get("processing_time", 0) > 0
        ]
        b_times = [
            r.get("processing_time", 0)
            for r in strategy_b_results
            if r.get("processing_time", 0) > 0
        ]

        a_avg_time = float(np.mean(a_times)) if a_times else 0.0
        b_avg_time = float(np.mean(b_times)) if b_times else 0.0

        # 综合评分
        a_overall = weighted_average(
            [a_success, a_automation, 1.0 - a_escalation],
            weights=[0.5, 0.25, 0.25],
        )
        b_overall = weighted_average(
            [b_success, b_automation, 1.0 - b_escalation],
            weights=[0.5, 0.25, 0.25],
        )

        comparison = {
            "strategy_a": {
                "name": strategy_a_name,
                "task_success_rate": round(a_success, 4),
                "automation_rate": round(a_automation, 4),
                "escalation_rate": round(a_escalation, 4),
                "avg_processing_time": round(a_avg_time, 2),
                "overall_score": round(a_overall, 4),
            },
            "strategy_b": {
                "name": strategy_b_name,
                "task_success_rate": round(b_success, 4),
                "automation_rate": round(b_automation, 4),
                "escalation_rate": round(b_escalation, 4),
                "avg_processing_time": round(b_avg_time, 2),
                "overall_score": round(b_overall, 4),
            },
            "winner": strategy_a_name if a_overall > b_overall else strategy_b_name,
            "improvement": round(
                abs(a_overall - b_overall) / max(b_overall, 0.001), 4
            ),
            "significant": abs(a_overall - b_overall) > 0.05,
        }

        logger.info(
            "Strategy comparison completed",
            winner=comparison["winner"],
            improvement=comparison["improvement"],
        )

        return comparison

    def _compute_metric_scores(
        self, batch_results: list[dict[str, Any]]
    ) -> list[MetricScore]:
        """
        根据批量评估结果计算各项指标得分

        Args:
            batch_results: 批量评估结果

        Returns:
            list[MetricScore]: 指标得分列表
        """
        if not batch_results:
            return []

        # 任务成功率
        tsr = task_success_rate(batch_results)
        rca_accuracy = sum(
            1 for r in batch_results if r.get("root_cause_match", False)
        ) / len(batch_results)
        action_accuracy = sum(
            1 for r in batch_results if r.get("action_match", False)
        ) / len(batch_results)
        automation = automation_rate(batch_results)
        escalation = escalation_rate(batch_results)
        detection = sum(
            1 for r in batch_results if r.get("detection_correct", False)
        ) / len(batch_results)

        # 处理时间分数
        processing_times = [
            r.get("processing_time", 0)
            for r in batch_results
            if r.get("processing_time", 0) > 0
        ]
        avg_time = float(np.mean(processing_times)) if processing_times else 0.0
        time_score = self._compute_time_score(avg_time)

        return [
            MetricScore(
                metric_name="task_success_rate",
                score=tsr,
                weight=0.25,
                details={"description": "任务成功率", "sample_count": len(batch_results)},
            ),
            MetricScore(
                metric_name="rca_accuracy",
                score=rca_accuracy,
                weight=0.20,
                details={"description": "根因分析准确性"},
            ),
            MetricScore(
                metric_name="action_accuracy",
                score=action_accuracy,
                weight=0.15,
                details={"description": "修复动作准确性"},
            ),
            MetricScore(
                metric_name="automation_rate",
                score=automation,
                weight=0.15,
                details={"description": "自动化率"},
            ),
            MetricScore(
                metric_name="escalation_rate",
                score=1.0 - escalation,  # 升级率越低越好
                weight=0.10,
                details={
                    "description": "低升级率（越低越好）",
                    "raw_escalation_rate": escalation,
                },
            ),
            MetricScore(
                metric_name="handling_time_score",
                score=time_score,
                weight=0.10,
                details={
                    "description": "处理时间得分",
                    "avg_processing_time_seconds": round(avg_time, 2),
                },
            ),
            MetricScore(
                metric_name="detection_accuracy",
                score=detection,
                weight=0.05,
                details={"description": "检测准确率"},
            ),
        ]

    @staticmethod
    def _compute_time_score(processing_time: float) -> float:
        """
        计算处理时间分数

        - 2分钟内(120s): 满分 1.0
        - 2-5分钟(120-300s): 线性递减到 0.7
        - 5-10分钟(300-600s): 线性递减到 0.3
        - 超过10分钟(600s): 0.0

        Args:
            processing_time: 处理时间(秒)

        Returns:
            float: 时间分数 (0.0 - 1.0)
        """
        if processing_time <= 120:
            return 1.0
        elif processing_time <= 300:
            return 1.0 - (processing_time - 120) / 180 * 0.3
        elif processing_time <= 600:
            return 0.7 - (processing_time - 300) / 300 * 0.4
        else:
            return max(0.0, 0.3 - (processing_time - 600) / 600 * 0.3)

    def get_eval_history(self) -> list[EvaluationResult]:
        """获取评估历史"""
        return self._eval_history

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

            filename = f"end_to_end_{result.started_at.strftime('%Y%m%d_%H%M%S')}.json"
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
                "sample_results": result.sample_results,
            }

            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            logger.info("Evaluation results saved", filepath=str(filepath))
            return str(filepath)

        except Exception as e:
            logger.error("Failed to save results", error=str(e))
            return ""
