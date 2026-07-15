"""
AIOps Agent Platform - Metric Mapper

将症状映射到具体的监控指标，生成诊断查询计划。
这是"大白话 → 可观测数据"的核心转换层。
"""

from __future__ import annotations


from pydantic import BaseModel, Field

from app.data.knowledge_base import SERVICE_TOPOLOGY


class DiagnosticQuery(BaseModel):
    """单条诊断查询"""
    target: str = Field(description="目标服务/组件")
    metric: str = Field(description="指标名称")
    source: str = Field(default="prometheus", description="数据来源")
    priority: int = Field(default=1, description="优先级(1最高)")
    reason: str = Field(default="", description="为什么查这个指标")


class DiagnosisPlan(BaseModel):
    """诊断计划"""
    queries: list[DiagnosticQuery] = Field(default_factory=list)
    service: str = Field(default="", description="主服务")
    symptoms: list[str] = Field(default_factory=list)
    business_domain: str = Field(default="", description="业务域")


class MetricMapper:
    """
    症状 → 指标映射器

    将模糊的症状描述（如"慢"、"挂了"）映射到具体的、
    可观测的监控指标，并生成按优先级排序的诊断查询计划。
    """

    # 症状 → 指标映射（核心映射表）
    SYMPTOM_METRIC_MAP: dict[str, list[tuple[str, str, str, int]]] = {
        # 格式: 症状 → [(指标名, 查询维度, 说明, 优先级)]
        "high_latency": [
            ("p99_latency_ms", "service", "P99 延迟（首选指标）", 1),
            ("avg_latency_ms", "service", "平均延迟", 2),
            ("p99_latency_ms", "dependency", "上游依赖延迟", 3),
        ],
        "timeout": [
            ("request_timeout_rate", "service", "请求超时率", 1),
            ("error_rate_percent", "service", "错误率（辅助判断）", 2),
            ("p99_latency_ms", "dependency", "依赖服务延迟", 3),
        ],
        "high_cpu": [
            ("cpu_usage_percent", "service", "CPU 使用率", 1),
            ("container_cpu_throttled", "pod", "CPU 限流", 2),
        ],
        "high_memory": [
            ("memory_usage_percent", "service", "内存使用率", 1),
            ("oom_killed_count", "pod", "OOM Kill 次数", 2),
        ],
        "memory_leak": [
            ("memory_usage_percent", "service", "内存持续增长", 1),
            ("gc_pause_ms", "pod", "GC 暂停时间", 2),
        ],
        "high_error_rate": [
            ("error_rate_percent", "service", "错误率", 1),
            ("http_5xx_count", "service", "5xx 错误数", 2),
            ("exception_count", "pod", "异常次数", 3),
        ],
        "service_down": [
            ("pod_ready_status", "service", "Pod 就绪状态", 1),
            ("health_check_status", "service", "健康检查", 2),
            ("replica_count", "deployment", "副本数", 3),
        ],
        "disk_full": [
            ("disk_usage_percent", "service", "磁盘使用率", 1),
            ("inode_usage_percent", "pod", "Inode 使用率", 2),
        ],
        "auth_failure": [
            ("auth_failure_rate", "service", "认证失败率", 1),
            ("login_error_count", "service", "登录错误数", 2),
            ("token_validation_failure", "dependency", "Token 验证失败（Redis）", 3),
        ],
        # 业务症状
        "duplicate_charge": [
            ("business_br_duplicate_charge", "business", "重复扣款（业务规则）", 1),
            ("payment_transaction_count", "service", "支付交易量", 2),
        ],
        "oversold": [
            ("business_br_oversell", "business", "库存超卖（业务规则）", 1),
            ("inventory_mismatch_count", "service", "库存差异数", 2),
        ],
        "amount_mismatch": [
            ("business_br_amount_mismatch", "business", "金额对账（业务规则）", 1),
            ("order_amount_variance", "service", "订单金额偏差", 2),
        ],
        "inventory_mismatch": [
            ("replication_lag_seconds", "service", "复制延迟", 1),
            ("inventory_mismatch_count", "service", "库存差异", 2),
        ],
    }

    # 服务到依赖的快速查找（基于拓扑数据）
    _dependency_cache: dict[str, list[str]] = {}

    def map(
        self,
        service: str,
        symptoms: list[str],
        business_domain: str = "",
    ) -> DiagnosisPlan:
        """
        将症状映射为诊断查询计划。

        Args:
            service: 主服务名
            symptoms: 症状列表
            business_domain: 业务域

        Returns:
            DiagnosisPlan: 按优先级排序的诊断计划
        """
        queries: list[DiagnosticQuery] = []
        seen: set[tuple[str, str]] = set()

        for symptom in symptoms:
            metric_specs = self.SYMPTOM_METRIC_MAP.get(symptom, [])

            # 如果没有精确匹配，尝试前缀匹配
            if not metric_specs:
                for key, specs in self.SYMPTOM_METRIC_MAP.items():
                    if symptom.startswith(key) or key.startswith(symptom):
                        metric_specs = specs
                        break

            # 仍然没有匹配 → 添加泛化查询
            if not metric_specs:
                metric_specs = [
                    ("error_rate_percent", "service", f"通用异常检测({symptom})", 2),
                    ("p99_latency_ms", "service", f"通用延迟检测({symptom})", 3),
                ]

            for metric_name, dimension, reason, priority in metric_specs:
                targets = self._resolve_targets(service, dimension)

                for target in targets:
                    key = (target, metric_name)
                    if key not in seen:
                        seen.add(key)
                        queries.append(DiagnosticQuery(
                            target=target,
                            metric=metric_name,
                            source="prometheus" if not metric_name.startswith("business_") else "business_rules",
                            priority=priority,
                            reason=reason,
                        ))

        # 按优先级排序
        queries.sort(key=lambda q: q.priority)

        return DiagnosisPlan(
            queries=queries,
            service=service,
            symptoms=symptoms,
            business_domain=business_domain,
        )

    def _resolve_targets(self, service: str, dimension: str) -> list[str]:
        """
        根据维度解析查询目标。

        - "service" → 只查自身
        - "dependency" → 查所有上游依赖
        - "pod" / "deployment" → 查自身
        - "business" → 返回特殊标记
        """
        if dimension == "service":
            return [service]

        if dimension == "dependency":
            return self._get_dependencies(service)

        if dimension in ("pod", "deployment"):
            return [service]

        if dimension == "business":
            return ["business-rules-engine"]

        return [service]

    def _get_dependencies(self, service: str) -> list[str]:
        """获取服务的上游依赖列表"""
        if service in self._dependency_cache:
            return self._dependency_cache[service]

        topo = SERVICE_TOPOLOGY.get(service, {})
        deps = list(topo.get("dependencies", []))

        # 缓存结果
        self._dependency_cache[service] = deps
        return deps

    def get_explanation(
        self,
        plan: DiagnosisPlan,
        root_cause: str = "",
        confidence: float = 0.0,
    ) -> str:
        """
        生成面向用户的自然语言解释。

        Args:
            plan: 诊断计划
            root_cause: 根因（如果有的话）
            confidence: 置信度

        Returns:
            str: 自然语言解释
        """
        parts: list[str] = []

        if plan.symptoms:
            symptom_desc = "、".join(plan.symptoms)
            parts.append(f"检测到服务 {plan.service} 出现 {symptom_desc} 的症状。")

        parts.append(f"已生成 {len(plan.queries)} 条诊断查询，按优先级排列：")
        for i, q in enumerate(plan.queries[:5], 1):
            parts.append(f"  {i}. [{q.target}] {q.metric} — {q.reason}")

        if root_cause:
            conf_pct = round(confidence * 100)
            parts.append(f"系统推断最可能的根因是「{root_cause}」（置信度 {conf_pct}%）。")
        else:
            parts.append("根因分析正在进行中，请在故障详情页面查看实时分析结果。")

        return "\n".join(parts)
