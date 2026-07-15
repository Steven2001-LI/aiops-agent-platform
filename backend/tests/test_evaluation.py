"""
AIOps Agent Platform - Evaluation Framework Integration Tests

测试评估框架的端到端指标、推理评估、工具调用评估、RAG评估和完整报告生成。
"""

from __future__ import annotations

import pytest

from app.evaluation.core import EvaluationFramework, get_evaluation_framework
from app.evaluation.end_to_end import EndToEndEvaluator
from app.evaluation.rag_eval import RAGEvaluator
from app.evaluation.reasoning_eval import ReasoningEvaluator
from app.evaluation.tool_call_eval import ToolCallEvaluator
from app.models.evaluation import (
    BenchmarkReport,
    EvaluationResult,
    EvaluationStatus,
    EvaluationType,
)


class TestEndToEndEvaluation:
    """端到端评估测试套件"""

    @pytest.fixture
    def evaluator(self) -> EndToEndEvaluator:
        """创建EndToEndEvaluator实例"""
        return EndToEndEvaluator()

    @pytest.mark.asyncio
    async def test_end_to_end_refuses_without_samples(
        self, evaluator: EndToEndEvaluator
    ) -> None:
        """无显式样本时明确拒绝——不再回落预烤 actual_result 的内置默认集自评"""
        result = await evaluator.evaluate(target_agent="test_agent")

        assert isinstance(result, EvaluationResult)
        assert result.evaluation_type == EvaluationType.END_TO_END
        assert result.status == EvaluationStatus.FAILED
        assert result.total_samples == 0
        assert any("real" in e.lower() or "样本" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_end_to_end_with_samples(self, evaluator: EndToEndEvaluator) -> None:
        """测试使用自定义样本的端到端评估"""
        samples = [
            {
                "id": "test-001",
                "name": "CPU High",
                "actual_result": {
                    "resolved": True,
                    "automated": True,
                    "root_cause": "traffic_spike",
                    "action": "scale_up",
                    "time_to_resolve_seconds": 120,
                },
                "expected_result": {
                    "resolved": True,
                    "root_cause": "traffic_spike",
                    "action": "scale_up",
                },
            },
            {
                "id": "test-002",
                "name": "DB Timeout",
                "actual_result": {
                    "resolved": False,
                    "automated": False,
                    "root_cause": "db_pool_exhausted",
                    "action": "escalate",
                    "time_to_resolve_seconds": 600,
                },
                "expected_result": {
                    "resolved": False,
                    "root_cause": "db_pool_exhausted",
                    "action": "escalate",
                },
            },
        ]
        result = await evaluator.evaluate(target_agent="test_agent", samples=samples)

        assert isinstance(result, EvaluationResult)
        # 硬断言 COMPLETED:此前包在 if 里,FAILED 会静默通过
        assert result.status == EvaluationStatus.COMPLETED
        assert result.total_samples == 2
        assert len(result.metric_scores) > 0

    @pytest.mark.asyncio
    async def test_evaluate_batch_missing_actual_result(
        self, evaluator: EndToEndEvaluator
    ) -> None:
        """缺 actual_result 的样本记错误,不产生空值比对的假满分"""
        batch = await evaluator.evaluate_batch(
            [{"id": "no-actual", "expected_result": {"root_cause": "memory_leak"}}]
        )
        assert len(batch) == 1
        assert batch[0]["success"] is False
        assert "error" in batch[0]
        assert "root_cause_match" not in batch[0]

    @pytest.mark.asyncio
    async def test_root_cause_match_not_vacuous(
        self, evaluator: EndToEndEvaluator
    ) -> None:
        """actual 未给出根因时不得与空 expected 匹配成 True"""
        batch = await evaluator.evaluate_batch(
            [
                {
                    "id": "vacuous",
                    "actual_result": {"resolved": True},
                    "expected_result": {},
                }
            ]
        )
        assert batch[0]["root_cause_match"] is False
        assert batch[0]["action_match"] is False

    def test_time_score_calculation(self, evaluator: EndToEndEvaluator) -> None:
        """测试处理时间分数计算"""
        # 2分钟内应得满分
        score_fast = evaluator._compute_time_score(60)
        assert score_fast == 1.0

        # 超过10分钟应得低分
        score_slow = evaluator._compute_time_score(900)
        assert score_slow < 0.3


class TestReasoningEvaluation:
    """推理评估测试套件"""

    @pytest.fixture
    def evaluator(self) -> ReasoningEvaluator:
        """创建ReasoningEvaluator实例"""
        return ReasoningEvaluator()

    @pytest.mark.asyncio
    async def test_reasoning_eval(self, evaluator: ReasoningEvaluator) -> None:
        """
        测试推理评估

        确保推理能力评估指标被正确计算。
        """
        result = await evaluator.evaluate(target_agent="test_agent")

        assert isinstance(result, EvaluationResult)
        assert result.evaluation_type == EvaluationType.REASONING

        if result.status == EvaluationStatus.COMPLETED:
            assert len(result.metric_scores) > 0
            metric_names = [m.metric_name for m in result.metric_scores]
            assert "root_cause_accuracy" in metric_names

    @pytest.mark.asyncio
    async def test_reasoning_with_samples(self, evaluator: ReasoningEvaluator) -> None:
        """测试使用自定义样本的推理评估"""
        samples = [
            {
                "id": "reason-001",
                "predicted": {
                    "root_cause": "traffic_spike",
                    "confidence": 0.9,
                    "evidence": {
                        "metrics": ["cpu_usage", "request_rate"],
                        "logs": ["rate_limiter_triggered"],
                    },
                    "impact_chain": ["traffic_spike", "increased_request_rate", "cpu_high"],
                    "reasoning_steps": [
                        {"description": "Observe CPU anomaly", "order": 1},
                        {"description": "Check request rate", "order": 2},
                    ],
                },
                "ground_truth": {
                    "root_cause": "traffic_spike",
                    "confidence": 0.9,
                    "evidence": {
                        "metrics": ["cpu_usage", "request_rate"],
                        "logs": ["rate_limiter_triggered"],
                    },
                    "impact_chain": ["traffic_spike", "increased_request_rate", "cpu_high"],
                    "reasoning_steps": [
                        {"description": "Observe CPU anomaly", "order": 1},
                        {"description": "Check request rate", "order": 2},
                    ],
                },
            },
        ]
        result = await evaluator.evaluate(target_agent="test_agent", samples=samples)

        assert isinstance(result, EvaluationResult)
        if result.status == EvaluationStatus.COMPLETED:
            # 完美匹配应得高分
            rca_metric = next(
                (m for m in result.metric_scores if m.metric_name == "root_cause_accuracy"),
                None,
            )
            if rca_metric:
                assert rca_metric.score == 1.0

    @pytest.mark.asyncio
    async def test_evaluate_rca(self, evaluator: ReasoningEvaluator) -> None:
        """测试RCA评估"""
        result = await evaluator.evaluate_rca(
            predicted_root_causes=["traffic_spike", "db_issue"],
            actual_root_causes=["traffic_spike", "config_error"],
            predicted_confidences=[0.9, 0.7],
        )
        assert "root_cause_accuracy" in result
        assert result["total_samples"] == 2

    @pytest.mark.asyncio
    async def test_evaluate_confidence(self, evaluator: ReasoningEvaluator) -> None:
        """测试置信度校准评估"""
        confidences = [0.9, 0.8, 0.7, 0.6, 0.5]
        accuracies = [True, True, False, True, False]
        result = await evaluator.evaluate_confidence(confidences, accuracies)

        assert "ece" in result
        assert "calibration_score" in result
        assert 0.0 <= result["ece"] <= 1.0

    def test_rubric(self, evaluator: ReasoningEvaluator) -> None:
        """测试评分标准"""
        rubric = evaluator.get_rubric()
        assert "root_cause_identification" in rubric
        assert "evidence_quality" in rubric
        assert "confidence_calibration" in rubric


class TestToolCallEvaluation:
    """工具调用评估测试套件"""

    @pytest.fixture
    def evaluator(self) -> ToolCallEvaluator:
        """创建ToolCallEvaluator实例"""
        return ToolCallEvaluator()

    @pytest.mark.asyncio
    async def test_tool_call_eval(self, evaluator: ToolCallEvaluator) -> None:
        """
        测试工具调用评估

        确保工具调用准确性指标被正确计算。
        """
        result = await evaluator.evaluate(target_agent="test_agent")

        assert isinstance(result, EvaluationResult)
        assert result.evaluation_type == EvaluationType.TOOL_CALL

        if result.status == EvaluationStatus.COMPLETED:
            assert len(result.metric_scores) > 0
            metric_names = [m.metric_name for m in result.metric_scores]
            assert "tool_selection_accuracy" in metric_names

    @pytest.mark.asyncio
    async def test_evaluate_tool_selection(self, evaluator: ToolCallEvaluator) -> None:
        """测试工具选择评估"""
        result = await evaluator.evaluate_tool_selection(
            selected_tools=["query_metrics", "get_service_info"],
            expected_tools=["query_metrics", "get_service_info"],
        )
        assert result["accuracy"] == 1.0
        assert len(result["correct_tools"]) == 2

    @pytest.mark.asyncio
    async def test_evaluate_parameters(self, evaluator: ToolCallEvaluator) -> None:
        """测试参数评估"""
        tool_calls = [
            {
                "tool_name": "query_metrics",
                "parameters": {"service": "order-service", "metric": "cpu"},
                "expected_parameters": {"service": "order-service", "metric": "cpu"},
            },
        ]
        result = await evaluator.evaluate_parameters(tool_calls)
        assert result["overall_accuracy"] == 1.0

    @pytest.mark.asyncio
    async def test_evaluate_efficiency(self, evaluator: ToolCallEvaluator) -> None:
        """测试调用效率评估"""
        result = await evaluator.evaluate_efficiency(
            actual_calls=["query_metrics", "get_service_info"],
            minimum_calls=["query_metrics", "get_service_info"],
        )
        assert result["is_optimal"] is True
        assert result["efficiency"] == 1.0


class TestRAGEvaluation:
    """RAG评估测试套件"""

    @pytest.fixture
    def evaluator(self) -> RAGEvaluator:
        """创建RAGEvaluator实例"""
        return RAGEvaluator()

    @pytest.mark.asyncio
    async def test_rag_eval(self, evaluator: RAGEvaluator) -> None:
        """
        测试RAG评估

        确保RAG效果指标被正确计算。
        """
        result = await evaluator.evaluate(target_agent="test_agent")

        assert isinstance(result, EvaluationResult)
        assert result.evaluation_type == EvaluationType.RAG

        if result.status == EvaluationStatus.COMPLETED:
            assert len(result.metric_scores) > 0
            metric_names = [m.metric_name for m in result.metric_scores]
            assert "retrieval_precision" in metric_names
            assert "retrieval_recall" in metric_names

    @pytest.mark.asyncio
    async def test_retrieval_eval(self, evaluator: RAGEvaluator) -> None:
        """测试检索质量评估"""
        samples = [
            {
                "id": "rag-001",
                "retrieved_docs": [
                    {"id": "doc_001", "content": "relevant"},
                    {"id": "doc_002", "content": "irrelevant"},
                ],
                "relevant_docs": ["doc_001"],
            },
        ]
        result = await evaluator.evaluate_retrieval(samples)
        assert "avg_precision" in result
        assert "avg_recall" in result

    @pytest.mark.asyncio
    async def test_generation_eval(self, evaluator: RAGEvaluator) -> None:
        """测试生成质量评估"""
        samples = [
            {
                "id": "rag-001",
                "generated_answer": "The answer is based on the documents.",
                "retrieved_docs": [
                    {"id": "doc_001", "content": "The documents contain relevant info."},
                ],
                "query": "What is the answer?",
            },
        ]
        result = await evaluator.evaluate_generation(samples)
        assert "avg_faithfulness" in result
        assert "avg_relevance" in result


class TestEvaluationFramework:
    """评估框架整体测试套件"""

    @pytest.fixture
    def framework(self) -> EvaluationFramework:
        """创建EvaluationFramework实例"""
        fw = get_evaluation_framework()
        fw.reset_history()
        return fw

    @pytest.mark.asyncio
    async def test_full_report(self, framework: EvaluationFramework) -> None:
        """
        测试完整报告生成

        端到端测试评估框架的报告生成功能。
        """
        # 执行所有评估
        results = await framework.evaluate_all(target_agent="test_agent")

        assert len(results) == 4  # 4种评估类型
        completed = [r for r in results if r.status == EvaluationStatus.COMPLETED]
        assert len(completed) > 0

        # 生成报告
        report = await framework.generate_report(
            results=results,
            report_name="Integration Test Report",
        )

        assert isinstance(report, BenchmarkReport)
        assert report.report_name == "Integration Test Report"
        assert len(report.results) == 4
        assert "total_evaluations" in report.summary

    @pytest.mark.asyncio
    async def test_individual_evaluations(self, framework: EvaluationFramework) -> None:
        """测试各类型独立评估"""
        for eval_type in [
            EvaluationType.END_TO_END,
            EvaluationType.REASONING,
            EvaluationType.TOOL_CALL,
            EvaluationType.RAG,
        ]:
            result = await framework.evaluate(
                eval_type=eval_type,
                target_agent="test_agent",
            )
            assert isinstance(result, EvaluationResult)
            assert result.evaluation_type == eval_type

    def test_compute_summary(self, framework: EvaluationFramework) -> None:
        """测试汇总统计计算"""
        results = [
            EvaluationResult(
                evaluation_type=EvaluationType.END_TO_END,
                eval_name="e2e_test",
                overall_score=0.85,
                status=EvaluationStatus.COMPLETED,
                total_samples=10,
                passed_samples=8,
            ),
            EvaluationResult(
                evaluation_type=EvaluationType.REASONING,
                eval_name="reasoning_test",
                overall_score=0.75,
                status=EvaluationStatus.COMPLETED,
                total_samples=5,
                passed_samples=4,
            ),
        ]
        summary = framework._compute_summary(results)

        assert summary["total_evaluations"] == 2
        assert summary["completed"] == 2
        assert "avg_overall_score" in summary

    def test_generate_recommendations(self, framework: EvaluationFramework) -> None:
        """测试改进建议生成"""
        results = [
            EvaluationResult(
                evaluation_type=EvaluationType.END_TO_END,
                eval_name="e2e_test",
                status=EvaluationStatus.COMPLETED,
                overall_score=0.5,
            ),
        ]
        recommendations = framework._generate_recommendations(results)
        assert isinstance(recommendations, list)
        assert len(recommendations) > 0

    @pytest.mark.asyncio
    async def test_trend_analysis(self, framework: EvaluationFramework) -> None:
        """测试趋势分析"""
        # 先执行几次评估生成历史数据
        for _ in range(3):
            await framework.evaluate(
                eval_type=EvaluationType.END_TO_END,
                target_agent="test_agent",
            )

        trend = await framework.analyze_trends(eval_type=EvaluationType.END_TO_END)

        assert "total_runs" in trend
        assert trend["total_runs"] >= 3
        assert "trend_direction" in trend

    def test_history_management(self, framework: EvaluationFramework) -> None:
        """测试历史记录管理"""
        history = framework.get_history()
        assert isinstance(history, list)

        framework.reset_history()
        assert len(framework.get_history()) == 0


class TestSendToLangfuse:
    """send_to_langfuse 真实上报测试(此前是无条件返回 True 的占位实现)。"""

    def _make_result(self) -> EvaluationResult:
        from app.models.evaluation import MetricScore

        return EvaluationResult(
            evaluation_type=EvaluationType.END_TO_END,
            eval_name="e2e_smoke",
            agent_type="rca",
            status=EvaluationStatus.COMPLETED,
            overall_score=0.87,
            metric_scores=[
                MetricScore(metric_name="task_success_rate", score=0.9, weight=0.5),
                MetricScore(metric_name="mttr_score", score=0.8, weight=0.5),
            ],
        )

    @pytest.mark.asyncio
    async def test_disabled_skips_and_returns_false(self) -> None:
        """conftest 强制 LANGFUSE_ENABLED=false:禁用时不再假装成功。"""
        framework = EvaluationFramework()
        assert await framework.send_to_langfuse(self._make_result()) is False

    @pytest.mark.asyncio
    async def test_enabled_reports_trace_and_scores(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """enabled 时创建 eval trace 并逐指标 + overall 打分。"""
        from types import SimpleNamespace

        class StubLangfuse:
            def __init__(self) -> None:
                self.traces: list[str] = []
                self.scores: list[tuple[str, str, float]] = []

            def is_enabled(self) -> bool:
                return True

            def create_trace(self, name, metadata=None, user_id="", session_id="", tags=None):
                self.traces.append(name)
                return SimpleNamespace(id="trace-eval-001")

            def score_trace(self, trace_id, name, value, comment=""):
                self.scores.append((trace_id, name, value))

        stub = StubLangfuse()
        monkeypatch.setattr(
            "app.services.langfuse_service.get_langfuse_service", lambda: stub
        )

        framework = EvaluationFramework()
        result = self._make_result()
        assert await framework.send_to_langfuse(result) is True

        assert stub.traces == ["eval_end_to_end"]
        assert len(stub.scores) == 3  # 2 个指标 + overall_score
        assert ("trace-eval-001", "task_success_rate", 0.9) in stub.scores
        assert ("trace-eval-001", "overall_score", 0.87) in stub.scores

    @pytest.mark.asyncio
    async def test_stub_failure_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """上报中途异常返回 False,不向调用方抛出。"""

        class BrokenLangfuse:
            def is_enabled(self) -> bool:
                return True

            def create_trace(self, *args, **kwargs):
                raise RuntimeError("network down")

        monkeypatch.setattr(
            "app.services.langfuse_service.get_langfuse_service",
            lambda: BrokenLangfuse(),
        )

        framework = EvaluationFramework()
        assert await framework.send_to_langfuse(self._make_result()) is False
