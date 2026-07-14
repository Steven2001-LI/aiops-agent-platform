"""
AIOps Agent Platform - Public Playbooks (社区来源)

从互联网公开数据中提取的运维手册。
数据来源：
- samber/awesome-prometheus-alerts (940+ 社区告警规则)
- Kubernetes 官方调试文档 (kubernetes.io/docs/tasks/debug)
- PagerDuty Incident Response 文档 (response.pagerduty.com)
- Google SRE Book 实践指南
- Prometheus 官方 Alerting Rules 推荐

这些 Playbook 可与内置 Playbook 互补，提供更全面的故障处理指导。
"""

from __future__ import annotations

from typing import Any

# ============================================================
# 社区来源 Playbook（从 awesome-prometheus-alerts 提取）
# ============================================================

PUBLIC_PLAYBOOKS: list[dict[str, Any]] = [
    # ========== Kubernetes 节点层 ==========
    {
        "id": "pub_k8s_node_cpu_high",
        "name": "K8s Node CPU Load Critical",
        "source": "awesome-prometheus-alerts / kubernetes/node-exporter",
        "source_url": "https://samber.github.io/awesome-prometheus-alerts/rules#host-and-hardware",
        "trigger": {"metric": "node_load15", "threshold": "> node_cpu_cores * 1.5"},
        "description": "节点 15 分钟平均负载超过 CPU 核心数 1.5 倍",
        "actions": [
            {"type": "check_node_processes", "tool": "top/htop"},
            {"type": "identify_high_cpu_pods", "tool": "kubectl top pods --all-namespaces"},
            {"type": "check_recent_deployments", "window_minutes": 30},
            {"type": "cordon_node", "reason": "防止新 Pod 调度到高负载节点"},
        ],
        "rollback": {"type": "uncordon_node"},
        "max_blast_radius": 0.05,
        "severity": "warning",
    },
    {
        "id": "pub_k8s_node_memory_pressure",
        "name": "K8s Node Memory Pressure",
        "source": "awesome-prometheus-alerts / kubernetes/node-exporter",
        "source_url": "https://samber.github.io/awesome-prometheus-alerts/rules#host-and-hardware",
        "trigger": {"metric": "node_memory_MemAvailable_bytes", "threshold": "< 10%"},
        "description": "节点可用内存低于 10%，可能触发 OOM Killer",
        "actions": [
            {"type": "check_oom_events", "tool": "kubectl get events --field-selector reason=OOMKilling"},
            {"type": "identify_memory_heavy_pods", "tool": "kubectl top pods --sort-by=memory"},
            {"type": "check_memory_limits", "namespace": "affected"},
            {"type": "evict_low_priority_pods", "auto": False},
        ],
        "rollback": {"type": "restore_evicted_pods"},
        "max_blast_radius": 0.1,
        "severity": "critical",
    },
    {
        "id": "pub_k8s_node_disk_filling",
        "name": "K8s Node Disk Filling Up",
        "source": "awesome-prometheus-alerts / kubernetes/node-exporter",
        "source_url": "https://samber.github.io/awesome-prometheus-alerts/rules#host-and-hardware",
        "trigger": {"metric": "node_filesystem_avail_bytes", "threshold": "< 10%"},
        "description": "节点磁盘可用空间低于 10%，预测 4 小时内将满",
        "actions": [
            {"type": "identify_disk_usage", "tool": "du -sh /var/lib/docker/*"},
            {"type": "cleanup_docker_images", "tool": "docker image prune -af"},
            {"type": "cleanup_evicted_pods", "tool": "kubectl delete pods --field-selector status.phase=Failed"},
            {"type": "rotate_logs", "retention_hours": 24},
        ],
        "rollback": None,
        "max_blast_radius": 0.03,
        "severity": "warning",
    },
    # ========== Kubernetes Pod 层 ==========
    {
        "id": "pub_k8s_pod_cpu_throttling",
        "name": "K8s Pod CPU Throttling High",
        "source": "awesome-prometheus-alerts / kubernetes/kube-state-metrics",
        "source_url": "https://samber.github.io/awesome-prometheus-alerts/rules#kubernetes-resources",
        "trigger": {"metric": "container_cpu_cfs_throttled_seconds_total", "threshold": "rate > 0.25"},
        "description": "Pod 的 CPU 使用被限流超过 25% 运行时间",
        "actions": [
            {"type": "check_cpu_limits", "target": "deployment"},
            {"type": "increase_cpu_limit", "percent": 50},
            {"type": "analyze_cpu_usage_pattern", "window_minutes": 30},
        ],
        "rollback": {"type": "restore_cpu_limit"},
        "max_blast_radius": 0.02,
        "severity": "warning",
    },
    {
        "id": "pub_k8s_pvc_filling",
        "name": "K8s PersistentVolume Filling Up",
        "source": "awesome-prometheus-alerts / kubernetes/kubelet",
        "source_url": "https://samber.github.io/awesome-prometheus-alerts/rules#kubernetes-storage",
        "trigger": {"metric": "kubelet_volume_stats_used_bytes / kubelet_volume_stats_capacity_bytes", "threshold": "> 0.85"},
        "description": "PersistentVolume 使用率超过 85%",
        "actions": [
            {"type": "check_pvc_usage", "namespace": "all"},
            {"type": "expand_pvc", "size_increase": "50Gi"},
            {"type": "cleanup_old_data", "auto": False},
        ],
        "rollback": None,
        "max_blast_radius": 0.05,
        "severity": "warning",
    },
    # ========== Kubernetes 控制面 ==========
    {
        "id": "pub_k8s_apiserver_down",
        "name": "K8s API Server Down",
        "source": "awesome-prometheus-alerts / kubernetes/api-server",
        "source_url": "https://samber.github.io/awesome-prometheus-alerts/rules#kubernetes",
        "trigger": {"metric": "up{job='apiserver'}", "threshold": "== 0"},
        "description": "Kubernetes API Server 不可达，集群管理功能丧失",
        "actions": [
            {"type": "check_apiserver_logs", "journalctl": "kube-apiserver"},
            {"type": "check_etcd_health", "target": "etcd"},
            {"type": "check_certificate_expiry", "target": "apiserver"},
            {"type": "escalate_to_k8s_admin", "severity": "critical"},
        ],
        "rollback": None,
        "max_blast_radius": 0.8,  # 控制面故障影响范围极大
        "severity": "critical",
    },
    {
        "id": "pub_k8s_etcd_leader_change",
        "name": "K8s etcd Leader Changes Frequent",
        "source": "awesome-prometheus-alerts / kubernetes/etcd",
        "source_url": "https://samber.github.io/awesome-prometheus-alerts/rules#etcd",
        "trigger": {"metric": "increase(etcd_server_leader_changes_seen_total[1h])", "threshold": "> 3"},
        "description": "1 小时内 etcd leader 变更超过 3 次，可能存在网络/磁盘问题",
        "actions": [
            {"type": "check_etcd_disk_latency", "target": "etcd nodes"},
            {"type": "check_etcd_network", "target": "etcd cluster"},
            {"type": "check_etcd_logs", "target": "etcd"},
        ],
        "rollback": None,
        "max_blast_radius": 0.6,
        "severity": "critical",
    },
    # ========== 容器运行时 ==========
    {
        "id": "pub_k8s_pod_crashloop",
        "name": "K8s Pod CrashLoopBackOff",
        "source": "Kubernetes 官方调试文档",
        "source_url": "https://kubernetes.io/docs/tasks/debug/debug-application/debug-pods/",
        "trigger": {"metric": "kube_pod_container_status_waiting_reason{reason='CrashLoopBackOff'}", "threshold": "> 0"},
        "description": "Pod 持续崩溃重启，处于 CrashLoopBackOff 状态",
        "actions": [
            {"type": "check_pod_logs", "previous": True},
            {"type": "describe_pod", "check_events": True},
            {"type": "check_resource_limits", "target": "pod"},
            {"type": "check_configmap_secrets", "mount_status": True},
            {"type": "check_readiness_probe", "target": "container"},
        ],
        "rollback": None,
        "max_blast_radius": 0.05,
        "severity": "high",
    },
    {
        "id": "pub_k8s_image_pull_error",
        "name": "K8s Image Pull BackOff",
        "source": "Kubernetes 官方调试文档",
        "source_url": "https://kubernetes.io/docs/tasks/debug/debug-application/debug-pods/",
        "trigger": {"metric": "kube_pod_container_status_waiting_reason{reason='ImagePullBackOff'}", "threshold": "> 0"},
        "description": "Pod 无法拉取容器镜像",
        "actions": [
            {"type": "check_image_tag", "verify_exists": True},
            {"type": "check_image_pull_secret", "target": "namespace"},
            {"type": "check_registry_connectivity", "target": "container registry"},
        ],
        "rollback": None,
        "max_blast_radius": 0.03,
        "severity": "high",
    },
    # ========== 应用层标准 Runbook（PagerDuty 风格） ==========
    {
        "id": "pub_app_p99_latency_spike",
        "name": "Application P99 Latency Spike — Standard Response",
        "source": "PagerDuty Incident Response / Google SRE",
        "source_url": "https://response.pagerduty.com/",
        "trigger": {"metric": "histogram_quantile(0.99, http_request_duration_seconds_bucket)", "threshold": "> baseline * 2"},
        "description": "P99 延迟超过基线的 2 倍",
        "actions": [
            {"type": "classify_severity", "method": "PagerDuty severity levels: P1(critical) / P2(high) / P3(medium)"},
            {"type": "assign_incident_commander", "role": "IC"},
            {"type": "check_dependency_health", "method": "BFS traversal"},
            {"type": "check_recent_deployments", "window_minutes": 30},
            {"type": "check_database_slow_queries", "window_minutes": 15},
            {"type": "decide_scale_or_rollback", "based_on": "evidence"},
        ],
        "rollback": {"type": "rollback_last_deployment"},
        "max_blast_radius": 0.2,
        "severity": "high",
    },
    {
        "id": "pub_app_error_budget_burn",
        "name": "Error Budget Burn Rate Critical",
        "source": "Google SRE Book — Error Budgets",
        "source_url": "https://sre.google/workbook/alerting-on-slos/",
        "trigger": {"metric": "error_budget_burn_rate", "threshold": "> 14.4 (1h window, 2% budget consumed)"},
        "description": "错误预算消耗速率严重超标",
        "actions": [
            {"type": "freeze_deployments", "reason": "error budget critically low"},
            {"type": "escalate_to_sre", "severity": "critical"},
            {"type": "initiate_blameless_postmortem", "after_resolution": True},
            {"type": "review_reliability_improvements", "next_sprint": True},
        ],
        "rollback": None,
        "max_blast_radius": 0.0,  # 这是策略层决策，不影响服务
        "severity": "critical",
    },
    # ========== 数据库层 ==========
    {
        "id": "pub_db_connection_pool_exhausted",
        "name": "Database Connection Pool Exhausted",
        "source": "PostgreSQL / MySQL 官方文档 + 社区实践",
        "source_url": "https://www.postgresql.org/docs/current/runtime-config-connection.html",
        "trigger": {"metric": "pg_stat_activity_count / max_connections", "threshold": "> 0.85"},
        "description": "数据库连接池使用率超过 85%，即将耗尽",
        "actions": [
            {"type": "check_active_connections", "query": "SELECT count(*) FROM pg_stat_activity"},
            {"type": "check_long_running_queries", "query": "SELECT * FROM pg_stat_activity WHERE state='active' AND now()-query_start > interval '30 seconds'"},
            {"type": "kill_idle_connections", "auto": False, "reason": "需人工确认"},
            {"type": "increase_pool_size", "method": "reconfigure connection pool"},
            {"type": "check_connection_leak", "target": "application code"},
        ],
        "rollback": {"type": "restore_pool_config"},
        "max_blast_radius": 0.4,
        "severity": "critical",
    },
    {
        "id": "pub_db_replication_lag",
        "name": "Database Replication Lag Critical",
        "source": "PostgreSQL / MySQL 官方文档",
        "source_url": "https://www.postgresql.org/docs/current/monitoring-stats.html",
        "trigger": {"metric": "pg_stat_replication_flush_lag", "threshold": "> 10 seconds"},
        "description": "数据库主从复制延迟超过 10 秒",
        "actions": [
            {"type": "check_replica_health", "target": "standby nodes"},
            {"type": "check_network_bandwidth", "between": "primary and replica"},
            {"type": "check_disk_io", "target": "replica nodes"},
            {"type": "reduce_primary_write_load", "method": "rate limit batch jobs"},
        ],
        "rollback": {"type": "resume_batch_jobs"},
        "max_blast_radius": 0.3,
        "severity": "high",
    },
]


# ============================================================
# 动态 Playbook 加载器
# ============================================================

class PublicPlaybookLoader:
    """
    社区 Playbook 加载器

    从多个来源加载 Playbook：
    1. 内置社区 Playbook（本文件）
    2. 文件系统 YAML/JSON 文件
    3. 未来：从 Git 仓库自动同步
    """

    @staticmethod
    def get_all() -> list[dict[str, Any]]:
        """获取所有社区 Playbook"""
        return list(PUBLIC_PLAYBOOKS)

    @staticmethod
    def get_by_source(source: str) -> list[dict[str, Any]]:
        """按来源过滤"""
        return [p for p in PUBLIC_PLAYBOOKS if source.lower() in p.get("source", "").lower()]

    @staticmethod
    def get_by_severity(severity: str) -> list[dict[str, Any]]:
        """按严重级别过滤"""
        return [p for p in PUBLIC_PLAYBOOKS if p.get("severity") == severity]

    @staticmethod
    def get_by_keyword(keyword: str) -> list[dict[str, Any]]:
        """按关键词搜索"""
        keyword_lower = keyword.lower()
        return [
            p for p in PUBLIC_PLAYBOOKS
            if keyword_lower in p.get("name", "").lower()
            or keyword_lower in p.get("description", "").lower()
            or keyword_lower in p.get("source", "").lower()
        ]

    @staticmethod
    def get_statistics() -> dict[str, Any]:
        """获取统计信息"""
        sources: dict[str, int] = {}
        severity_counts: dict[str, int] = {}
        for p in PUBLIC_PLAYBOOKS:
            # 来源统计
            source = p.get("source", "unknown")
            # 取来源的第一关键词
            source_key = source.split("/")[0].strip() if "/" in source else source.split("-")[0].strip()
            sources[source_key] = sources.get(source_key, 0) + 1
            # 严重级别统计
            sev = p.get("severity", "unknown")
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

        return {
            "total": len(PUBLIC_PLAYBOOKS),
            "by_source": sources,
            "by_severity": severity_counts,
        }


# ============================================================
# 与现有 PLAYBOOKS 合并使用
# ============================================================

def get_all_playbooks() -> list[dict[str, Any]]:
    """
    获取所有 Playbook（内置 + 社区来源）。

    在 HealAgent 中替换直接引用 PLAYBOOKS，
    改为调用此函数以同时使用社区 Playbook。
    """
    from app.data.playbooks import PLAYBOOKS
    combined = list(PLAYBOOKS)
    combined.extend(PUBLIC_PLAYBOOKS)
    return combined
