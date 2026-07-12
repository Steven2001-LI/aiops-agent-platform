"""
AIOps Agent Platform - Eval Agent

评估 Agent，负责对 AIOps 系统进行端到端评估。
涵盖任务成功率、推理准确率、工具调用评估、RAG 评估和报告生成。

集成完整的四维度评估框架:
- 端到端评估 (End-to-End)
- 推理评估 (Reasoning)
- 工具调用评估 (Tool Call)
- RAG 评估 (RAG)

支持:
- 手动评估请求
- 自动评估（在处理完成后自动触发）
- 评估报告生成
- 历史评估对比
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from app.agents.base import AgentResult, BaseAgent
from app.evaluation.core import get_evaluation_framework
from app.evaluation.end_to_end import EndToEndEvaluator
from app.evaluation.metrics import (
    accuracy,
    automation_rate,
    confidence_calibration,
    escalation_rate,
    false_positive_rate,
    mae,
    mse,
    mttr_simulated,
    precision_recall_f1,
    resolution_rate,
    retrieval_precision,
    retrieval_recall,
    root_cause_accuracy,
    task_success_rate,
    tool_execution_success,
    tool_selection_accuracy,
    weighted_average,
)
from app.evaluation.rag_eval import RAGEvaluator
from app.evaluation.reasoning_eval import ReasoningEvaluator
from app.evaluation.tool_call_eval import ToolCallEvaluator
from app.models.agent import AgentExecutionContext
from app.models.evaluation import (
    BenchmarkReport,
    EvaluationResult,
    EvaluationStatus,
    EvaluationType,
)
from app.models.events import (
    AlertEvent,
    ApprovalStatus,
    AuditEvent,
    EventStatus,
    HealEvent,
    RCAEvent,
    SeverityLevel,
)
from app.models.incident import Incident, IncidentMetrics, IncidentPhase, IncidentState
from app.utils.logging import get_logger

logger = get_logger(__name__)


class EvalType(str, Enum):
    """评估类型"""
    END_TO_END = "end_to_end"
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"
    RAG = "rag"
    FULL = "full"


class EvalInput(BaseModel):
    """Eval Agent 输入"""

    eval_type: EvalType = Field(default=EvalType.FULL, description="评估类型")
    incident_id: str = Field(default="", description="故障ID（可选）")
    ground_truth: dict[str, Any] = Field(
        default_factory=dict, description="地面真值数据"
    )
    test_cases: list[dict[str, Any]] = Field(
        default_factory=list, description="测试用例"
    )
    agent_results: list[dict[str, Any]] = Field(
        default_factory=list, description="Agent 执行结果"
    )
    # 新增的评估配置
    target_agent: str = Field(default="", description="目标 Agent 名称")
    auto_trigger: bool = Field(
        default=False, description="是否为自动触发（故障处理后自动评估）"
    )
    samples_by_type: dict[str, list[dict[str, Any]]] = Field(
        default_factory=dict, description="按类型的自定义样本"
    )


class EndToEndMetrics(BaseModel):
    """端到端评估指标"""

    task_success_rate: float = Field(default=0.0, description="任务成功率")
    mttr_simulated_seconds: float = Field(default=0.0, description="模拟 MTTR(秒)")
    detection_accuracy: float = Field(default=0.0, description="检测准确率")
    resolution_rate: float = Field(default=0.0, description="解决率")
    escalation_rate: float = Field(default=0.0, description="升级率")
    false_positive_rate: float = Field(default=0.0, description="误报率")
    automation_rate: float = Field(default=0.0, description="自动化率")
    total_test_cases: int = Field(default=0, description="总测试用例数")


class ReasoningMetrics(BaseModel):
    """推理评估指标"""

    root_cause_accuracy: float = Field(default=0.0, description="根因准确率")
    confidence_calibration_error: float = Field(default=0.0, description="置信度校准误差")
    confidence_calibration_score: float = Field(default=0.0, description="置信度校准得分")
    impact_chain_precision: float = Field(default=0.0, description="影响链精确率")
    impact_chain_recall: float = Field(default=0.0, description="影响链召回率")
    impact_chain_f1: float = Field(default=0.0, description="影响链 F1")
    evidence_completeness: float = Field(default=0.0, description="证据完整性")
    reasoning_chain_quality: float = Field(default=0.0, description="推理链质量")
    suggested_action_accuracy: float = Field(default=0.0, description="建议操作准确率")
    mean_confidence: float = Field(default=0.0, description="平均置信度")


class ToolCallMetrics(BaseModel):
    """工具调用评估指标"""

    tool_selection_accuracy: float = Field(default=0.0, description="工具选择准确率")
    parameter_accuracy: float = Field(default=0.0, description="参数正确率")
    execution_success_rate: float = Field(default=0.0, description="执行成功率")
    call_sequence_quality: float = Field(default=0.0, description="调用序列质量")
    tool_call_efficiency: float = Field(default=0.0, description="调用效率")
    intervention_validation_rate: float = Field(default=0.0, description="干预验证率")
    average_tool_calls: float = Field(default=0.0, description="平均工具调用次数")
    tool_call_distribution: dict[str, int] = Field(
        default_factory=dict, description="工具调用分布"
    )


class RAGMetrics(BaseModel):
    """RAG 评估指标"""

    retrieval_precision: float = Field(default=0.0, description="检索精确率")
    retrieval_recall: float = Field(default=0.0, description="检索召回率")
    retrieval_f1: float = Field(default=0.0, description="检索 F1")
    context_relevance: float = Field(default=0.0, description="上下文相关性")
    context_sufficiency: float = Field(default=0.0, description="上下文充分性")
    answer_faithfulness: float = Field(default=0.0, description="答案忠实度")
    answer_relevance: float = Field(default=0.0, description="回答相关性")
    mean_retrieval_score: float = Field(default=0.0, description="平均检索得分")


class EvalReport(BaseModel):
    """评估报告"""

    report_id: str = Field(
        default_factory=lambda: f"eval-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
    )
    eval_type: str = Field(default="")
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    end_to_end: EndToEndMetrics = Field(default_factory=EndToEndMetrics)
    reasoning: ReasoningMetrics = Field(default_factory=ReasoningMetrics)
    tool_call: ToolCallMetrics = Field(default_factory=ToolCallMetrics)
    rag: RAGMetrics = Field(default_factory=RAGMetrics)
    overall_score: float = Field(default=0.0, description="综合得分")
    recommendations: list[str] = Field(default_factory=list, description="改进建议")
    raw_details: dict[str, Any] = Field(default_factory=dict, description="原始详情")
    # 新增字段
    benchmark_summary: dict[str, Any] = Field(
        default_factory=dict, description="基准测试汇总"
    )
    eval_results: list[dict[str, Any]] = Field(
        default_factory=list, description="各维度详细评估结果"
    )


class EvalAgent(BaseAgent[EvalInput, EvalReport]):
    """
    评估 Agent

    职责：
    - 端到端评估（任务成功率、MTTR 模拟）
    - 推理评估（根因准确率、置信度校准）
    - 工具调用评估（工具选择准确率、参数正确率）
    - RAG 评估（检索准确率、上下文相关性）
    - 生成评估报告
    - 历史评估追踪
    """

    def __init__(self) -> None:
        super().__init__()
        # 历史评估结果
        self._eval_history: list[EvalReport] = []
        # 子评估器
        self._end_to_end_evaluator = EndToEndEvaluator()
        self._reasoning_evaluator = ReasoningEvaluator()
        self._tool_call_evaluator = ToolCallEvaluator()
        self._rag_evaluator = RAGEvaluator()
        # 框架核心
        self._framework = get_evaluation_framework()

    def get_name(self) -> str:
        return "eval_agent"

    def get_description(self) -> str:
        return "评估 Agent - 端到端/推理/工具调用/RAG 多维评估"

    # ==================== 核心处理 ====================

    async def process(
        self,
        input_data: EvalInput,
        context: AgentExecutionContext,
    ) -> AgentResult:
        """
        执行评估

        Args:
            input_data: 评估输入
            context: 执行上下文

        Returns:
            AgentResult: 包含 EvalReport 的结果
        """
        logger.info(
            "EvalAgent processing",
            eval_type=input_data.eval_type.value,
            test_cases=len(input_data.test_cases),
            auto_trigger=input_data.auto_trigger,
        )

        report = EvalReport(eval_type=input_data.eval_type.value)
        eval_results: list[EvaluationResult] = []

        try:
            # 根据评估类型执行不同评估
            if input_data.eval_type in (EvalType.END_TO_END, EvalType.FULL):
                report.end_to_end = self._eval_end_to_end(input_data)
                # 同时通过框架执行标准化评估
                e2e_result = await self._framework.evaluate(
                    EvaluationType.END_TO_END,
                    target_agent=input_data.target_agent,
                    samples=input_data.samples_by_type.get("end_to_end")
                    or input_data.test_cases,
                )
                eval_results.append(e2e_result)

            if input_data.eval_type in (EvalType.REASONING, EvalType.FULL):
                report.reasoning = self._eval_reasoning(input_data)
                reasoning_result = await self._framework.evaluate(
                    EvaluationType.REASONING,
                    target_agent=input_data.target_agent,
                    samples=input_data.samples_by_type.get("reasoning"),
                )
                eval_results.append(reasoning_result)

            if input_data.eval_type in (EvalType.TOOL_CALL, EvalType.FULL):
                report.tool_call = self._eval_tool_call(input_data)
                tool_result = await self._framework.evaluate(
                    EvaluationType.TOOL_CALL,
                    target_agent=input_data.target_agent,
                    samples=input_data.samples_by_type.get("tool_call"),
                )
                eval_results.append(tool_result)

            if input_data.eval_type in (EvalType.RAG, EvalType.FULL):
                report.rag = self._eval_rag(input_data)
                rag_result = await self._framework.evaluate(
                    EvaluationType.RAG,
                    target_agent=input_data.target_agent,
                    samples=input_data.samples_by_type.get("rag"),
                )
                eval_results.append(rag_result)

            # 计算综合得分
            report.overall_score = self._calculate_overall_score(report)

            # 生成改进建议
            report.recommendations = self._generate_recommendations(report)

            # 保存各维度详细结果
            report.eval_results = [
                {
                    "eval_type": r.evaluation_type.value,
                    "overall_score": r.overall_score,
                    "status": r.status.value,
                    "metrics": [
                        {
                            "name": m.metric_name,
                            "score": round(m.score, 4),
                            "weight": m.weight,
                        }
                        for m in r.metric_scores
                    ],
                }
                for r in eval_results
            ]

            # 基准测试汇总
            if eval_results:
                report.benchmark_summary = {
                    "avg_score": round(
                        float(np.mean([r.overall_score for r in eval_results])), 4
                    ),
                    "min_score": round(
                        min(r.overall_score for r in eval_results), 4
                    ),
                    "max_score": round(
                        max(r.overall_score for r in eval_results), 4
                    ),
                    "total_evaluations": len(eval_results),
                    "dimensions": {
                        r.evaluation_type.value: round(r.overall_score, 4)
                        for r in eval_results
                    },
                }

            # 保存历史
            self._eval_history.append(report)

            logger.info(
                "Evaluation completed",
                eval_type=input_data.eval_type.value,
                overall_score=report.overall_score,
            )

            output_data = {
                "report": report.model_dump(),
                "overall_score": report.overall_score,
                "recommendations": report.recommendations,
                "benchmark_summary": report.benchmark_summary,
            }

            return AgentResult.success_result(
                agent_name=self.get_name(),
                output_data=output_data,
            )

        except Exception as e:
            logger.error("EvalAgent processing failed", error=str(e), exc_info=True)
            return AgentResult.failure_result(
                agent_name=self.get_name(),
                error_message=f"Evaluation failed: {str(e)}",
            )

    # ==================== 自动评估 ====================

    async def auto_evaluate_after_processing(
        self,
        incident: Incident,
        expected_result: dict[str, Any],
        context: AgentExecutionContext,
    ) -> EvalReport | None:
        """
        故障处理后自动触发评估

        在 Incident 处理完成后自动调用评估。

        Args:
            incident: 处理完成的故障实例
            expected_result: 期望结果
            context: 执行上下文

        Returns:
            EvalReport | None: 评估报告（评估失败返回 None）
        """
        try:
            logger.info(
                "Auto-evaluation triggered",
                incident_id=incident.incident_id,
            )

            # 构建端到端测试用例
            test_case = self._incident_to_test_case(incident, expected_result)

            input_data = EvalInput(
                eval_type=EvalType.FULL,
                incident_id=incident.incident_id,
                ground_truth=expected_result,
                test_cases=[test_case],
                auto_trigger=True,
            )

            result = await self.process(input_data, context)

            if result.success:
                report_data = result.output_data.get("report", {})
                # 从 dict 重建 EvalReport
                return EvalReport(**report_data)

            return None

        except Exception as e:
            logger.error(
                "Auto-evaluation failed",
                incident_id=incident.incident_id,
                error=str(e),
            )
            return None

    # ==================== 端到端评估 ====================

    def _eval_end_to_end(self, input_data: EvalInput) -> EndToEndMetrics:
        """
        端到端评估

        评估完整故障处理流程的成功率、MTTR 等指标。
        """
        test_cases = input_data.test_cases
        agent_results = input_data.agent_results

        if not test_cases:
            test_cases = self._get_default_test_cases()

        total = len(test_cases)
        if total == 0:
            return EndToEndMetrics()

        successes = 0
        mttrs: list[float] = []
        detections_correct = 0
        resolutions = 0
        escalations = 0
        false_positives = 0
        automated = 0

        for case in test_cases:
            case_result = case.get("expected_result", {})
            actual_result = case.get("actual_result", {})

            # 任务成功
            if actual_result.get("resolved", False):
                successes += 1

            # MTTR
            if actual_result.get("time_to_resolve_seconds"):
                mttrs.append(actual_result["time_to_resolve_seconds"])

            # 检测准确
            expected_anomaly = case.get("is_anomaly", False)
            detected = actual_result.get("is_anomaly", False)
            if expected_anomaly == detected:
                detections_correct += 1

            # 误报
            if not expected_anomaly and detected:
                false_positives += 1

            # 解决
            if actual_result.get("state") in ("resolved", "closed"):
                resolutions += 1

            # 升级
            if actual_result.get("state") == "escalated":
                escalations += 1

            # 自动化
            if actual_result.get("automated", False):
                automated += 1

        mttr = float(np.mean(mttrs)) if mttrs else 0.0

        return EndToEndMetrics(
            task_success_rate=successes / total if total > 0 else 0.0,
            mttr_simulated_seconds=round(mttr, 2),
            detection_accuracy=detections_correct / total if total > 0 else 0.0,
            resolution_rate=resolutions / total if total > 0 else 0.0,
            escalation_rate=escalations / total if total > 0 else 0.0,
            false_positive_rate=false_positives / total if total > 0 else 0.0,
            automation_rate=automated / total if total > 0 else 0.0,
            total_test_cases=total,
        )

    # ==================== 推理评估 ====================

    def _eval_reasoning(self, input_data: EvalInput) -> ReasoningMetrics:
        """
        推理评估

        评估根因分析 Agent 的推理质量。
        """
        ground_truth = input_data.ground_truth
        agent_results = input_data.agent_results

        if not ground_truth and not agent_results:
            raise ValueError(
                "Cannot evaluate reasoning: both ground_truth and agent_results are empty. "
                "Provide real agent_results from a completed incident, or pass ground_truth for offline evaluation."
            )

        # 提取 RCA 结果
        rca_results = [
            r for r in agent_results if r.get("agent_name") == "rca_agent"
        ]

        if not rca_results:
            return ReasoningMetrics()

        # 根因准确率
        correct_causes = sum(
            1
            for r in rca_results
            if r.get("output_data", {}).get("root_cause")
            == ground_truth.get("root_cause")
        )
        root_cause_acc = correct_causes / len(rca_results)

        # 置信度校准
        confidences: list[float] = []
        accuracies: list[bool] = []
        for r in rca_results:
            conf = r.get("output_data", {}).get("confidence", 0)
            if conf is not None:
                confidences.append(conf)
                accuracies.append(
                    r.get("output_data", {}).get("root_cause")
                    == ground_truth.get("root_cause")
                )

        calibration_error = 0.0
        calibration_score = 0.0
        if confidences and accuracies:
            calibration_error = confidence_calibration(confidences, accuracies)
            calibration_score = max(0.0, 1.0 - calibration_error * 3)

        # 影响链评估
        pred_chain = (
            rca_results[0].get("output_data", {}).get("impact_chain", [])
            if rca_results
            else []
        )
        true_chain = ground_truth.get("impact_chain", [])
        if pred_chain and true_chain:
            pred_set = set(pred_chain)
            true_set = set(true_chain)
            intersection = len(pred_set & true_set)
            precision = intersection / len(pred_set) if pred_set else 0.0
            recall = intersection / len(true_set) if true_set else 0.0
            f1 = (
                2 * precision * recall / (precision + recall)
                if (precision + recall) > 0
                else 0.0
            )
        else:
            precision = recall = f1 = 0.0

        # 建议操作准确率
        pred_actions = set(
            rca_results[0].get("output_data", {}).get("suggested_actions", [])
        ) if rca_results else set()
        true_actions = set(ground_truth.get("suggested_actions", []))
        action_acc = (
            len(pred_actions & true_actions) / len(true_actions)
            if true_actions
            else 0.0
        )

        # 证据完整性
        pred_evidence = (
            rca_results[0].get("output_data", {}).get("evidence", {})
            if rca_results
            else {}
        )
        true_evidence = ground_truth.get("evidence", {})
        if pred_evidence and true_evidence:
            from app.evaluation.metrics import evidence_completeness
            ev_score = evidence_completeness(pred_evidence, true_evidence)
        else:
            ev_score = 0.0

        # 推理链质量
        pred_steps = (
            rca_results[0].get("output_data", {}).get("reasoning_steps", [])
            if rca_results
            else []
        )
        true_steps = ground_truth.get("reasoning_steps", [])
        if pred_steps and true_steps:
            from app.evaluation.metrics import reasoning_steps_quality
            chain_score = reasoning_steps_quality(pred_steps, true_steps)
        else:
            chain_score = 0.0

        return ReasoningMetrics(
            root_cause_accuracy=round(root_cause_acc, 4),
            confidence_calibration_error=round(calibration_error, 4),
            confidence_calibration_score=round(calibration_score, 4),
            impact_chain_precision=round(precision, 4),
            impact_chain_recall=round(recall, 4),
            impact_chain_f1=round(f1, 4),
            evidence_completeness=round(ev_score, 4),
            reasoning_chain_quality=round(chain_score, 4),
            suggested_action_accuracy=round(action_acc, 4),
            mean_confidence=round(float(np.mean(confidences)), 4) if confidences else 0.0,
        )

    # ==================== 工具调用评估 ====================

    def _eval_tool_call(self, input_data: EvalInput) -> ToolCallMetrics:
        """
        工具调用评估

        评估 Agent 工具选择和参数填写的准确性。
        """
        agent_results = input_data.agent_results

        if not agent_results:
            return ToolCallMetrics()

        # 统计工具调用
        tool_calls: dict[str, int] = {}
        correct_selections = 0
        correct_params = 0
        successful_executions = 0
        total_tool_calls = 0
        correct_sequences = 0

        for result in agent_results:
            tools_used = result.get("output_data", {}).get("tools_used", [])
            for tool in tools_used:
                total_tool_calls += 1
                tool_name = tool.get("name", "unknown")
                tool_calls[tool_name] = tool_calls.get(tool_name, 0) + 1

                if tool.get("selection_correct", False):
                    correct_selections += 1
                if tool.get("params_correct", False):
                    correct_params += 1
                if tool.get("execution_success", False):
                    successful_executions += 1
                if tool.get("sequence_correct", False):
                    correct_sequences += 1

        n = total_tool_calls if total_tool_calls > 0 else 1

        # 计算干预验证率（如果可用）
        intervention_validations = sum(
            1
            for r in agent_results
            for t in r.get("output_data", {}).get("tools_used", [])
            if t.get("intervention_validated", False)
        )
        intervention_total = sum(
            1
            for r in agent_results
            for t in r.get("output_data", {}).get("tools_used", [])
            if t.get("intervention_tested", False)
        )
        intervention_rate = (
            intervention_validations / intervention_total
            if intervention_total > 0
            else 0.0
        )

        return ToolCallMetrics(
            tool_selection_accuracy=correct_selections / n,
            parameter_accuracy=correct_params / n,
            execution_success_rate=successful_executions / n,
            call_sequence_quality=correct_sequences / n if total_tool_calls > 0 else 0.0,
            tool_call_efficiency=min(1.0, correct_selections / max(n, 1)),
            intervention_validation_rate=intervention_rate,
            average_tool_calls=total_tool_calls / len(agent_results)
            if agent_results
            else 0.0,
            tool_call_distribution=tool_calls,
        )

    # ==================== RAG 评估 ====================

    def _eval_rag(self, input_data: EvalInput) -> RAGMetrics:
        """
        RAG 评估

        评估知识库检索的准确性和相关性。
        """
        test_cases = input_data.test_cases

        if not test_cases:
            return RAGMetrics()

        precisions: list[float] = []
        recalls: list[float] = []
        relevances: list[float] = []
        sufficiencies: list[float] = []
        faithfulness_scores: list[float] = []
        answer_relevances: list[float] = []
        scores: list[float] = []

        for case in test_cases:
            retrieved = case.get("retrieved_docs", [])
            relevant = set(case.get("relevant_docs", []))
            query = case.get("query", "")
            answer = case.get("generated_answer", "")

            if not retrieved:
                continue

            retrieved_ids = set(r.get("id", "") for r in retrieved)
            retrieved_scores = [r.get("score", 0) for r in retrieved]
            contexts = [r.get("content", "") for r in retrieved if r.get("content")]

            # 精确率
            if retrieved_ids:
                prec = retrieval_precision(list(retrieved_ids), list(relevant))
                precisions.append(prec)

            # 召回率
            if relevant:
                rec = retrieval_recall(list(retrieved_ids), list(relevant))
                recalls.append(rec)

            # 相关性
            if contexts and query:
                rel = context_relevance(contexts, query)
                relevances.append(rel)

            # 充分性
            if contexts and query:
                suff = context_sufficiency(contexts, query)
                sufficiencies.append(suff)

            # 忠实度
            if answer and contexts:
                faith = answer_faithfulness(answer, contexts)
                faithfulness_scores.append(faith)

            # 回答相关性
            if answer and query:
                ans_rel = answer_relevance(answer, query)
                answer_relevances.append(ans_rel)

            scores.extend(retrieved_scores)

        n_prec = len(precisions) if precisions else 1
        n_rec = len(recalls) if recalls else 1

        avg_precision = float(np.mean(precisions)) if precisions else 0.0
        avg_recall = float(np.mean(recalls)) if recalls else 0.0
        retrieval_f1 = (
            2 * avg_precision * avg_recall / (avg_precision + avg_recall)
            if (avg_precision + avg_recall) > 0
            else 0.0
        )

        return RAGMetrics(
            retrieval_precision=round(avg_precision, 4),
            retrieval_recall=round(avg_recall, 4),
            retrieval_f1=round(retrieval_f1, 4),
            context_relevance=round(float(np.mean(relevances)), 4)
            if relevances
            else 0.0,
            context_sufficiency=round(float(np.mean(sufficiencies)), 4)
            if sufficiencies
            else 0.0,
            answer_faithfulness=round(float(np.mean(faithfulness_scores)), 4)
            if faithfulness_scores
            else 0.0,
            answer_relevance=round(float(np.mean(answer_relevances)), 4)
            if answer_relevances
            else 0.0,
            mean_retrieval_score=round(float(np.mean(scores)), 4) if scores else 0.0,
        )

    # ==================== 综合评分 ====================

    def _calculate_overall_score(self, report: EvalReport) -> float:
        """
        计算综合得分

        加权平均各维度得分。
        """
        weights = {
            "end_to_end": 0.30,
            "reasoning": 0.30,
            "tool_call": 0.20,
            "rag": 0.20,
        }

        scores = [
            weights["end_to_end"]
            * (
                report.end_to_end.task_success_rate * 0.4
                + report.end_to_end.detection_accuracy * 0.3
                + report.end_to_end.automation_rate * 0.2
                + (1 - report.end_to_end.false_positive_rate) * 0.1
            ),
            weights["reasoning"]
            * (
                report.reasoning.root_cause_accuracy * 0.4
                + (1 - report.reasoning.confidence_calibration_error) * 0.25
                + report.reasoning.impact_chain_f1 * 0.15
                + report.reasoning.reasoning_chain_quality * 0.1
                + report.reasoning.evidence_completeness * 0.1
            ),
            weights["tool_call"]
            * (
                report.tool_call.tool_selection_accuracy * 0.35
                + report.tool_call.parameter_accuracy * 0.25
                + report.tool_call.execution_success_rate * 0.25
                + report.tool_call.tool_call_efficiency * 0.15
            ),
            weights["rag"]
            * (
                report.rag.retrieval_f1 * 0.35
                + report.rag.answer_faithfulness * 0.25
                + report.rag.context_relevance * 0.2
                + report.rag.context_sufficiency * 0.2
            ),
        ]

        return round(sum(scores), 4)

    def _generate_recommendations(self, report: EvalReport) -> list[str]:
        """生成改进建议"""
        recommendations: list[str] = []

        # 端到端建议
        if report.end_to_end.task_success_rate < 0.8:
            recommendations.append(
                f"[End-to-End] Task success rate ({report.end_to_end.task_success_rate:.1%}) "
                f"is below target (80%). Consider improving playbook coverage, "
                f"enhancing error handling, and adding more self-healing scenarios."
            )
        if report.end_to_end.false_positive_rate > 0.1:
            recommendations.append(
                f"[End-to-End] False positive rate ({report.end_to_end.false_positive_rate:.1%}) "
                f"is high. Review anomaly detection thresholds and tune sensitivity."
            )
        if report.end_to_end.automation_rate < 0.6:
            recommendations.append(
                f"[End-to-End] Automation rate ({report.end_to_end.automation_rate:.1%}) "
                f"is low. Review manual intervention points and automate common recovery."
            )

        # 推理建议
        if report.reasoning.root_cause_accuracy < 0.7:
            recommendations.append(
                f"[Reasoning] Root cause accuracy ({report.reasoning.root_cause_accuracy:.1%}) "
                f"needs improvement. Expand knowledge base and refine Bayesian priors."
            )
        if report.reasoning.confidence_calibration_error > 0.2:
            recommendations.append(
                f"[Reasoning] Confidence calibration error "
                f"({report.reasoning.confidence_calibration_error:.2f}) "
                f"is significant. Implement temperature scaling for calibration."
            )
        if report.reasoning.reasoning_chain_quality < 0.6:
            recommendations.append(
                f"[Reasoning] Reasoning chain quality "
                f"({report.reasoning.reasoning_chain_quality:.1%}) "
                f"is low. Improve CoT prompting and add validation steps."
            )

        # 工具调用建议
        if report.tool_call.tool_selection_accuracy < 0.8:
            recommendations.append(
                f"[Tool Call] Tool selection accuracy "
                f"({report.tool_call.tool_selection_accuracy:.1%}) "
                f"can be improved. Add more training examples for tool selection."
            )
        if report.tool_call.parameter_accuracy < 0.8:
            recommendations.append(
                f"[Tool Call] Parameter accuracy ({report.tool_call.parameter_accuracy:.1%}) "
                f"needs work. Improve parameter schema understanding."
            )
        if report.tool_call.intervention_validation_rate > 0:
            recommendations.append(
                f"[Tool Call] Intervention validation rate "
                f"({report.tool_call.intervention_validation_rate:.1%}). "
                f"Use DoVer-style verification to optimize tool selection."
            )

        # RAG 建议
        if report.rag.retrieval_precision < 0.7:
            recommendations.append(
                f"[RAG] Retrieval precision ({report.rag.retrieval_precision:.1%}) "
                f"is low. Consider using better embedding models or adding metadata."
            )
        if report.rag.retrieval_recall < 0.6:
            recommendations.append(
                f"[RAG] Retrieval recall ({report.rag.retrieval_recall:.1%}) "
                f"is low. Increase top-k values or use hybrid search."
            )
        if report.rag.answer_faithfulness < 0.7:
            recommendations.append(
                f"[RAG] Answer faithfulness ({report.rag.answer_faithfulness:.1%}) "
                f"needs improvement. Implement groundedness checks."
            )

        if not recommendations:
            recommendations.append(
                "All metrics are within acceptable ranges. System is performing well."
            )

        return recommendations

    # ==================== 辅助方法 ====================

    @staticmethod
    def _incident_to_test_case(
        incident: Incident, expected_result: dict[str, Any]
    ) -> dict[str, Any]:
        """将故障实例转换为测试用例"""
        return {
            "id": incident.incident_id,
            "name": incident.title,
            "is_anomaly": incident.state not in (IncidentState.CANCELLED,),
            "expected_result": expected_result,
            "actual_result": {
                "resolved": incident.is_resolved,
                "is_anomaly": incident.state not in (IncidentState.CANCELLED,),
                "time_to_resolve_seconds": incident.metrics.total_handling_time_seconds
                if incident.metrics
                else 0,
                "state": incident.state.value if incident.state else "",
                "root_cause": (
                    incident.rca_event.root_cause if incident.rca_event else ""
                ),
                "action": (
                    incident.heal_events[0].action
                    if incident.heal_events
                    else ""
                ),
                "automated": incident.state
                in (IncidentState.RESOLVED, IncidentState.CLOSED),
            },
        }

    @staticmethod
    def _get_default_test_cases() -> list[dict[str, Any]]:
        """获取默认测试用例"""
        return [
            {
                "name": "cpu_high_auto_heal",
                "is_anomaly": True,
                "expected_result": {"root_cause": "traffic_spike", "action": "scale_up"},
                "actual_result": {
                    "resolved": True,
                    "is_anomaly": True,
                    "time_to_resolve_seconds": 120,
                    "state": "resolved",
                    "automated": True,
                },
            },
            {
                "name": "memory_leak_restart",
                "is_anomaly": True,
                "expected_result": {"root_cause": "memory_leak", "action": "restart"},
                "actual_result": {
                    "resolved": True,
                    "is_anomaly": True,
                    "time_to_resolve_seconds": 180,
                    "state": "resolved",
                    "automated": True,
                },
            },
            {
                "name": "db_timeout_escalate",
                "is_anomaly": True,
                "expected_result": {"root_cause": "database_issue", "action": "escalate"},
                "actual_result": {
                    "resolved": False,
                    "is_anomaly": True,
                    "time_to_resolve_seconds": 0,
                    "state": "escalated",
                    "automated": False,
                },
            },
            {
                "name": "false_positive_normal",
                "is_anomaly": False,
                "expected_result": {"root_cause": "none", "action": "none"},
                "actual_result": {
                    "resolved": True,
                    "is_anomaly": False,
                    "time_to_resolve_seconds": 0,
                    "state": "closed",
                    "automated": True,
                },
            },
            {
                "name": "dependency_failure",
                "is_anomaly": True,
                "expected_result": {"root_cause": "dependency_failure", "action": "circuit_breaker"},
                "actual_result": {
                    "resolved": True,
                    "is_anomaly": True,
                    "time_to_resolve_seconds": 90,
                    "state": "resolved",
                    "automated": True,
                },
            },
        ]

    def get_eval_history(self, limit: int = 10) -> list[dict[str, Any]]:
        """获取历史评估结果"""
        history = sorted(
            self._eval_history,
            key=lambda x: x.generated_at,
            reverse=True,
        )
        return [h.model_dump() for h in history[:limit]]

    async def generate_comparison_report(
        self,
        report_ids: list[str],
    ) -> dict[str, Any]:
        """
        生成对比报告

        比较多次评估的结果。

        Args:
            report_ids: 要比较的评估报告ID列表

        Returns:
            dict: 对比报告
        """
        if len(report_ids) < 2:
            return {"error": "Need at least 2 reports to compare"}

        # 查找报告
        reports = []
        for rid in report_ids:
            for report in self._eval_history:
                if report.report_id == rid:
                    reports.append(report)
                    break

        if len(reports) < 2:
            return {"error": "Not enough reports found for comparison"}

        # 构建对比
        comparison = {
            "reports": [
                {
                    "id": r.report_id,
                    "generated_at": r.generated_at.isoformat(),
                    "overall_score": r.overall_score,
                    "end_to_end": r.end_to_end.model_dump(),
                    "reasoning": r.reasoning.model_dump(),
                    "tool_call": r.tool_call.model_dump(),
                    "rag": r.rag.model_dump(),
                }
                for r in reports
            ],
            "trends": {
                "overall": [
                    {"report_id": r.report_id, "score": r.overall_score}
                    for r in sorted(reports, key=lambda x: x.generated_at)
                ],
            },
        }

        # 计算改进
        if len(reports) >= 2:
            sorted_reports = sorted(reports, key=lambda x: x.generated_at)
            latest = sorted_reports[-1]
            previous = sorted_reports[-2]
            comparison["improvement"] = {
                "overall_score_diff": round(
                    latest.overall_score - previous.overall_score, 4
                ),
                "end_to_end_diff": round(
                    latest.end_to_end.task_success_rate
                    - previous.end_to_end.task_success_rate,
                    4,
                ),
                "reasoning_diff": round(
                    latest.reasoning.root_cause_accuracy
                    - previous.reasoning.root_cause_accuracy,
                    4,
                ),
                "tool_call_diff": round(
                    latest.tool_call.tool_selection_accuracy
                    - previous.tool_call.tool_selection_accuracy,
                    4,
                ),
                "rag_diff": round(
                    latest.rag.retrieval_f1 - previous.rag.retrieval_f1, 4
                ),
            }

        return comparison
