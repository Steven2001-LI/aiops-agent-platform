"""测试 EvalAgent 在无数据时不再 fallback 到 mock"""
from __future__ import annotations

import pytest


def test_eval_reasoning_method_removed():
    """_eval_reasoning_with_mock_data 必须不存在"""
    from app.agents import eval_agent
    assert not hasattr(eval_agent.EvalAgent, "_eval_reasoning_with_mock_data"), (
        "_eval_reasoning_with_mock_data should be deleted (no placeholders allowed)"
    )


def test_eval_reasoning_raises_without_data():
    """无 ground_truth 无 agent_results 时必须抛 ValueError"""
    from app.agents.eval_agent import EvalAgent, EvalInput

    agent = EvalAgent()
    input_data = EvalInput(
        eval_type="reasoning",
        ground_truth={},
        agent_results=[],
    )
    with pytest.raises(ValueError, match="Cannot evaluate reasoning"):
        agent._eval_reasoning(input_data)