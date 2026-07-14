"""
AIOps Agent Platform - Knowledge Base

运维知识库数据结构和加载。
"""

from __future__ import annotations

from typing import Any


# ============================================================
# 知识库数据 - 用于 RCAAgent 的 RAG 增强
# ============================================================

KNOWLEDGE_BASE: list[dict[str, Any]] = [
    # ========== 基础设施层 ==========
    {
        "id": "kb_001",
        "category": "deployment_issue",
        "symptoms": ["high_cpu", "increased_latency"],
        "root_causes": ["recent_deployment", "configuration_change"],
        "solutions": ["rollback_deployment", "check_configuration"],
        "confidence_boost": 0.2,
    },
    {
        "id": "kb_002",
        "category": "resource_exhaustion",
        "symptoms": ["high_memory", "oom_killed"],
        "root_causes": ["memory_leak", "insufficient_resources"],
        "solutions": ["restart_pod", "scale_up_resources"],
        "confidence_boost": 0.15,
    },
    {
        "id": "kb_003",
        "category": "dependency_failure",
        "symptoms": ["high_error_rate", "timeout"],
        "root_causes": ["upstream_service_down", "network_issue"],
        "solutions": ["check_dependencies", "failover"],
        "confidence_boost": 0.18,
    },
    # ========== 业务层 ==========
    {
        "id": "kb_biz_001",
        "category": "payment_failure",
        "symptoms": ["payment_timeout", "transaction_failed", "order_backlog"],
        "root_causes": ["payment_gateway_timeout", "third_party_unavailable", "account_balance_insufficient"],
        "solutions": ["enable_payment_fallback", "retry_with_backoff", "reconcile_transactions"],
        "confidence_boost": 0.22,
    },
    {
        "id": "kb_biz_002",
        "category": "order_processing_failure",
        "symptoms": ["order_stuck", "inventory_mismatch", "shipping_delay"],
        "root_causes": ["inventory_data_stale", "payment_not_confirmed", "warehouse_system_down"],
        "solutions": ["reconcile_inventory", "manual_order_release", "check_payment_status"],
        "confidence_boost": 0.19,
    },
    {
        "id": "kb_biz_003",
        "category": "authentication_failure",
        "symptoms": ["login_failed", "token_expired", "sso_error"],
        "root_causes": ["token_cache_expired", "sso_service_down", "certificate_expired"],
        "solutions": ["enable_static_auth", "refresh_token_cache", "check_sso_health"],
        "confidence_boost": 0.21,
    },
    {
        "id": "kb_biz_004",
        "category": "rate_limiting",
        "symptoms": ["rate_limit_hit", "request_blocked", "throttle_error"],
        "root_causes": ["rate_limit_misconfig", "traffic_surge", "ddos_attack"],
        "solutions": ["adjust_rate_limit", "whitelist_ips", "enable_caching"],
        "confidence_boost": 0.16,
    },
    {
        "id": "kb_biz_005",
        "category": "message_queue_issue",
        "symptoms": ["consumer_lag", "message_loss", "dead_letter_queue_full"],
        "root_causes": ["corrupted_message", "consumer_stuck", "partition_imbalance"],
        "solutions": ["skip_corrupted_message", "restart_consumer", "rebalance_partitions"],
        "confidence_boost": 0.17,
    },
    {
        "id": "kb_biz_006",
        "category": "data_inconsistency",
        "symptoms": ["stale_data", "replication_lag", "cache_mismatch"],
        "root_causes": ["replication_lag", "cache_not_invalidated", "etl_pipeline_failed"],
        "solutions": ["force_reindex", "invalidate_cache", "trigger_reconciliation"],
        "confidence_boost": 0.18,
    },
    # ========== 业务异常（新增） ==========
    {
        "id": "kb_biz_007",
        "category": "duplicate_charge",
        "symptoms": ["duplicate_charge", "double_billing", "charge_repeated"],
        "root_causes": ["payment_idempotency_failure", "callback_retry_storm", "message_dedup_failure"],
        "solutions": ["verify_duplicate_charges", "refund_duplicates", "fix_payment_idempotency"],
        "confidence_boost": 0.25,
    },
    {
        "id": "kb_biz_008",
        "category": "oversold_inventory",
        "symptoms": ["oversold", "negative_stock", "order_cannot_fulfill"],
        "root_causes": ["inventory_sync_failure", "cache_not_invalidated", "concurrent_order_race"],
        "solutions": ["pause_product_listings", "reconcile_inventory", "cancel_oversold_orders"],
        "confidence_boost": 0.23,
    },
    {
        "id": "kb_biz_009",
        "category": "amount_mismatch",
        "symptoms": ["amount_mismatch", "order_payment_diff", "accounting_error"],
        "root_causes": ["rounding_error", "currency_conversion_bug", "discount_calculation_error"],
        "solutions": ["reconcile_transactions", "fix_amount_calculation", "audit_discount_logic"],
        "confidence_boost": 0.20,
    },
    {
        "id": "kb_biz_010",
        "category": "payment_callback_loss",
        "symptoms": ["payment_success_order_unpaid", "callback_missing", "status_inconsistent"],
        "root_causes": ["webhook_endpoint_down", "network_timeout", "message_queue_full"],
        "solutions": ["reconcile_payment_status", "retry_missed_callbacks", "check_webhook_endpoint"],
        "confidence_boost": 0.22,
    },
]


# ============================================================
# 服务拓扑数据 - 用于 RCAAgent 的依赖分析
# ============================================================

SERVICE_TOPOLOGY: dict[str, dict[str, Any]] = {
    "order-service": {
        "dependencies": ["payment-service", "inventory-service", "user-service"],
        "dependents": ["api-gateway"],
        "namespace": "production",
        "tier": "critical",
    },
    "payment-service": {
        "dependencies": ["mysql-primary", "redis-cache"],
        "dependents": ["order-service"],
        "namespace": "production",
        "tier": "critical",
    },
    "inventory-service": {
        "dependencies": ["elasticsearch", "mysql-primary"],
        "dependents": ["order-service"],
        "namespace": "production",
        "tier": "standard",
    },
    "user-service": {
        "dependencies": ["mysql-primary", "redis-cache"],
        "dependents": ["order-service", "api-gateway"],
        "namespace": "production",
        "tier": "critical",
    },
    "api-gateway": {
        "dependencies": ["order-service", "user-service"],
        "dependents": [],
        "namespace": "production",
        "tier": "critical",
    },
    "mysql-primary": {
        "dependencies": [],
        "dependents": ["payment-service", "inventory-service", "user-service"],
        "namespace": "data",
        "tier": "critical",
    },
    "redis-cache": {
        "dependencies": [],
        "dependents": ["payment-service", "user-service"],
        "namespace": "data",
        "tier": "standard",
    },
    "elasticsearch": {
        "dependencies": [],
        "dependents": ["inventory-service"],
        "namespace": "data",
        "tier": "standard",
    },
}


class KnowledgeBase:
    """
    运维知识库

    包含故障处理经验、最佳实践和解决方案。
    """

    @staticmethod
    def get_fault_solutions() -> list[dict[str, Any]]:
        """获取常见故障解决方案"""
        return [
            {
                "id": "sol-001",
                "category": "performance",
                "title": "CPU 使用率过高",
                "symptoms": [
                    "CPU 使用率持续超过 80%",
                    "请求响应时间增加",
                    "服务吞吐量下降",
                ],
                "causes": [
                    "流量突增",
                    "低效代码",
                    "资源竞争",
                    "定时任务影响",
                ],
                "solutions": [
                    "水平扩容实例",
                    "优化热点代码",
                    "调整限流策略",
                    "分离定时任务",
                ],
                "playbook_id": "pb-cpu-high",
            },
            {
                "id": "sol-002",
                "category": "database",
                "title": "数据库连接超时",
                "symptoms": [
                    "数据库连接超时错误增加",
                    "接口响应时间波动",
                    "连接池耗尽告警",
                ],
                "causes": [
                    "连接池配置不足",
                    "慢查询",
                    "网络延迟",
                    "数据库锁等待",
                ],
                "solutions": [
                    "扩容连接池",
                    "优化慢查询",
                    "添加读写分离",
                    "实施连接池监控",
                ],
                "playbook_id": "pb-db-timeout",
            },
            {
                "id": "sol-003",
                "category": "memory",
                "title": "内存泄漏",
                "symptoms": [
                    "内存使用率持续增长",
                    "GC 频率增加",
                    "服务重启后恢复",
                ],
                "causes": [
                    "未释放的对象引用",
                    "缓存无限增长",
                    "线程泄漏",
                ],
                "solutions": [
                    "分析堆内存 dump",
                    "检查缓存 TTL 配置",
                    "代码审查对象生命周期",
                    "临时重启并回滚版本",
                ],
                "playbook_id": "pb-memory-leak",
            },
            {
                "id": "sol-004",
                "category": "network",
                "title": "服务间调用超时",
                "symptoms": [
                    "下游调用超时增加",
                    "熔断器频繁触发",
                    "级联故障",
                ],
                "causes": [
                    "网络分区",
                    "下游服务过载",
                    "超时配置不合理",
                ],
                "solutions": [
                    "检查网络连通性",
                    "实施熔断降级",
                    "调整超时配置",
                    "增加重试策略",
                ],
                "playbook_id": "pb-network-timeout",
            },
        ]

    @staticmethod
    def get_best_practices() -> list[dict[str, Any]]:
        """获取运维最佳实践"""
        return [
            {
                "id": "bp-001",
                "category": "monitoring",
                "title": "黄金指标监控",
                "description": "每个服务必须监控四大黄金指标",
                "items": [
                    "延迟(Latency)",
                    "流量(Traffic)",
                    "错误率(Errors)",
                    "饱和度(Saturation)",
                ],
            },
            {
                "id": "bp-002",
                "category": "deployment",
                "title": "安全部署实践",
                "description": "部署时的安全注意事项",
                "items": [
                    "蓝绿部署或金丝雀发布",
                    "部署前执行自动化测试",
                    "准备回滚方案",
                    "部署期间加强监控",
                ],
            },
        ]

    @staticmethod
    def get_service_topology() -> dict[str, dict[str, Any]]:
        """获取服务拓扑"""
        return SERVICE_TOPOLOGY

    @staticmethod
    def get_knowledge_base() -> list[dict[str, Any]]:
        """获取知识库"""
        return KNOWLEDGE_BASE
