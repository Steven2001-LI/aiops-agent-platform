"""Accepted Baseline v1 守护测试:入库基线可复现、数据集不漂移、口径受保护"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.data.datasets import FAULT_SCENARIOS
from scripts.generate_accepted_baseline import canonical_hash, run_baseline

BASELINE_PATH = (
    Path(__file__).resolve().parents[1]
    / "app" / "evaluation" / "baseline" / "accepted" / "v1" / "baseline.json"
)


def _load() -> dict:
    with open(BASELINE_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_baseline_file_committed_and_schema_valid() -> None:
    """基线文件已入库且必备字段齐全"""
    assert BASELINE_PATH.exists(), "Accepted Baseline v1 未入库"
    doc = _load()
    assert doc["schema_version"] == 1
    assert doc["acceptance"]["status"] == "accepted"
    for key in (
        "evaluated_git_sha", "dataset", "environment",
        "per_case", "aggregate", "result_hash", "scope_note",
    ):
        assert key in doc, f"baseline.json 缺字段 {key}"
    assert doc["dataset"]["evaluated_count"] == len(doc["per_case"])
    # 口径:纯规则路径
    assert doc["environment"]["llm"] == {
        "enable_rca": False, "enable_nlu": False, "enable_judge": False,
    }


def test_dataset_hash_matches_live_scenarios() -> None:
    """FAULT_SCENARIOS 改动时强制重新生成并接受基线(数据集漂移报警)"""
    doc = _load()
    assert doc["dataset"]["content_hash"] == canonical_hash(FAULT_SCENARIOS), (
        "FAULT_SCENARIOS 与入库基线的数据集哈希不一致:"
        "场景真值已改动,请重跑 scripts/generate_accepted_baseline --force 并重新接受"
    )


@pytest.mark.asyncio
async def test_baseline_reproducible_in_process() -> None:
    """进程内重跑全部场景,逐例投影与聚合指标与入库值零容差相等"""
    doc = _load()
    per_case, report = await run_baseline()

    assert canonical_hash(per_case) == doc["result_hash"], (
        "基线不可复现:当前代码的确定性结果与入库基线不一致,"
        "若为有意的行为变更请重新生成并接受基线"
    )
    agg = doc["aggregate"]["reasoning"]
    reasoning = report["reasoning"]
    for metric in (
        "root_cause_accuracy",
        "root_cause_top3_hit_rate",
        "suggested_action_accuracy",
        "evidence_completeness",
        "confidence_calibration_error",
    ):
        assert reasoning[metric] == agg[metric], f"{metric} 与入库基线不一致"


def test_generator_refuses_when_llm_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """任一 LLM 开关开启时生成入口拒绝运行(守住纯规则口径)"""
    import scripts.generate_accepted_baseline as gen

    monkeypatch.setattr(
        gen,
        "get_config",
        lambda: SimpleNamespace(
            llm=SimpleNamespace(enable_rca=True, enable_nlu=False, enable_judge=False)
        ),
    )
    with pytest.raises(SystemExit):
        gen.assert_rule_only_config()
