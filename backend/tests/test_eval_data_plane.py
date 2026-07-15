"""S4/S5/S7 评测数据面回归测试。"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.agents.eval_agent import EvalAgent, EvalInput, EvalType
from app.api import routes as routes_module
from app.main import app
from app.models.agent import AgentExecutionContext
from app.models.events import RCAEvent
from app.models.incident import Incident


def make_rca_result(
    root_cause: str,
    *,
    confidence: float = 0.8,
    alert_metric: str = "memory_usage_percent",
) -> dict[str, Any]:
    evidence = {
        "bayesian_top_causes": [{"cause": root_cause, "posterior": 0.8}],
        "rag_matches": [],
        "impact_chain_detail": [{"service": "payment-service", "hop": 0}],
        "alert_metric": alert_metric,
        "alert_value": 92.0,
        "alert_threshold": 85.0,
        "reasoning_mode": "rule_only",
    }
    return {
        "agent_name": "rca_agent",
        "output_data": {
            "rca_event": {
                "root_cause": root_cause,
                "confidence": confidence,
                "impact_chain": ["payment-service"],
                "recommended_actions": ["restart_pod"],
                "evidence": evidence,
            },
            "root_cause": root_cause,
            "confidence": confidence,
            "impact_chain": ["payment-service"],
            "suggested_actions": ["restart_pod"],
        },
    }


def make_pair(
    incident_id: str,
    predicted_root: str,
    expected_root: str | None,
) -> dict[str, Any]:
    result = make_rca_result(predicted_root, confidence=1.0)
    ground_truth = (
        {
            "root_cause": expected_root,
            "suggested_actions": ["restart_pod"],
            "evidence": {"alert_metric": {"memory_usage_percent": "leak"}},
        }
        if expected_root is not None
        else {}
    )
    return {
        "id": incident_id,
        "predicted": EvalAgent._prediction_from_rca_result(result),
        "ground_truth": ground_truth,
    }


def make_context() -> AgentExecutionContext:
    return AgentExecutionContext(incident_id="eval-data-plane-test")


def make_incident(incident_id: str, root_cause: str) -> Incident:
    return Incident(
        incident_id=incident_id,
        service="payment-service",
        context={},
        rca_event=RCAEvent(
            incident_id=incident_id,
            root_cause=root_cause,
            confidence=1.0,
            impact_chain=["payment-service"],
            evidence={
                "alert_metric": "memory_usage_percent",
                "reasoning_mode": "rule_only",
                "bayesian_top_causes": [{"cause": root_cause, "posterior": 1.0}],
            },
            recommended_actions=["restart_pod"],
        ),
    )


def test_reasoning_reads_evidence_from_nested_rca_event() -> None:
    result = make_rca_result("memory_leak")
    assert "evidence" not in result["output_data"]

    metrics = EvalAgent()._eval_reasoning(
        EvalInput(
            eval_type=EvalType.REASONING,
            ground_truth={
                "root_cause": "memory_leak",
                "evidence": {"alert_metric": {"memory_usage_percent": "leak"}},
            },
            agent_results=[result],
        )
    )

    assert metrics.evidence_completeness == 1.0


def test_reasoning_pairs_ground_truth_per_incident() -> None:
    metrics = EvalAgent()._eval_reasoning(
        EvalInput(
            eval_type=EvalType.REASONING,
            samples_by_type={
                "reasoning": [
                    make_pair("inc-correct", "memory_leak", "memory_leak"),
                    make_pair("inc-wrong", "cache_failure", "memory_leak"),
                ]
            },
        )
    )

    assert metrics.root_cause_accuracy == 0.5


@pytest.mark.asyncio
async def test_reasoning_excludes_incident_without_ground_truth() -> None:
    matched = make_pair("inc-matched", "memory_leak", "memory_leak")
    unmatched = make_pair("inc-unmatched", "wrong", None)

    async def run(samples: list[dict[str, Any]]) -> Any:
        return await EvalAgent().process(
            EvalInput(
                eval_type=EvalType.REASONING,
                samples_by_type={"reasoning": samples},
            ),
            make_context(),
        )

    control = await run([matched])
    mixed = await run([matched, unmatched])

    assert control.success is True
    assert mixed.success is True
    assert (
        mixed.output_data["report"]["reasoning"]
        == control.output_data["report"]["reasoning"]
    )
    assert mixed.output_data["overall_score"] == control.output_data["overall_score"]
    coverage = mixed.output_data["report"]["score_coverage"]["reasoning"]
    assert coverage["valid_samples"] == 1
    assert coverage["excluded_samples"] == 1


@pytest.mark.asyncio
async def test_reasoning_only_overall_is_normalized() -> None:
    result = await EvalAgent().process(
        EvalInput(
            eval_type=EvalType.REASONING,
            samples_by_type={
                "reasoning": [make_pair("inc-perfect", "memory_leak", "memory_leak")]
            },
        ),
        make_context(),
    )

    assert result.success is True
    assert result.output_data["overall_score"] == pytest.approx(1.0, abs=1e-4)
    # 单维满分样本归一到 1.0，不再额外乘 reasoning 的 0.30。


@pytest.mark.asyncio
async def test_empty_reasoning_samples_are_na_and_score_zero() -> None:
    result = await EvalAgent().process(
        EvalInput(
            eval_type=EvalType.REASONING,
            samples_by_type={
                "reasoning": [make_pair("inc-no-truth", "memory_leak", None)]
            },
        ),
        make_context(),
    )

    assert result.success is True
    report = result.output_data["report"]
    coverage = report["score_coverage"]["reasoning"]
    assert coverage["status"] == "not_applicable"
    assert coverage["score"] is None
    assert coverage["metrics"]["root_cause_accuracy"]["value"] is None
    assert result.output_data["overall_score"] == 0.0


def test_run_evaluation_pairs_dataset_scenario_incident(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incident = make_incident("fs_002", "memory_leak")
    monkeypatch.setattr(
        routes_module._incident_service, "_incidents", {incident.incident_id: incident}
    )

    response = TestClient(app).post("/api/v1/evaluations/run?eval_type=reasoning")

    assert response.status_code == 200
    data = response.json()
    assert data["report"]["reasoning"]["root_cause_accuracy"] == 1.0
    assert data["ground_truth_sources"] == {
        "explicit": 0,
        "context_scenario": 0,
        "incident_id_scenario": 1,
        "excluded": 0,
        "total": 1,
    }


def test_run_evaluation_marks_unmatched_trigger_incident_na(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incident = make_incident("9d95cb89-ordinary-trigger", "memory_leak")
    monkeypatch.setattr(
        routes_module._incident_service, "_incidents", {incident.incident_id: incident}
    )

    response = TestClient(app).post("/api/v1/evaluations/run?eval_type=reasoning")

    assert response.status_code == 200
    data = response.json()
    coverage = data["report"]["score_coverage"]["reasoning"]
    assert coverage["status"] == "not_applicable"
    assert coverage["metrics"]["root_cause_accuracy"]["value"] is None
    assert data["overall_score"] == 0.0
    assert data["ground_truth_sources"]["excluded"] == 1


def test_run_evaluation_accepts_explicit_ground_truth_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incident = make_incident("9d95cb89-explicit-truth", "memory_leak")
    monkeypatch.setattr(
        routes_module._incident_service, "_incidents", {incident.incident_id: incident}
    )

    response = TestClient(app).post(
        "/api/v1/evaluations/run?eval_type=reasoning",
        json={
            "ground_truth_by_incident": {
                incident.incident_id: {
                    "root_cause": "memory_leak",
                    "expected_action": {"type": "restart_pod", "level": "L0"},
                    "expected_approval": "auto",
                    "metrics": {"memory_usage_percent": "leak"},
                }
            }
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["report"]["reasoning"]["root_cause_accuracy"] == 1.0
    assert data["ground_truth_sources"] == {
        "explicit": 1,
        "context_scenario": 0,
        "incident_id_scenario": 0,
        "excluded": 0,
        "total": 1,
    }


def test_run_evaluation_accepts_eval_type_in_json_body() -> None:
    """前端发送 JSON body 时与 query 形式等价，且同步返回 completed。"""
    response = TestClient(app).post(
        "/api/v1/evaluations/run",
        json={"eval_type": "end_to_end"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert data["execution"] == "completed"
    assert data["report"] is not None


def test_rag_metrics_execute_with_retrieved_documents() -> None:
    """RAG 真实样本会调用相关性/忠实度指标，不得 NameError。"""
    metrics = EvalAgent()._eval_rag(
        EvalInput(
            eval_type=EvalType.RAG,
            test_cases=[
                {
                    "query": "database latency",
                    "retrieved_docs": [
                        {
                            "id": "doc-1",
                            "content": "database latency caused by connection pool",
                            "score": 0.9,
                        }
                    ],
                    "relevant_docs": ["doc-1"],
                    "generated_answer": "database latency",
                }
            ],
        )
    )

    assert metrics.retrieval_precision == 1.0
    assert metrics.retrieval_recall == 1.0
    assert metrics.context_relevance > 0
    assert metrics.answer_faithfulness == 1.0


# =============================================================================
# P10:Top-K 命中率 / 逐事故延迟 / LLM token 成本
# =============================================================================


def _rca_result_with_candidates(
    root_cause: str,
    candidates: list[tuple[str, float]],
    *,
    llm_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = make_rca_result(root_cause)
    rca_event = result["output_data"]["rca_event"]
    rca_event["candidate_causes"] = [
        {"cause": cause, "score": score} for cause, score in candidates
    ]
    if llm_meta is not None:
        rca_event["evidence"]["llm_meta"] = llm_meta
    return result


def _timeline_incident(states_with_offsets: list[tuple[str, int]]) -> Incident:
    """按 (state, 秒偏移) 构造带确定时间线的 Incident"""
    from datetime import datetime, timedelta, timezone

    from app.models.incident import IncidentState, TimelineEntry

    base = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
    incident = Incident(incident_id="inc-latency", service="payment-service", context={})
    incident.timeline = [
        TimelineEntry(
            timestamp=base + timedelta(seconds=offset),
            state=IncidentState(state),
        )
        for state, offset in states_with_offsets
    ]
    return incident


class TestTopKHitRate:
    def test_topk_hit_counts_rank2_candidate(self) -> None:
        """真值在候选第 2 位:Top-1 不中,Top-3 命中"""
        agent = EvalAgent()
        result = _rca_result_with_candidates(
            "traffic_spike",
            [("traffic_spike", 0.6), ("memory_leak", 0.3), ("config_error", 0.1)],
        )
        eval_input = EvalInput(
            eval_type=EvalType.REASONING,
            agent_results=[result],
            ground_truth={"root_cause": "memory_leak"},
        )
        metrics = agent._eval_reasoning(eval_input)
        assert metrics.root_cause_accuracy == 0.0
        assert metrics.root_cause_top3_hit_rate == 1.0

        report = None  # coverage 经 process 验证于路由级测试;此处验证指标级计数
        coverage_input = eval_input
        from app.agents.eval_agent import EvalReport

        report = EvalReport(eval_type="reasoning")
        report.reasoning = metrics
        coverage = agent._build_score_coverage(coverage_input, report)
        topk_cov = coverage["reasoning"]["metrics"]["root_cause_top3_hit_rate"]
        assert topk_cov["status"] == "evaluated"
        assert topk_cov["value"] == 1.0

    def test_topk_na_without_candidates_or_bayes(self) -> None:
        """旧形态产物(无 candidate_causes 也无贝叶斯候选)→ 指标级 not_applicable"""
        agent = EvalAgent()
        result = make_rca_result("memory_leak")
        result["output_data"]["rca_event"]["evidence"].pop("bayesian_top_causes")
        eval_input = EvalInput(
            eval_type=EvalType.REASONING,
            agent_results=[result],
            ground_truth={"root_cause": "memory_leak"},
        )
        metrics = agent._eval_reasoning(eval_input)
        from app.agents.eval_agent import EvalReport

        report = EvalReport(eval_type="reasoning")
        report.reasoning = metrics
        coverage = agent._build_score_coverage(eval_input, report)
        topk_cov = coverage["reasoning"]["metrics"]["root_cause_top3_hit_rate"]
        assert topk_cov["status"] == "not_applicable"
        assert topk_cov["value"] is None
        # 其余指标不受影响
        assert coverage["reasoning"]["status"] == "evaluated"

    def test_topk_falls_back_to_bayesian_candidates(self) -> None:
        """无 P8 字段时回退 evidence.bayesian_top_causes"""
        prediction = EvalAgent._prediction_from_rca_result(make_rca_result("memory_leak"))
        assert prediction["candidate_causes"] == ["memory_leak"]


class TestLatencyMetrics:
    def test_latency_sample_from_real_timeline(self) -> None:
        incident = _timeline_incident(
            [
                ("new", 0),
                ("acknowledged", 2),
                ("rca_in_progress", 5),
                ("rca_completed", 11),
                ("healing", 12),
                ("resolved", 20),
            ]
        )
        sample = EvalAgent._timeline_to_latency_sample(incident)
        assert sample is not None
        assert sample["stages"]["ack"] == 2.0
        assert sample["stages"]["rca"] == 6.0
        assert sample["stages"]["heal_to_terminal"] == 8.0
        assert sample["end_to_end_seconds"] == 20.0
        assert sample["terminal_state"] == "resolved"

    def test_latency_excludes_seed_and_short_timeline(self) -> None:
        from app.models.events import AlertEvent, SeverityLevel

        seed_incident = _timeline_incident([("new", 0), ("resolved", 5)])
        seed_incident.alert_event = AlertEvent(
            service="payment-service",
            metric="cpu_usage_percent",
            value=95.0,
            threshold=80.0,
            severity=SeverityLevel.HIGH,
            annotations={"seed": "true"},
        )
        assert EvalAgent._timeline_to_latency_sample(seed_incident) is None

        short = _timeline_incident([("new", 0)])
        assert EvalAgent._timeline_to_latency_sample(short) is None

    def test_latency_aggregation_and_coverage(self) -> None:
        agent = EvalAgent()
        incident = _timeline_incident(
            [("new", 0), ("acknowledged", 1), ("rca_in_progress", 2),
             ("rca_completed", 5), ("healing", 6), ("escalated", 10)]
        )
        sample = EvalAgent._timeline_to_latency_sample(incident)
        eval_input = EvalInput(
            eval_type=EvalType.END_TO_END,
            samples_by_type={"latency": [sample]},
        )
        metrics = agent._eval_latency(eval_input)
        assert metrics.incidents_measured == 1
        assert metrics.end_to_end_seconds_mean == 10.0
        assert metrics.terminal_state_breakdown == {"escalated": 1}

        from app.agents.eval_agent import EvalReport

        report = EvalReport(eval_type="end_to_end")
        report.latency = metrics
        coverage = agent._build_score_coverage(eval_input, report)
        assert coverage["latency"]["status"] == "evaluated"
        # eval_type=reasoning 时 latency 维度 not_run
        coverage2 = agent._build_score_coverage(
            EvalInput(eval_type=EvalType.REASONING, ground_truth={"root_cause": "x"}),
            report,
        )
        assert coverage2["latency"]["status"] == "not_run"


class TestCostMetrics:
    def test_cost_na_when_llm_disabled(self) -> None:
        """默认规则路径无 llm_meta → cost 维度 not_applicable,各值为 0"""
        agent = EvalAgent()
        eval_input = EvalInput(
            eval_type=EvalType.REASONING,
            agent_results=[make_rca_result("memory_leak")],
            ground_truth={"root_cause": "memory_leak"},
        )
        cost = agent._eval_cost(eval_input, None)
        assert cost.total_tokens == 0
        assert cost.llm_calls == 0

        from app.agents.eval_agent import EvalReport

        report = EvalReport(eval_type="reasoning")
        report.cost = cost
        coverage = agent._build_score_coverage(eval_input, report)
        assert coverage["cost"]["status"] == "not_applicable"

    def test_cost_aggregates_rca_and_judge_meta(self) -> None:
        agent = EvalAgent()
        meta = {"usage": {"input": 100, "output": 50, "total": 150},
                "model": "deepseek-chat", "latency_ms": 1200.0}
        results = [
            _rca_result_with_candidates("memory_leak", [("memory_leak", 0.8)], llm_meta=meta),
            _rca_result_with_candidates("traffic_spike", [("traffic_spike", 0.7)], llm_meta=meta),
        ]
        judge_meta = {"usage": {"input": 30, "output": 20, "total": 50},
                      "model": "deepseek-chat", "latency_ms": 800.0}
        eval_input = EvalInput(
            eval_type=EvalType.REASONING,
            agent_results=results,
            ground_truth={"root_cause": "memory_leak"},
        )
        cost = agent._eval_cost(eval_input, judge_meta)
        assert cost.total_tokens == 350
        assert cost.llm_calls == 3
        assert cost.by_source["rca"]["total"] == 300
        assert cost.by_source["rca"]["calls"] == 2
        assert cost.by_source["judge"]["total"] == 50
        assert cost.avg_tokens_per_call == pytest.approx(350 / 3, abs=0.1)


def test_run_evaluation_reports_latency_and_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    """路由级:report 携带 latency/cost 维度,coverage 六键齐备,总分不含新维贡献"""
    from datetime import datetime, timedelta, timezone

    from app.models.incident import IncidentState, TimelineEntry

    incident = make_incident("inc-p10-route", "memory_leak")
    incident.context["scenario_id"] = "fs_002"
    base = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
    incident.timeline = [
        TimelineEntry(timestamp=base, state=IncidentState.NEW),
        TimelineEntry(timestamp=base + timedelta(seconds=3), state=IncidentState.RESOLVED),
    ]
    incident.state = IncidentState.RESOLVED

    monkeypatch.setattr(
        routes_module._incident_service, "_incidents", {"inc-p10-route": incident}
    )
    client = TestClient(app)

    # e2e 请求面:latency 维度参评
    resp = client.post("/api/v1/evaluations/run?eval_type=end_to_end")
    assert resp.status_code == 200
    report = resp.json()["report"]
    assert report["latency"]["incidents_measured"] == 1
    assert report["latency"]["end_to_end_seconds_mean"] == 3.0
    coverage = report["score_coverage"]
    assert set(coverage) == {
        "end_to_end", "reasoning", "tool_call", "rag", "latency", "cost",
    }
    assert coverage["latency"]["status"] == "evaluated"
    assert coverage["cost"]["status"] == "not_run"  # cost 挂 reasoning 请求面

    # reasoning 请求面:规则路径无 llm_meta → cost 如实 not_applicable
    resp = client.post("/api/v1/evaluations/run?eval_type=reasoning")
    assert resp.status_code == 200
    report = resp.json()["report"]
    assert report["cost"]["total_tokens"] == 0
    coverage = report["score_coverage"]
    assert coverage["cost"]["status"] == "not_applicable"
    assert coverage["latency"]["status"] == "not_run"


class TestIncidentToTestCaseHonesty:
    """Codex 阻塞项 3 回归:escalated/automated/时长三处口径"""

    @staticmethod
    def _incident_with_timeline(
        state: "IncidentState", seconds: int = 1000
    ) -> Incident:
        from datetime import datetime, timedelta, timezone

        from app.models.incident import IncidentState, TimelineEntry

        base = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
        incident = make_incident("inc-honesty", "memory_leak")
        incident.timeline = [
            TimelineEntry(timestamp=base, state=IncidentState.NEW),
            TimelineEntry(
                timestamp=base + timedelta(seconds=seconds), state=state
            ),
        ]
        incident.state = state
        return incident

    def test_escalated_incident_marked_escalated(self) -> None:
        from app.models.incident import IncidentState

        incident = self._incident_with_timeline(IncidentState.ESCALATED)
        case = EvalAgent._incident_to_test_case(incident, {})
        assert case["actual_result"]["escalated"] is True
        assert case["actual_result"]["resolved"] is False

    def test_human_approved_resolution_not_automated(self) -> None:
        from app.models.events import ApprovalStatus, ChangeEvent
        from app.models.incident import IncidentState

        incident = self._incident_with_timeline(IncidentState.RESOLVED)
        incident.change_events.append(
            ChangeEvent(
                incident_id=incident.incident_id,
                approval_status=ApprovalStatus.APPROVED,  # 人工批准
            )
        )
        case = EvalAgent._incident_to_test_case(incident, {})
        assert case["actual_result"]["automated"] is False

        # 对照:自动批准的解决算自动化
        incident.change_events[0].approval_status = ApprovalStatus.AUTO_APPROVED
        case = EvalAgent._incident_to_test_case(incident, {})
        assert case["actual_result"]["automated"] is True

    def test_time_from_timeline_not_dead_metrics_field(self) -> None:
        """1000 秒的真实时间线不得重建成 0 秒(死字段恒 0 会骗到满分时间分)"""
        from app.models.incident import IncidentState

        incident = self._incident_with_timeline(IncidentState.RESOLVED, seconds=1000)
        case = EvalAgent._incident_to_test_case(incident, {})
        assert case["actual_result"]["time_to_resolve_seconds"] == 1000.0

        # 时间线不可测 → None(而非伪装成 0 秒)
        incident.timeline = []
        case = EvalAgent._incident_to_test_case(incident, {})
        assert case["actual_result"]["time_to_resolve_seconds"] is None


class TestSamplesByTypeUnified:
    """Codex 建议 1 回归:仅经 samples_by_type 传入的样本,覆盖判定与执行一致"""

    def test_e2e_samples_by_type_only_is_evaluated(self) -> None:
        agent = EvalAgent()
        sample = {
            "id": "sbt-e2e",
            "is_anomaly": True,
            "actual_result": {
                "resolved": True,
                "is_anomaly": True,
                "escalated": False,
                "root_cause": "memory_leak",
                "action": "restart_pod",
                "time_to_resolve_seconds": 60,
            },
            "expected_result": {"root_cause": "memory_leak", "action": "restart_pod"},
        }
        eval_input = EvalInput(
            eval_type=EvalType.END_TO_END,
            samples_by_type={"end_to_end": [sample]},
        )
        metrics = agent._eval_end_to_end(eval_input)
        assert metrics.total_test_cases == 1
        assert metrics.task_success_rate == 1.0

        from app.agents.eval_agent import EvalReport

        report = EvalReport(eval_type="end_to_end")
        report.end_to_end = metrics
        coverage = agent._build_score_coverage(eval_input, report)
        assert coverage["end_to_end"]["status"] == "evaluated"

    def test_rag_samples_by_type_only_is_evaluated(self) -> None:
        agent = EvalAgent()
        sample = {
            "id": "sbt-rag",
            "query": "mysql timeout",
            "generated_answer": "connection pool exhausted",
            "retrieved_docs": [{"id": "d1", "content": "mysql pool exhausted"}],
            "relevant_docs": ["d1"],
        }
        eval_input = EvalInput(
            eval_type=EvalType.RAG,
            samples_by_type={"rag": [sample]},
        )
        metrics = agent._eval_rag(eval_input)
        from app.agents.eval_agent import EvalReport

        report = EvalReport(eval_type="rag")
        report.rag = metrics
        coverage = agent._build_score_coverage(eval_input, report)
        assert coverage["rag"]["status"] == "evaluated"
