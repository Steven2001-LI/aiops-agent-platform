"""生成 Accepted Baseline v1 —— 确定性规则路径的评测基线并落库。

用法:
    cd backend && .venv/bin/python -m scripts.generate_accepted_baseline [--force]

口径(全部条件写入 baseline.json,违反任一条拒绝生成):
- LLM 三开关全关且未设 LLM_API_KEY(纯规则路径);
- 记忆系统隔离(不读 ChromaDB 历史,结果与本机记忆库内容无关);
- 数据集为 FAULT_SCENARIOS(golden/fault_scenarios.json)中带告警指标的
  11 条场景;fs_biz_007~010 是业务信号场景,不走告警驱动 RCA,如实排除;
- 只收 reasoning 维度规则指标(端到端维度需要完整管道驱动,不在脚本
  范围);同一评测数据面与真值归一口径(evaluation/ground_truth.py);
- 确定性门禁:同进程完整跑两遍,逐例投影哈希不一致即失败退出。

基线是"仓库代码在合成场景上的确定性参照点",不代表真实生产效果;
数字不做任何挑选或调优,如实入库。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agents.eval_agent import EvalAgent, EvalInput, EvalType  # noqa: E402
from app.agents.rca_agent import RCAAgent, RCAInput  # noqa: E402
from app.config import get_config  # noqa: E402
from app.data.datasets import FAULT_SCENARIOS  # noqa: E402
from app.evaluation.ground_truth import normalize_ground_truth  # noqa: E402
from app.models.agent import AgentExecutionContext  # noqa: E402
from app.models.events import AlertEvent, SeverityLevel  # noqa: E402

BASELINE_DIR = BACKEND_ROOT / "app" / "evaluation" / "baseline" / "accepted" / "v1"

# 告警数值映射表(确定性合成):场景的 metrics 只有 {"指标": "档位"} 标签,
# 无数值;这里为每个指标声明一组明显越过阈值、量纲符合指标语义的常量。
# 数值只影响告警构造,不按结果好坏调整——基线是参照点不是成绩单。
METRIC_ALERT_VALUES: dict[str, tuple[float, float]] = {
    # 基础设施指标:量级对齐 METRICS_DATASETS 异常档
    "cpu_usage_percent": (95.0, 80.0),
    "memory_usage_percent": (95.0, 85.0),
    "p99_latency_ms": (2000.0, 500.0),
    "error_rate_percent": (12.0, 5.0),
    # 业务指标:无静态序列,显式声明合成常量
    "payment_failure_rate": (0.35, 0.05),
    "order_backlog_count": (1200.0, 200.0),
    "auth_failure_rate": (0.4, 0.05),
    "rate_limit_hit_rate": (0.5, 0.1),
    "mq_consumer_lag": (50000.0, 5000.0),
    "replication_lag_seconds": (120.0, 10.0),
}


def canonical_hash(obj: Any) -> str:
    payload = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def assert_rule_only_config() -> None:
    """守住"LLM 关"口径:任一 LLM 能力开启即拒绝生成。"""
    config = get_config()
    llm = config.llm
    if llm.enable_rca or llm.enable_nlu or llm.enable_judge:
        raise SystemExit(
            "拒绝生成基线:LLM 开关未全关"
            f"(rca={llm.enable_rca}, nlu={llm.enable_nlu}, judge={llm.enable_judge})。"
            "Accepted Baseline v1 的口径是纯规则路径。"
        )


def eligible_scenarios() -> tuple[list[dict[str, Any]], list[str]]:
    """带告警指标的场景参评;业务信号场景(无 metrics)如实排除。"""
    included = [s for s in FAULT_SCENARIOS if s.get("metrics")]
    excluded = [s["id"] for s in FAULT_SCENARIOS if not s.get("metrics")]
    return included, excluded


def build_alert(scenario: dict[str, Any]) -> AlertEvent:
    metric = next(iter(scenario["metrics"]))
    value, threshold = METRIC_ALERT_VALUES[metric]
    return AlertEvent(
        source="baseline",
        service=scenario["service"],
        metric=metric,
        value=value,
        threshold=threshold,
        operator=">",
        severity=SeverityLevel(scenario["severity"]),
        labels={"scenario_id": scenario["id"]},
    )


async def run_baseline() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """跑一遍全部参评场景,返回 (per_case 确定性投影, reasoning 报告)。"""
    included, _ = eligible_scenarios()
    agent_results: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []

    for scenario in included:
        agent = RCAAgent()
        # 记忆隔离:惰性初始化直接短路,历史事故检索恒空,
        # 结果与本机 ChromaDB 内容无关(可复现的前提)
        agent._memory_init_attempted = True
        result = await agent.execute(
            RCAInput(alert=build_alert(scenario), incident_id=scenario["id"]),
            AgentExecutionContext(incident_id=scenario["id"], input_data={}),
        )
        if not result.success:
            raise SystemExit(f"RCA 执行失败: {scenario['id']}: {result.error_message}")
        agent_result = {
            "incident_id": scenario["id"],
            "agent_name": "rca_agent",
            "output_data": result.output_data,
        }
        agent_results.append(agent_result)
        samples.append(
            {
                "id": scenario["id"],
                "predicted": EvalAgent._prediction_from_rca_result(agent_result),
                "ground_truth": normalize_ground_truth(scenario),
            }
        )

    eval_agent = EvalAgent()
    eval_result = await eval_agent.execute(
        EvalInput(
            eval_type=EvalType.REASONING,
            target_agent="rca_agent",
            agent_results=agent_results,
            samples_by_type={"reasoning": samples},
        ),
        AgentExecutionContext(incident_id="accepted-baseline-v1", input_data={}),
    )
    if not eval_result.success:
        raise SystemExit(f"评测执行失败: {eval_result.error_message}")
    report = eval_result.output_data["report"]

    per_case: list[dict[str, Any]] = []
    for scenario, sample in zip(included, samples):
        predicted = sample["predicted"]
        truth = sample["ground_truth"]
        expected_actions = truth.get("suggested_actions", [])
        predicted_actions = predicted.get("suggested_actions", [])
        candidates = predicted.get("candidate_causes", [])
        per_case.append(
            {
                "scenario_id": scenario["id"],
                "service": scenario["service"],
                "metric": next(iter(scenario["metrics"])),
                "predicted_root_cause": predicted.get("root_cause"),
                "expected_root_cause": truth.get("root_cause"),
                "root_cause_correct": predicted.get("root_cause")
                == truth.get("root_cause"),
                "root_cause_in_top3": truth.get("root_cause") in candidates[:3],
                "confidence": predicted.get("confidence"),
                "predicted_actions": predicted_actions,
                "expected_actions": expected_actions,
                "action_hit": bool(set(predicted_actions) & set(expected_actions)),
            }
        )
    return per_case, report


def git_info() -> tuple[str, bool]:
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
        cwd=BACKEND_ROOT,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True,
            check=True, cwd=BACKEND_ROOT,
        ).stdout.strip()
    )
    return sha, dirty


def build_baseline_document(
    per_case: list[dict[str, Any]], report: dict[str, Any]
) -> dict[str, Any]:
    included, excluded = eligible_scenarios()
    sha, dirty = git_info()
    config = get_config()
    reasoning = report["reasoning"]
    # judge 未启用恒为 None,剔除避免误读
    reasoning = {k: v for k, v in reasoning.items() if k != "judge"}
    return {
        "schema_version": 1,
        "name": "accepted-baseline-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        # evaluated_git_sha 指被评测代码所在提交,非基线文件入库提交;
        # git_dirty=true 表示生成时工作树有未提交改动(见 report.md 说明)
        "evaluated_git_sha": sha,
        "git_dirty": dirty,
        "dataset": {
            "source": "app.data.datasets.FAULT_SCENARIOS (golden/fault_scenarios.json)",
            "total_scenarios": len(FAULT_SCENARIOS),
            "evaluated_count": len(included),
            "excluded": {
                "ids": excluded,
                "reason": "业务信号场景无告警指标,不走告警驱动 RCA 路径",
            },
            "content_hash": canonical_hash(FAULT_SCENARIOS),
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "llm": {
                "enable_rca": config.llm.enable_rca,
                "enable_nlu": config.llm.enable_nlu,
                "enable_judge": config.llm.enable_judge,
            },
            "memory": "isolated (no ChromaDB history)",
        },
        "metric_definitions": "app.agents.eval_agent.ReasoningMetrics",
        "scope_note": (
            "只收 reasoning 维度规则指标;end_to_end 维度需要完整管道驱动,"
            "不在本基线范围。根因为精确串匹配,闭集通用根因与场景具体真值的"
            "命名差异会如实压低准确率——这是参照点,不是成绩单。"
        ),
        "per_case": per_case,
        "aggregate": {
            "reasoning": reasoning,
            "overall_score": report.get("overall_score"),
        },
        "result_hash": canonical_hash(per_case),
        "acceptance": {
            "status": "accepted",
            "accepted_by": "Steven Li (repo owner)",
            "accepted_at": datetime.now(timezone.utc).isoformat(),
            "criteria": (
                "确定性双跑一致 + 场景真值为仓库作者手写并复核的合成场景 + "
                "纯规则路径(LLM 全关/记忆隔离);仅作为仓库代码在合成场景上的"
                "确定性参照点,不构成对真实生产效果的结论"
            ),
        },
    }


def write_report_md(doc: dict[str, Any]) -> str:
    agg = doc["aggregate"]["reasoning"]
    lines = [
        "# Accepted Baseline v1",
        "",
        f"- 生成时间:{doc['generated_at']}",
        f"- 被评测代码:`{doc['evaluated_git_sha']}`"
        + "(生成时工作树含未提交改动)" * int(doc["git_dirty"]),
        f"- 数据集:{doc['dataset']['source']},"
        f"参评 {doc['dataset']['evaluated_count']}/{doc['dataset']['total_scenarios']} 条"
        f"(排除 {', '.join(doc['dataset']['excluded']['ids'])}:"
        f"{doc['dataset']['excluded']['reason']})",
        "- 口径:纯规则路径(LLM 全关)、记忆隔离、reasoning 维度规则指标",
        "",
        "## 聚合指标",
        "",
        f"- 根因 Top-1 准确率:{agg['root_cause_accuracy']:.4f}",
        f"- 根因 Top-3 命中率:{agg['root_cause_top3_hit_rate']:.4f}",
        f"- 建议操作准确率:{agg['suggested_action_accuracy']:.4f}",
        f"- 证据完整性:{agg['evidence_completeness']:.4f}",
        f"- 置信度校准误差:{agg['confidence_calibration_error']:.4f}",
        f"- 平均置信度:{agg['mean_confidence']:.4f}",
        "",
        "## 逐场景结果",
        "",
        "| 场景 | 服务 | 预测根因 | 真值根因 | Top-1 | Top-3 | 动作命中 |",
        "|------|------|----------|----------|-------|-------|----------|",
    ]
    for case in doc["per_case"]:
        lines.append(
            f"| {case['scenario_id']} | {case['service']} "
            f"| {case['predicted_root_cause']} | {case['expected_root_cause']} "
            f"| {'✓' if case['root_cause_correct'] else '✗'} "
            f"| {'✓' if case['root_cause_in_top3'] else '✗'} "
            f"| {'✓' if case['action_hit'] else '✗'} |"
        )
    lines += [
        "",
        "结果为确定性双跑一致校验后如实入库,未做任何挑选;"
        "该基线是合成场景上的确定性参照点,不代表真实生产效果。",
        "",
        f"复现:`cd backend && python -m scripts.generate_accepted_baseline`"
        f"(结果哈希 `{doc['result_hash'][:16]}…`,由 tests/test_accepted_baseline.py 守护)",
        "",
    ]
    return "\n".join(lines)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="允许覆盖已有基线文件")
    args = parser.parse_args()

    assert_rule_only_config()

    baseline_path = BASELINE_DIR / "baseline.json"
    if baseline_path.exists() and not args.force:
        raise SystemExit(f"{baseline_path} 已存在;覆盖请加 --force")

    # 确定性门禁:完整跑两遍,逐例投影必须一致
    per_case_a, report = await run_baseline()
    per_case_b, _ = await run_baseline()
    if canonical_hash(per_case_a) != canonical_hash(per_case_b):
        raise SystemExit("确定性门禁失败:两次运行结果不一致,拒绝落基线")

    doc = build_baseline_document(per_case_a, report)
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    with open(baseline_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")
    with open(BASELINE_DIR / "report.md", "w", encoding="utf-8") as f:
        f.write(write_report_md(doc))

    agg = doc["aggregate"]["reasoning"]
    print(f"Accepted Baseline v1 已写入 {BASELINE_DIR}")
    print(
        f"top1={agg['root_cause_accuracy']:.4f} "
        f"top3={agg['root_cause_top3_hit_rate']:.4f} "
        f"action={agg['suggested_action_accuracy']:.4f} "
        f"result_hash={doc['result_hash'][:16]}…"
    )


if __name__ == "__main__":
    asyncio.run(main())
