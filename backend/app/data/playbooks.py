"""
AIOps Agent Platform - Playbook Definitions

运维手册(Playbook)定义，包含标准故障处理流程。
"""

from __future__ import annotations

from typing import Any


# ============================================================
# 自愈 Playbook 定义 - 用于 HealAgent 匹配和执行
# ============================================================

PLAYBOOKS: list[dict[str, Any]] = [
    # ========== 基础设施层 ==========
    {
        "id": "pb_high_cpu",
        "name": "High CPU Usage",
        "trigger": {"metric": "cpu_usage_percent", "threshold": 80},
        "actions": [
            {"type": "scale_up", "target": "deployment", "replicas": "+2"},
            {"type": "restart", "target": "pod", "selector": "app={service}"},
        ],
        "rollback": {"type": "scale_down", "replicas": "-2"},
        "max_blast_radius": 0.1,
    },
    {
        "id": "pb_memory_leak",
        "name": "Memory Leak",
        "trigger": {"metric": "memory_usage_percent", "threshold": 90},
        "actions": [
            {"type": "restart", "target": "pod", "selector": "app={service}"},
        ],
        "rollback": {"type": "rollback_deployment", "revision": -1},
        "max_blast_radius": 0.05,
    },
    {
        "id": "pb_disk_full",
        "name": "Disk Full",
        "trigger": {"metric": "disk_usage_percent", "threshold": 85},
        "actions": [
            {"type": "cleanup_logs", "retention_days": 7},
            {"type": "expand_volume", "size": "+50Gi"},
        ],
        "rollback": None,
        "max_blast_radius": 0.02,
    },
    {
        "id": "pb_high_latency",
        "name": "High Latency",
        "trigger": {"metric": "p99_latency_ms", "threshold": 1000},
        "actions": [
            {"type": "circuit_breaker", "threshold": 0.5},
            {"type": "rate_limit", "rps": 100},
        ],
        "rollback": {"type": "disable_circuit_breaker"},
        "max_blast_radius": 0.15,
    },
    {
        "id": "pb_error_rate",
        "name": "High Error Rate",
        "trigger": {"metric": "error_rate_percent", "threshold": 5},
        "actions": [
            {"type": "rollback_deployment", "revision": -1},
            {"type": "alert_oncall", "severity": "critical"},
        ],
        "rollback": {"type": "redeploy", "revision": "current"},
        "max_blast_radius": 0.2,
    },
    # ========== 业务层 ==========
    {
        "id": "pb_payment_failure",
        "name": "Payment Processing Failure",
        "trigger": {"metric": "payment_failure_rate", "threshold": 0.02},
        "actions": [
            {"type": "check_payment_gateway", "target": "third_party"},
            {"type": "enable_payment_fallback", "method": "offline_queue"},
            {"type": "reconcile_transactions", "window_minutes": 30},
        ],
        "rollback": {"type": "disable_fallback"},
        "max_blast_radius": 0.3,
    },
    {
        "id": "pb_order_stuck",
        "name": "Order Processing Stuck",
        "trigger": {"metric": "order_backlog_count", "threshold": 100},
        "actions": [
            {"type": "check_inventory_service", "health_check": True},
            {"type": "check_payment_service", "health_check": True},
            {"type": "drain_queue", "target": "order-processing-queue"},
            {"type": "scale_workers", "target": "order-processor", "replicas": "+3"},
        ],
        "rollback": {"type": "scale_down_workers", "replicas": "-3"},
        "max_blast_radius": 0.25,
    },
    {
        "id": "pb_inventory_inconsistency",
        "name": "Inventory Data Inconsistency",
        "trigger": {"metric": "inventory_mismatch_count", "threshold": 10},
        "actions": [
            {"type": "reconcile_inventory", "method": "full_audit"},
            {"type": "pause_orders", "service": "order-service"},
            {"type": "sync_elasticsearch", "force": True},
        ],
        "rollback": {"type": "resume_orders"},
        "max_blast_radius": 0.15,
    },
    {
        "id": "pb_auth_failure",
        "name": "Authentication Service Failure",
        "trigger": {"metric": "auth_failure_rate", "threshold": 0.05},
        "actions": [
            {"type": "check_sso_service", "health_check": True},
            {"type": "check_token_cache", "service": "redis-cache"},
            {"type": "enable_static_auth", "fallback": True},
        ],
        "rollback": {"type": "disable_static_auth"},
        "max_blast_radius": 0.4,
    },
    {
        "id": "pb_rate_limit_triggered",
        "name": "API Rate Limiting Triggered",
        "trigger": {"metric": "rate_limit_hit_rate", "threshold": 0.1},
        "actions": [
            {"type": "analyze_traffic_pattern", "window_minutes": 15},
            {"type": "adjust_rate_limit", "increase_percent": 50},
            {"type": "block_suspicious_ips", "auto": True},
        ],
        "rollback": {"type": "restore_rate_limit"},
        "max_blast_radius": 0.1,
    },
    {
        "id": "pb_mq_backlog",
        "name": "Message Queue Backlog",
        "trigger": {"metric": "mq_consumer_lag", "threshold": 5000},
        "actions": [
            {"type": "check_consumer_health", "consumer_group": "main"},
            {"type": "scale_consumers", "replicas": "+5"},
            {"type": "check_dlq", "dead_letter_queue": True},
        ],
        "rollback": {"type": "scale_down_consumers", "replicas": "-5"},
        "max_blast_radius": 0.12,
    },
    # ========== 业务异常（新增） ==========
    {
        "id": "pb_duplicate_charge",
        "name": "Duplicate Charge Remediation",
        "trigger": {"rule": "br_duplicate_charge"},
        "actions": [
            {"type": "verify_duplicate_charges", "method": "check_payment_gateway"},
            {"type": "mark_duplicates_for_refund", "auto": False},
            {"type": "fix_payment_idempotency", "target": "payment-service"},
            {"type": "notify_affected_users", "channel": "email"},
        ],
        "rollback": {"type": "none", "description": "退款不可自动回滚，需人工审核"},
        "max_blast_radius": 0.05,
    },
    {
        "id": "pb_oversold_inventory",
        "name": "Oversold Inventory Remediation",
        "trigger": {"rule": "br_oversell"},
        "actions": [
            {"type": "pause_product_listings", "auto": True},
            {"type": "identify_oversold_orders"},
            {"type": "cancel_oversold_orders", "auto": False},
            {"type": "reconcile_inventory", "method": "full_audit"},
        ],
        "rollback": {"type": "resume_product_listings"},
        "max_blast_radius": 0.15,
    },
    {
        "id": "pb_amount_mismatch",
        "name": "Amount Mismatch Remediation",
        "trigger": {"rule": "br_amount_mismatch"},
        "actions": [
            {"type": "reconcile_transactions", "window_hours": 24},
            {"type": "check_payment_gateway_logs"},
            {"type": "fix_amount_calculation", "auto": False},
        ],
        "rollback": {"type": "none"},
        "max_blast_radius": 0.08,
    },
    {
        "id": "pb_payment_callback_loss",
        "name": "Payment Callback Loss Remediation",
        "trigger": {"rule": "br_payment_callback_loss"},
        "actions": [
            {"type": "reconcile_payment_status", "window_hours": 1},
            {"type": "update_order_status", "batch_size": 100},
            {"type": "check_webhook_endpoint", "health_check": True},
        ],
        "rollback": {"type": "none", "description": "订单状态更新需人工确认"},
        "max_blast_radius": 0.1,
    },
]


class PlaybookDefinitions:
    """
    Playbook 定义

    标准化的故障处理操作流程。
    """

    @staticmethod
    def get_all_playbooks() -> list[dict[str, Any]]:
        """获取所有 Playbook"""
        return [
            PlaybookDefinitions._cpu_high_playbook(),
            PlaybookDefinitions._db_timeout_playbook(),
            PlaybookDefinitions._memory_leak_playbook(),
            PlaybookDefinitions._network_timeout_playbook(),
        ]

    @staticmethod
    def _cpu_high_playbook() -> dict[str, Any]:
        """CPU 使用率过高处理手册"""
        return {
            "id": "pb-cpu-high",
            "name": "CPU 使用率过高处理",
            "description": "处理服务 CPU 使用率过高的标准流程",
            "triggers": ["cpu_usage_percent > 80"],
            "severity": "high",
            "steps": [
                {
                    "step": 1,
                    "action": "check_metrics",
                    "description": "确认 CPU 指标和趋势",
                    "tools": ["get_service_metrics"],
                    "timeout_seconds": 30,
                },
                {
                    "step": 2,
                    "action": "analyze_processes",
                    "description": "分析高 CPU 进程",
                    "tools": ["query_process_list"],
                    "timeout_seconds": 30,
                },
                {
                    "step": 3,
                    "action": "check_recent_changes",
                    "description": "检查近期变更",
                    "tools": ["query_change_history"],
                    "timeout_seconds": 30,
                },
                {
                    "step": 4,
                    "action": "decide_action",
                    "description": "根据分析结果决策",
                    "branches": {
                        "traffic_spike": "scale_horizontally",
                        "code_issue": "rollback_or_hotfix",
                        "scheduled_task": "reschedule_task",
                    },
                    "timeout_seconds": 30,
                },
                {
                    "step": 5,
                    "action": "verify_fix",
                    "description": "验证修复效果",
                    "tools": ["get_service_metrics"],
                    "timeout_seconds": 120,
                },
            ],
        }

    @staticmethod
    def _db_timeout_playbook() -> dict[str, Any]:
        """数据库连接超时处理手册"""
        return {
            "id": "pb-db-timeout",
            "name": "数据库连接超时处理",
            "description": "处理数据库连接超时的标准流程",
            "triggers": ["db_connection_timeout_rate > 0.1"],
            "severity": "critical",
            "steps": [
                {
                    "step": 1,
                    "action": "check_db_metrics",
                    "description": "检查数据库指标",
                    "tools": ["get_service_metrics", "query_metrics"],
                    "timeout_seconds": 30,
                },
                {
                    "step": 2,
                    "action": "check_slow_queries",
                    "description": "检查慢查询",
                    "tools": ["query_slow_queries"],
                    "timeout_seconds": 30,
                },
                {
                    "step": 3,
                    "action": "check_connection_pool",
                    "description": "检查连接池状态",
                    "tools": ["query_connection_pool_status"],
                    "timeout_seconds": 30,
                },
                {
                    "step": 4,
                    "action": "expand_pool_or_optimize",
                    "description": "扩容连接池或优化查询",
                    "branches": {
                        "pool_exhausted": "expand_connection_pool",
                        "slow_query": "optimize_query",
                        "db_overload": "implement_caching",
                    },
                    "timeout_seconds": 60,
                },
            ],
        }

    @staticmethod
    def _memory_leak_playbook() -> dict[str, Any]:
        """内存泄漏处理手册"""
        return {
            "id": "pb-memory-leak",
            "name": "内存泄漏处理",
            "description": "处理服务内存泄漏的标准流程",
            "triggers": ["memory_usage_percent > 85 and continuously_increasing"],
            "severity": "high",
            "steps": [
                {
                    "step": 1,
                    "action": "confirm_leak",
                    "description": "确认是否为内存泄漏",
                    "tools": ["get_service_metrics"],
                    "timeout_seconds": 60,
                },
                {
                    "step": 2,
                    "action": "dump_heap",
                    "description": "获取堆内存 dump",
                    "tools": ["capture_heap_dump"],
                    "timeout_seconds": 120,
                },
                {
                    "step": 3,
                    "action": "analyze_dump",
                    "description": "分析堆内存 dump",
                    "tools": ["analyze_heap_dump"],
                    "timeout_seconds": 300,
                },
                {
                    "step": 4,
                    "action": "temporary_fix",
                    "description": "临时修复",
                    "branches": {
                        "identified_cause": "hotfix_or_rollback",
                        "unknown": "schedule_restart",
                    },
                    "timeout_seconds": 60,
                },
            ],
        }

    @staticmethod
    def _network_timeout_playbook() -> dict[str, Any]:
        """网络超时处理手册"""
        return {
            "id": "pb-network-timeout",
            "name": "服务间调用超时处理",
            "description": "处理服务间调用超时的标准流程",
            "triggers": ["downstream_call_timeout_rate > 0.05"],
            "severity": "high",
            "steps": [
                {
                    "step": 1,
                    "action": "check_network",
                    "description": "检查网络连通性",
                    "tools": ["ping_test", "traceroute"],
                    "timeout_seconds": 30,
                },
                {
                    "step": 2,
                    "action": "check_downstream",
                    "description": "检查下游服务状态",
                    "tools": ["get_service_metrics", "health_check"],
                    "timeout_seconds": 30,
                },
                {
                    "step": 3,
                    "action": "check_circuit_breaker",
                    "description": "检查熔断器状态",
                    "tools": ["query_circuit_breaker_status"],
                    "timeout_seconds": 30,
                },
                {
                    "step": 4,
                    "action": "implement_fallback",
                    "description": "实施降级策略",
                    "tools": ["enable_fallback", "adjust_timeout"],
                    "timeout_seconds": 60,
                },
            ],
        }
