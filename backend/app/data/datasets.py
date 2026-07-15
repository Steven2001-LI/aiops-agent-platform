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
# 模拟指标数据集
# ============================================================

METRICS_DATASETS: dict[str, dict[str, dict[str, list[float]]]] = {
    "order-service": {
        "cpu_usage_percent": {
            "normal": [45, 48, 52, 50, 47, 49, 51, 46, 48, 50],
            "anomaly": [45, 48, 52, 50, 47, 49, 51, 46, 48, 50, 55, 60, 75, 85, 92, 95, 93, 88, 80, 70],
            "spike": [45, 48, 52, 50, 47, 95, 93, 88],
        },
        "memory_usage_percent": {
            "normal": [60, 62, 61, 63, 60, 62, 61, 63, 60, 62],
            "leak": [60, 62, 65, 68, 72, 78, 85, 90, 92, 95],
        },
        "p99_latency_ms": {
            "normal": [100, 105, 98, 102, 100, 103, 99, 101, 100, 102],
            "degradation": [100, 105, 150, 200, 350, 500, 800, 1200, 1500, 2000],
        },
        "error_rate_percent": {
            "normal": [0.1, 0.2, 0.1, 0.3, 0.1, 0.2, 0.1, 0.2, 0.1, 0.2],
            "spike": [0.1, 0.2, 0.1, 0.3, 5.0, 8.0, 12.0, 15.0, 10.0, 6.0],
        },
    },
    "payment-service": {
        "cpu_usage_percent": {
            "normal": [40, 42, 41, 43, 40, 42, 41, 43, 40, 42],
            "anomaly": [40, 42, 41, 43, 50, 65, 80, 88, 85, 78],
        },
        "memory_usage_percent": {
            "normal": [55, 57, 56, 58, 55, 57, 56, 58, 55, 57],
            "leak": [55, 57, 60, 65, 72, 80, 88, 92, 94, 96],
        },
        "p99_latency_ms": {
            "normal": [80, 82, 78, 85, 80, 83, 79, 81, 80, 84],
            "degradation": [80, 82, 150, 300, 600, 1000, 1500, 2000, 2500, 3000],
        },
        "error_rate_percent": {
            "normal": [0.05, 0.1, 0.05, 0.1, 0.05, 0.1, 0.05, 0.1, 0.05, 0.1],
            "spike": [0.05, 0.1, 0.05, 0.1, 2.0, 5.0, 8.0, 10.0, 6.0, 3.0],
        },
    },
    "user-service": {
        "cpu_usage_percent": {
            "normal": [35, 37, 36, 38, 35, 37, 36, 38, 35, 37],
            "anomaly": [35, 37, 36, 38, 45, 55, 70, 82, 78, 65],
        },
        "memory_usage_percent": {
            "normal": [50, 52, 51, 53, 50, 52, 51, 53, 50, 52],
            "leak": [50, 52, 55, 60, 68, 75, 82, 88, 90, 92],
        },
        "p99_latency_ms": {
            "normal": [50, 52, 48, 55, 50, 53, 49, 51, 50, 54],
            "degradation": [50, 52, 100, 250, 500, 800, 1200, 1800, 2200, 2800],
        },
        "error_rate_percent": {
            "normal": [0.1, 0.15, 0.1, 0.15, 0.1, 0.15, 0.1, 0.15, 0.1, 0.15],
            "spike": [0.1, 0.15, 0.1, 0.15, 3.0, 6.0, 10.0, 14.0, 8.0, 4.0],
        },
    },
    "api-gateway": {
        "cpu_usage_percent": {
            "normal": [30, 32, 31, 33, 30, 32, 31, 33, 30, 32],
            "anomaly": [30, 32, 31, 33, 40, 50, 65, 75, 70, 60],
        },
        "memory_usage_percent": {
            "normal": [45, 47, 46, 48, 45, 47, 46, 48, 45, 47],
            "leak": [45, 47, 50, 55, 62, 70, 78, 85, 88, 90],
        },
        "p99_latency_ms": {
            "normal": [30, 32, 28, 35, 30, 33, 29, 31, 30, 34],
            "degradation": [30, 32, 80, 150, 300, 500, 800, 1200, 1500, 1800],
        },
        "error_rate_percent": {
            "normal": [0.05, 0.1, 0.05, 0.1, 0.05, 0.1, 0.05, 0.1, 0.05, 0.1],
            "spike": [0.05, 0.1, 0.05, 0.1, 10.0, 20.0, 30.0, 25.0, 15.0, 8.0],
        },
    },
    "inventory-service": {
        "cpu_usage_percent": {
            "normal": [25, 27, 26, 28, 25, 27, 26, 28, 25, 27],
            "anomaly": [25, 27, 26, 28, 35, 45, 60, 70, 65, 55],
        },
        "memory_usage_percent": {
            "normal": [40, 42, 41, 43, 40, 42, 41, 43, 40, 42],
            "leak": [40, 42, 45, 50, 58, 68, 78, 85, 88, 90],
        },
        "p99_latency_ms": {
            "normal": [60, 62, 58, 65, 60, 63, 59, 61, 60, 64],
            "degradation": [60, 62, 120, 250, 400, 600, 900, 1300, 1600, 2000],
        },
        "error_rate_percent": {
            "normal": [0.1, 0.2, 0.1, 0.2, 0.1, 0.2, 0.1, 0.2, 0.1, 0.2],
            "spike": [0.1, 0.2, 0.1, 0.2, 4.0, 8.0, 12.0, 10.0, 6.0, 3.0],
        },
    },
}

# ============================================================
# 故障场景数据集
# ============================================================

FAULT_SCENARIOS: list[dict[str, Any]] = [
    # ========== 基础设施层 ==========
    {
        "id": "fs_001",
        "name": "Deployment-induced CPU spike",
        "description": "Recent deployment causes CPU usage to spike",
        "service": "order-service",
        "category": "infrastructure",
        "metrics": {"cpu_usage_percent": "anomaly"},
        "root_cause": "recent_deployment",
        "expected_action": {"type": "rollback_deployment", "level": "L1"},
        "expected_approval": "oncall",
        "blast_radius": 0.15,
        "severity": "high",
    },
    {
        "id": "fs_002",
        "name": "Memory leak leading to OOM",
        "description": "Gradual memory leak eventually causes OOM kills",
        "service": "payment-service",
        "category": "infrastructure",
        "metrics": {"memory_usage_percent": "leak"},
        "root_cause": "memory_leak",
        "expected_action": {"type": "restart_pod", "level": "L0"},
        "expected_approval": "auto",
        "blast_radius": 0.05,
        "severity": "critical",
    },
    {
        "id": "fs_003",
        "name": "Database connection pool exhausted",
        "description": "DB connection pool depletion causing cascading failures",
        "service": "user-service",
        "category": "infrastructure",
        "metrics": {"p99_latency_ms": "degradation", "error_rate_percent": "spike"},
        "root_cause": "database_connection_pool_exhausted",
        "expected_action": {"type": "scale_up", "level": "L1"},
        "expected_approval": "oncall",
        "blast_radius": 0.2,
        "severity": "critical",
    },
    {
        "id": "fs_004",
        "name": "Cache failure causing DB overload",
        "description": "Redis cache failure leads to direct DB queries",
        "service": "inventory-service",
        "category": "infrastructure",
        "metrics": {"p99_latency_ms": "degradation"},
        "root_cause": "cache_failure",
        "expected_action": {"type": "restart_pod", "level": "L0"},
        "expected_approval": "auto",
        "blast_radius": 0.1,
        "severity": "high",
    },
    {
        "id": "fs_005",
        "name": "Configuration error causing high error rate",
        "description": "Wrong configuration causes services to return errors",
        "service": "api-gateway",
        "category": "infrastructure",
        "metrics": {"error_rate_percent": "spike"},
        "root_cause": "configuration_error",
        "expected_action": {"type": "rollback_config", "level": "L1"},
        "expected_approval": "oncall",
        "blast_radius": 0.25,
        "severity": "critical",
    },
    # ========== 业务层 ==========
    {
        "id": "fs_biz_001",
        "name": "Payment gateway timeout — orders piling up",
        "description": "Third-party payment gateway timeout causes payment failures and order backlog in order-service",
        "service": "payment-service",
        "category": "business",
        "metrics": {"payment_failure_rate": "spike", "order_backlog_count": "spike"},
        "root_cause": "third_party_timeout",
        "expected_action": {"type": "enable_payment_fallback", "level": "L1"},
        "expected_approval": "oncall",
        "blast_radius": 0.3,
        "severity": "critical",
    },
    {
        "id": "fs_biz_002",
        "name": "Order processing stuck — inventory sync failure",
        "description": "Inventory service returns stale data causing order-processor to deadlock on stock validation",
        "service": "order-service",
        "category": "business",
        "metrics": {"order_backlog_count": "spike"},
        "root_cause": "inventory_data_stale",
        "expected_action": {"type": "reconcile_inventory", "level": "L1"},
        "expected_approval": "team_lead",
        "blast_radius": 0.25,
        "severity": "critical",
    },
    {
        "id": "fs_biz_003",
        "name": "User login failure spike — SSO token cache expired",
        "description": "Redis cache expires all SSO tokens causing mass login failures across user-service",
        "service": "user-service",
        "category": "business",
        "metrics": {"auth_failure_rate": "spike"},
        "root_cause": "token_cache_expired",
        "expected_action": {"type": "enable_static_auth", "level": "L1"},
        "expected_approval": "oncall",
        "blast_radius": 0.4,
        "severity": "critical",
    },
    {
        "id": "fs_biz_004",
        "name": "Rate limiter false-positive — blocking legitimate traffic",
        "description": "API gateway rate limiter misconfigured, blocking valid user requests during promotion event",
        "service": "api-gateway",
        "category": "business",
        "metrics": {"rate_limit_hit_rate": "spike", "error_rate_percent": "spike"},
        "root_cause": "rate_limit_misconfig",
        "expected_action": {"type": "adjust_rate_limit", "level": "L1"},
        "expected_approval": "oncall",
        "blast_radius": 0.1,
        "severity": "high",
    },
    {
        "id": "fs_biz_005",
        "name": "Message queue backlog — consumer group stuck",
        "description": "Kafka consumer group stuck on corrupted message, causing 5000+ message lag on order-processing topic",
        "service": "order-service",
        "category": "business",
        "metrics": {"mq_consumer_lag": "spike"},
        "root_cause": "corrupted_message",
        "expected_action": {"type": "skip_corrupted_message", "level": "L0"},
        "expected_approval": "auto",
        "blast_radius": 0.12,
        "severity": "high",
    },
    {
        "id": "fs_biz_006",
        "name": "Data inconsistency — DB-ES replication lag",
        "description": "MySQL-to-Elasticsearch replication lag causes search results to show incorrect inventory counts",
        "service": "inventory-service",
        "category": "business",
        "metrics": {"replication_lag_seconds": "spike"},
        "root_cause": "replication_lag",
        "expected_action": {"type": "force_reindex", "level": "L1"},
        "expected_approval": "team_lead",
        "blast_radius": 0.15,
        "severity": "medium",
    },
    # ========== 纯业务异常（新增，不依赖指标） ==========
    {
        "id": "fs_biz_007",
        "name": "重复扣款 — 支付幂等性失败",
        "description": "支付回调因网络抖动重试3次，导致用户同一笔订单被扣款3次。依赖 payment_idempotency_key 失效",
        "service": "payment-service",
        "category": "business_logic",
        "business_rules": ["br_duplicate_charge"],
        "root_cause": "payment_idempotency_failure",
        "expected_action": {"type": "refund_duplicates", "level": "L2"},
        "expected_approval": "team_lead",
        "blast_radius": 0.05,
        "severity": "critical",
    },
    {
        "id": "fs_biz_008",
        "name": "库存超卖 — 并发下单竞态条件",
        "description": "秒杀活动期间，库存缓存未及时更新，10个用户同时下单成功但实际只剩3件库存",
        "service": "inventory-service",
        "category": "business_logic",
        "business_rules": ["br_oversell"],
        "root_cause": "inventory_sync_failure",
        "expected_action": {"type": "cancel_oversold_orders", "level": "L2"},
        "expected_approval": "team_lead",
        "blast_radius": 0.15,
        "severity": "critical",
    },
    {
        "id": "fs_biz_009",
        "name": "订单金额对账不平 — 优惠券计算错误",
        "description": "满减券在多SKU订单中比例分摊计算有精度问题，导致订单总额与实付金额相差0.02元",
        "service": "order-service",
        "category": "business_logic",
        "business_rules": ["br_amount_mismatch"],
        "root_cause": "accounting_reconciliation_error",
        "expected_action": {"type": "reconcile_transactions", "level": "L1"},
        "expected_approval": "oncall",
        "blast_radius": 0.08,
        "severity": "high",
    },
    {
        "id": "fs_biz_010",
        "name": "支付回调丢失 — 订单状态不一致",
        "description": "支付网关回调 webhook 因证书过期返回 500，导致 50 笔已支付订单仍显示待支付",
        "service": "payment-service",
        "category": "business_logic",
        "business_rules": ["br_payment_callback_loss"],
        "root_cause": "third_party_callback_loss",
        "expected_action": {"type": "reconcile_payment_status", "level": "L1"},
        "expected_approval": "oncall",
        "blast_radius": 0.1,
        "severity": "critical",
    },
]

# ============================================================
# 变更记录数据集(模拟 CMDB/发布系统)
# ============================================================
# 时间戳相对进程启动时刻生成:此前是写死的 2024-01 固定日期,
# 消费方(RCA 近期变更检查 / knowledge_tools.get_recent_changes)的
# "最近 N 分钟"时间窗过滤对固定旧日期恒为空,"近期"语义是假的。
# 偏移保持原数据的先后顺序;order-service 的 deployment 保持在
# 默认 60 分钟窗口内(RCA 知识库 deployment_issue 加成依赖它)。


def _change_ts(minutes_ago: int) -> str:
    """生成相对当前时刻的 ISO 时间戳(aware UTC)。"""
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


CHANGE_RECORDS: list[dict[str, Any]] = [
    {
        "service": "order-service",
        "type": "deployment",
        "timestamp": _change_ts(45),
        "author": "developer-a",
        "description": "Update order processing logic",
    },
    {
        "service": "payment-service",
        "type": "config_change",
        "timestamp": _change_ts(30),
        "author": "developer-b",
        "description": "Increase timeout settings",
    },
    {
        "service": "user-service",
        "type": "deployment",
        "timestamp": _change_ts(180),
        "author": "developer-c",
        "description": "Add new authentication flow",
    },
    {
        "service": "api-gateway",
        "type": "config_change",
        "timestamp": _change_ts(210),
        "author": "developer-a",
        "description": "Update rate limiting rules",
    },
    {
        "service": "inventory-service",
        "type": "deployment",
        "timestamp": _change_ts(20 * 60),
        "author": "developer-d",
        "description": "Optimize query performance",
    },
]


# ============================================================
# 业务事件数据集 - BusinessMonitorAgent 规则引擎的数据源
# ============================================================
# 结构: {场景: {数据源: [事件记录]}}。"normal" 为不命中任何规则的日常记录;
# fs_biz_007~010 为对应 FAULT_SCENARIOS 纯业务故障场景准备的异常记录。
# 与 METRICS_DATASETS 的 normal/anomaly 分档同一思路。

BUSINESS_EVENTS: dict[str, dict[str, list[dict[str, Any]]]] = {
    "normal": {
        "payment_transactions": [
            {"user_id": "u_001", "order_id": "ord_1001", "amount": 99.00, "charged_at": "2026-07-01T10:00:00+00:00"},
            {"user_id": "u_002", "order_id": "ord_1002", "amount": 59.00, "charged_at": "2026-07-01T10:05:00+00:00"},
        ],
        "orders": [
            {"order_id": "ord_1001", "product_id": "sku_100", "quantity": 1, "status": "paid", "amount": 99.00, "updated_at": "2026-07-01T10:01:00+00:00"},
            {"order_id": "ord_1002", "product_id": "sku_200", "quantity": 2, "status": "paid", "amount": 59.00, "updated_at": "2026-07-01T10:06:00+00:00"},
        ],
        "products": [
            {"product_id": "sku_100", "stock": 50},
            {"product_id": "sku_200", "stock": 30},
        ],
        "payments": [
            {"order_id": "ord_1001", "status": "success", "amount": 99.00},
            {"order_id": "ord_1002", "status": "success", "amount": 59.00},
        ],
        "coupon_usage": [
            {"coupon_id": "cp_500", "user_id": "u_001", "used_at": "2026-07-01T09:58:00+00:00"},
            {"coupon_id": "cp_501", "user_id": "u_002", "used_at": "2026-07-01T10:03:00+00:00"},
        ],
    },
    # fs_biz_007: 支付回调重试导致同一 (user, order) 5 分钟内扣款 3 次
    "fs_biz_007": {
        "payment_transactions": [
            {"user_id": "u_100", "order_id": "ord_2001", "amount": 199.00, "charged_at": "2026-07-02T09:00:00+00:00"},
            {"user_id": "u_100", "order_id": "ord_2001", "amount": 199.00, "charged_at": "2026-07-02T09:01:10+00:00"},
            {"user_id": "u_100", "order_id": "ord_2001", "amount": 199.00, "charged_at": "2026-07-02T09:02:30+00:00"},
        ],
    },
    # fs_biz_008: 秒杀竞态,已付/处理中订单合计 10 件 > 实际库存 3 件
    "fs_biz_008": {
        "orders": [
            {"order_id": f"ord_30{i:02d}", "product_id": "sku_flash", "quantity": 1, "status": "paid" if i % 2 == 0 else "processing", "amount": 39.00, "updated_at": "2026-07-03T12:00:00+00:00"}
            for i in range(10)
        ],
        "products": [
            {"product_id": "sku_flash", "stock": 3},
        ],
    },
    # fs_biz_009: 优惠券分摊精度问题,订单金额与实付相差 0.02 元
    "fs_biz_009": {
        "orders": [
            {"order_id": "ord_4001", "product_id": "sku_100", "quantity": 3, "status": "paid", "amount": 100.00, "updated_at": "2026-07-04T15:00:00+00:00"},
        ],
        "payments": [
            {"order_id": "ord_4001", "status": "success", "amount": 99.98},
        ],
    },
    # fs_biz_010: 支付网关已扣款成功,但订单状态仍未更新为已支付
    "fs_biz_010": {
        "payments": [
            {"order_id": "ord_5001", "status": "success", "amount": 59.00},
            {"order_id": "ord_5002", "status": "success", "amount": 89.00},
        ],
        "orders": [
            {"order_id": "ord_5001", "product_id": "sku_200", "quantity": 1, "status": "pending_payment", "amount": 59.00, "updated_at": "2026-07-05T08:00:00+00:00"},
            {"order_id": "ord_5002", "product_id": "sku_200", "quantity": 1, "status": "pending_payment", "amount": 89.00, "updated_at": "2026-07-05T08:02:00+00:00"},
        ],
    },
}


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
