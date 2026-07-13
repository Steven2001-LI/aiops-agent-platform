"""RCA/NLU 最终路径 Prometheus 计数器回归测试。"""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.rca_agent import RCA_PATH_TOTAL, RCAInput
from app.data.knowledge_base import SERVICE_TOPOLOGY
from app.models.agent import AgentExecutionContext
from app.nlu.hybrid import NLU_PATH_TOTAL, hybrid_understand
from tests.test_llm_service import make_fake_response
from tests.test_nlu_hybrid import (
    AMBIGUOUS_QUERY,
    CONFIDENT_QUERY,
    make_nlu_service,
    valid_nlu_json,
)
from tests.test_rca_llm_integration import (
    make_agent,
    make_alert,
    make_rca_service,
    rca_evidence,
    valid_llm_json,
)

KNOWN_SERVICES = list(SERVICE_TOPOLOGY.keys())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "scripted", "service_kwargs"),
    [
        ("rule_only", [], {"enable_rca": False}),
        (
            "llm_hybrid",
            [make_fake_response(valid_llm_json("resource_exhaustion", 0.9))],
            {},
        ),
        ("rule_fallback", [ConnectionError("boom")], {"max_retries": 0}),
    ],
)
async def test_rca_path_counter_increments_once(
    path: str,
    scripted: list[Any],
    service_kwargs: dict[str, Any],
    agent_context: AgentExecutionContext,
) -> None:
    service, _fake = make_rca_service(scripted, **service_kwargs)
    agent = make_agent(service)
    counter = RCA_PATH_TOTAL.labels(path)
    before = counter._value.get()

    result = await agent.process(
        RCAInput(alert=make_alert(), incident_id=f"metrics-{path}"), agent_context
    )

    assert result.success is True
    assert rca_evidence(result)["reasoning_mode"] == path
    assert counter._value.get() - before == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "query", "scripted", "service_kwargs"),
    [
        ("fast_rule", CONFIDENT_QUERY, [], {"fast_path_confidence": 0.3}),
        (
            "llm_enhanced",
            AMBIGUOUS_QUERY,
            [make_fake_response(valid_nlu_json())],
            {},
        ),
        (
            "rule_fallback",
            AMBIGUOUS_QUERY,
            [ConnectionError("boom")],
            {"max_retries": 0},
        ),
    ],
)
async def test_nlu_path_counter_increments_once(
    path: str,
    query: str,
    scripted: list[Any],
    service_kwargs: dict[str, Any],
) -> None:
    service, _fake = make_nlu_service(scripted, **service_kwargs)
    counter = NLU_PATH_TOTAL.labels(path)
    before = counter._value.get()

    _intent, _entities, info = await hybrid_understand(
        query, known_services=KNOWN_SERVICES, llm_service=service
    )

    assert info["path"] == path
    assert counter._value.get() - before == 1
