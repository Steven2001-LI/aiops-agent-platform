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

    assert response.status_code == 202
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

    assert response.status_code == 202
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

    assert response.status_code == 202
    data = response.json()
    assert data["report"]["reasoning"]["root_cause_accuracy"] == 1.0
    assert data["ground_truth_sources"] == {
        "explicit": 1,
        "context_scenario": 0,
        "incident_id_scenario": 0,
        "excluded": 0,
        "total": 1,
    }
