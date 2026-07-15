"""
AIOps Agent Platform - Test Datasets

评估和测试用的数据集定义。
包含模拟指标数据、故障场景、变更记录等。
所有数据集支持动态扩展。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

# ============================================================
# Golden 数据文件加载
# ============================================================
# 四个数据集常量外置为 backend/app/data/golden/*.json(独立数据文件,
# 可被评测/文档直接引用与扩展);模块导入时一次性加载,保持既有的
# 模块级常量语义 —— from-import 名字绑定、add_* 函数的进程内可变
# 共享、测试对 routes.get_metric_data 的 monkeypatch 均不受影响。
# 注意:golden 文件必须放在 app/ 树内(backend/data/ 被 .gitignore
# 忽略且被 compose 数据卷遮蔽);若未来打包为 wheel,Path(__file__)
# 需换成 importlib.resources。

import json
from pathlib import Path

_GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


def _load_golden(name: str) -> Any:
    """读取 golden/<name>.json(UTF-8,中文内容未转义)。"""
    with open(_GOLDEN_DIR / f"{name}.json", encoding="utf-8") as f:
        return json.load(f)


# ============================================================
# 模拟指标数据集
# ============================================================
# 5 服务 × 4 指标 × normal/anomaly 等场景档,全静态数值

METRICS_DATASETS: dict[str, dict[str, dict[str, list[float]]]] = _load_golden(
    "metrics_datasets"
)


# ============================================================
# 故障场景数据集(评测真值 golden set,15 条)
# ============================================================

FAULT_SCENARIOS: list[dict[str, Any]] = _load_golden("fault_scenarios")


# ============================================================
# 变更记录数据集(模拟 CMDB/发布系统)
# ============================================================
# 文件里存相对偏移 minutes_ago,加载时换算为相对进程启动时刻的
# 时间戳:消费方("最近 N 分钟"时间窗过滤)对固定旧日期恒为空,
# "近期"语义必须靠相对时间保持为真。偏移保持原数据先后顺序;
# order-service 的 deployment 保持在默认 60 分钟窗口内(RCA 知识库
# deployment_issue 加成依赖它)。


def _change_ts(minutes_ago: int) -> str:
    """生成相对当前时刻的 ISO 时间戳(aware UTC)。"""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def _load_change_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for raw in _load_golden("change_records"):
        record = {k: v for k, v in raw.items() if k != "minutes_ago"}
        record["timestamp"] = _change_ts(int(raw["minutes_ago"]))
        records.append(record)
    return records


CHANGE_RECORDS: list[dict[str, Any]] = _load_change_records()


# ============================================================
# 业务事件数据集 - BusinessMonitorAgent 规则引擎的数据源
# ============================================================
# 结构: {场景: {数据源: [事件记录]}}。"normal" 为不命中任何规则的
# 日常记录;fs_biz_007~010 为对应 FAULT_SCENARIOS 纯业务故障场景
# 准备的异常记录。与 METRICS_DATASETS 的 normal/anomaly 分档同一思路。

BUSINESS_EVENTS: dict[str, dict[str, list[dict[str, Any]]]] = _load_golden(
    "business_events"
)


def get_business_events(scenario: str = "normal") -> dict[str, list[dict[str, Any]]]:
    """
    获取业务事件数据集

    Args:
        scenario: 场景ID(normal / fs_biz_007~010);未知场景回退 normal

    Returns:
        {数据源: [事件记录]} 字典
    """
    return BUSINESS_EVENTS.get(scenario, BUSINESS_EVENTS["normal"])


# ============================================================
# 数据集访问辅助函数
# ============================================================

def get_metric_data(
    service: str,
    metric: str,
    scenario: str = "normal",
) -> list[float]:
    """
    获取指定服务的指标数据

    Args:
        service: 服务名称
        metric: 指标名称
        scenario: 场景类型（normal/anomaly/spike/leak/degradation）

    Returns:
        指标数据点列表
    """
    service_data = METRICS_DATASETS.get(service, {})
    metric_data = service_data.get(metric, {})

    # 尝试获取指定场景的数据
    data = metric_data.get(scenario)
    if data is not None:
        return data

    # 回退到 normal
    data = metric_data.get("normal")
    if data is not None:
        return data

    # 最终回退
    return [50.0] * 10


def get_fault_scenario(scenario_id: str) -> dict[str, Any] | None:
    """
    根据 ID 获取故障场景

    Args:
        scenario_id: 场景ID

    Returns:
        故障场景字典，未找到返回 None
    """
    for scenario in FAULT_SCENARIOS:
        if scenario["id"] == scenario_id:
            return scenario
    return None


def get_change_records(service: str = "") -> list[dict[str, Any]]:
    """
    获取变更记录

    Args:
        service: 服务名称过滤，空字符串返回全部

    Returns:
        变更记录列表
    """
    if not service:
        return CHANGE_RECORDS.copy()
    return [r for r in CHANGE_RECORDS if r["service"] == service]


def add_fault_scenario(scenario: dict[str, Any]) -> None:
    """
    动态添加故障场景

    Args:
        scenario: 故障场景字典，必须包含 id 字段
    """
    if "id" not in scenario:
        raise ValueError("Scenario must have an 'id' field")

    # 如果已存在则更新，否则添加
    for i, existing in enumerate(FAULT_SCENARIOS):
        if existing["id"] == scenario["id"]:
            FAULT_SCENARIOS[i] = scenario
            return
    FAULT_SCENARIOS.append(scenario)


def add_metric_data(
    service: str,
    metric: str,
    scenario: str,
    data: list[float],
) -> None:
    """
    动态添加指标数据

    Args:
        service: 服务名称
        metric: 指标名称
        scenario: 场景类型
        data: 指标数据点列表
    """
    if service not in METRICS_DATASETS:
        METRICS_DATASETS[service] = {}
    if metric not in METRICS_DATASETS[service]:
        METRICS_DATASETS[service][metric] = {}
    METRICS_DATASETS[service][metric][scenario] = data


def add_change_record(record: dict[str, Any]) -> None:
    """
    动态添加变更记录

    Args:
        record: 变更记录字典
    """
    CHANGE_RECORDS.append(record)


# ============================================================
# 评估数据集类
# ============================================================


class EvaluationDatasets:
    """
    评估数据集

    提供各类评估使用的测试数据。
    """

    @staticmethod
    def get_end_to_end_samples() -> list[dict[str, Any]]:
        """
        端到端评估样本

        模拟完整的故障场景，用于评估整体处理效果。
        """
        return [
            {
                "id": "e2e-001",
                "name": "CPU 使用率过高",
                "description": "API 服务 CPU 使用率持续超过 90%",
                "alert": {
                    "service": "api-gateway",
                    "metric": "cpu_usage_percent",
                    "value": 95.2,
                    "threshold": 90.0,
                    "severity": "high",
                },
                "expected_root_cause": "请求量突增导致线程池耗尽",
                "expected_action": "自动扩容",
                "expected_duration_seconds": 300,
            },
            {
                "id": "e2e-002",
                "name": "数据库连接超时",
                "description": "订单服务频繁出现数据库连接超时",
                "alert": {
                    "service": "order-service",
                    "metric": "db_connection_timeout_rate",
                    "value": 0.35,
                    "threshold": 0.1,
                    "severity": "critical",
                },
                "expected_root_cause": "数据库连接池配置不足",
                "expected_action": "扩容连接池",
                "expected_duration_seconds": 180,
            },
            {
                "id": "e2e-003",
                "name": "内存泄漏",
                "description": "推荐服务内存持续增长，疑似内存泄漏",
                "alert": {
                    "service": "recommendation-service",
                    "metric": "memory_usage_percent",
                    "value": 92.0,
                    "threshold": 85.0,
                    "severity": "high",
                },
                "expected_root_cause": "最近部署的版本中缓存未正确释放",
                "expected_action": "回滚到上一版本",
                "expected_duration_seconds": 600,
            },
        ]

    @staticmethod
    def get_reasoning_samples() -> list[dict[str, Any]]:
        """
        推理能力评估样本

        测试 Agent 的逻辑推理和因果分析能力。
        """
        return [
            {
                "id": "reason-001",
                "scenario": "服务 A 的 QPS 在 14:00 突增 300%，同时 CPU 和内存使用率飙升",
                "evidence": [
                    "14:00 有营销活动上线",
                    "服务 A 的限流配置为 1000 QPS",
                    "数据库慢查询增加",
                ],
                "expected_cause": "营销活动导致流量突增，超出限流配置",
                "expected_actions": ["临时扩容", "调整限流阈值"],
            },
            {
                "id": "reason-002",
                "scenario": "支付服务在部署后 30 分钟内错误率从 0.1% 上升到 5%",
                "evidence": [
                    "部署时间 10:00",
                    "错误集中在支付接口",
                    "下游渠道服务正常",
                ],
                "expected_cause": "部署引入的代码缺陷导致支付接口异常",
                "expected_actions": ["回滚部署", "排查代码变更"],
            },
        ]

    @staticmethod
    def get_tool_call_samples() -> list[dict[str, Any]]:
        """
        工具调用评估样本

        测试 Agent 选择和调用工具的能力。
        """
        return [
            {
                "id": "tool-001",
                "task": "查询 api-gateway 服务最近1小时的 CPU 和内存指标",
                "expected_tools": ["get_service_metrics"],
                "expected_parameters": {
                    "service": "api-gateway",
                    "time_range": "1h",
                },
            },
            {
                "id": "tool-002",
                "task": "分析订单服务的数据库连接超时问题",
                "expected_tools": [
                    "get_service_metrics",
                    "query_topology",
                    "query_change_history",
                ],
            },
        ]

    @staticmethod
    def get_rag_samples() -> list[dict[str, Any]]:
        """
        RAG 评估样本

        测试检索增强生成的效果。
        """
        return [
            {
                "id": "rag-001",
                "query": "如何处理 Redis 连接超时问题？",
                "expected_keywords": ["Redis", "连接池", "超时", "网络"],
                "expected_completeness": True,
            },
            {
                "id": "rag-002",
                "query": "Kubernetes Pod 频繁重启的原因",
                "expected_keywords": ["Kubernetes", "Pod", "重启", "OOM", "健康检查"],
                "expected_completeness": True,
            },
        ]
