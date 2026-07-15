"""场景真值归一:评测端点与基线脚本共用同一口径。

从 routes.run_evaluation 的内嵌函数提升为模块级——保证
POST /evaluations/run 与 scripts/generate_accepted_baseline 的
ground_truth 形态完全一致,基线数字与端点数字可互相对照。
"""

from __future__ import annotations

from typing import Any


def normalize_ground_truth(raw: dict[str, Any]) -> dict[str, Any]:
    """把请求体/数据集的场景真值归一为 reasoning ground_truth。"""
    truth = dict(raw)
    expected_action = raw.get("expected_action", {})
    if "suggested_actions" not in truth:
        if isinstance(expected_action, dict) and expected_action.get("type"):
            truth["suggested_actions"] = [expected_action["type"]]
        elif isinstance(expected_action, str) and expected_action:
            truth["suggested_actions"] = [expected_action]
    # FAULT_SCENARIOS 没有单独 evidence 字段；metrics 是场景已知的
    # 观测真值，只映射为 evidence_completeness 所比较的证据类型。
    if "evidence" not in truth and raw.get("metrics"):
        truth["evidence"] = {"alert_metric": raw["metrics"]}
    return truth
