"""
AIOps Agent Platform - Business Monitor Agent

业务异常监控 Agent，负责检测指标无法覆盖的业务逻辑异常。
如：重复扣款、库存超卖、金额对账不平、优惠券滥用等。

与 MonitorAgent（指标异常检测）互补：
- MonitorAgent: CPU/内存/延迟/错误率等连续型时序指标
- BusinessMonitorAgent: 支付/订单/库存/用户等离散型业务事件
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.agents.base import AgentResult, BaseAgent
from app.models.agent import AgentExecutionContext
from app.models.events import AlertEvent, SeverityLevel
from app.utils.logging import get_logger

logger = get_logger(__name__)


# ==================== 业务规则定义 ====================

class BusinessRule(BaseModel):
    """单条业务规则"""
    id: str = Field(description="规则ID")
    name: str = Field(description="规则名称")
    category: str = Field(description="类别: financial/inventory/user/order")
    description: str = Field(description="规则描述")
    check_logic: str = Field(description="检测逻辑说明")
    severity: str = Field(description="严重级别")
    expected_root_cause: str = Field(description="预期根因")
    suggested_actions: list[str] = Field(description="建议操作")
    data_source: str = Field(default="business_events", description="数据来源")


# 业务规则库
BUSINESS_RULES: list[dict[str, Any]] = [
    {
        "id": "br_duplicate_charge",
        "name": "重复扣款检测",
        "category": "financial",
        "description": "同一用户在同一订单上被扣款超过 1 次",
        "check_logic": "检测 payment_transactions 表中同一 (user_id, order_id) 在 5 分钟内出现 > 1 条扣款记录",
        "severity": "critical",
        "expected_root_cause": "payment_idempotency_failure",
        "suggested_actions": [
            "verify_duplicate_charges",
            "refund_duplicates",
            "fix_payment_idempotency",
            "notify_affected_users",
        ],
        "data_source": "payment_transactions",
    },
    {
        "id": "br_oversell",
        "name": "库存超卖检测",
        "category": "inventory",
        "description": "已售商品数量超过实际库存",
        "check_logic": "检测 orders 表中 SUM(quantity) WHERE product_id=X AND status IN ('paid','processing') > products.stock",
        "severity": "critical",
        "expected_root_cause": "inventory_sync_failure",
        "suggested_actions": [
            "pause_product_listing",
            "cancel_oversold_orders",
            "reconcile_inventory",
            "notify_affected_users",
        ],
        "data_source": "orders + products",
    },
    {
        "id": "br_amount_mismatch",
        "name": "订单金额对账异常",
        "category": "financial",
        "description": "订单金额与支付金额不一致",
        "check_logic": "检测 orders.amount != payments.amount 的记录",
        "severity": "high",
        "expected_root_cause": "accounting_reconciliation_error",
        "suggested_actions": [
            "reconcile_transactions",
            "check_payment_gateway_logs",
            "fix_amount_calculation",
        ],
        "data_source": "orders + payments",
    },
    {
        "id": "br_coupon_abuse",
        "name": "优惠券滥用检测",
        "category": "financial",
        "description": "同一用户重复使用已消费的优惠券",
        "check_logic": "检测 coupon_usage 表中同一 coupon_id 被使用 > 1 次",
        "severity": "high",
        "expected_root_cause": "coupon_idempotency_failure",
        "suggested_actions": [
            "revoke_duplicate_coupons",
            "fix_coupon_state_machine",
            "audit_coupon_usage",
        ],
        "data_source": "coupon_usage",
    },
    {
        "id": "br_order_stuck",
        "name": "订单卡单检测",
        "category": "order",
        "description": "订单长时间停留在中间状态不流转",
        "check_logic": "检测 orders 表中 status='processing' 且 updated_at > 30 分钟前的记录",
        "severity": "high",
        "expected_root_cause": "order_state_machine_bug",
        "suggested_actions": [
            "check_order_processor_health",
            "retry_stuck_orders",
            "check_dependency_services",
        ],
        "data_source": "orders",
    },
    {
        "id": "br_payment_callback_loss",
        "name": "支付回调丢失检测",
        "category": "financial",
        "description": "支付网关已扣款但订单状态未更新为已支付",
        "check_logic": "检测 payments 表中 status='success' 但对应 orders.status != 'paid' 的记录",
        "severity": "critical",
        "expected_root_cause": "third_party_callback_loss",
        "suggested_actions": [
            "reconcile_payment_status",
            "update_order_status",
            "check_webhook_endpoint",
        ],
        "data_source": "payments + orders",
    },
    {
        "id": "br_inventory_discrepancy",
        "name": "库存数据不一致",
        "category": "inventory",
        "description": "MySQL 与 Elasticsearch 中的库存数据不一致",
        "check_logic": "对比 DB 和 ES 中同一 SKU 的库存数量，偏差 > 阈值则告警",
        "severity": "medium",
        "expected_root_cause": "replication_lag",
        "suggested_actions": [
            "force_reindex",
            "check_replication_lag",
            "invalidate_cache",
        ],
        "data_source": "mysql + elasticsearch",
    },
    {
        "id": "br_user_permission_error",
        "name": "用户权限异常",
        "category": "user",
        "description": "用户被拒绝访问应有权限的资源",
        "check_logic": "检测 auth_logs 中同一用户在短时间内出现大量 403 错误",
        "severity": "medium",
        "expected_root_cause": "permission_sync_failure",
        "suggested_actions": [
            "check_permission_service",
            "sync_user_permissions",
            "check_token_validity",
        ],
        "data_source": "auth_logs",
    },
]


# ==================== 业务异常检测输入/输出 ====================

class BusinessMetricInput(BaseModel):
    """BusinessMonitorAgent 输入"""
    service_name: str = Field(description="服务名称")
    business_domain: str = Field(description="业务域: financial/inventory/order/user")
    check_rules: list[str] = Field(
        default_factory=list,
        description="要检查的规则ID列表，空列表表示检查该域所有规则",
    )
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    context: dict[str, Any] = Field(default_factory=dict, description="业务上下文")


class BusinessRuleCheckResult(BaseModel):
    """单条规则的检查结果"""
    rule_id: str = Field(description="规则ID")
    rule_name: str = Field(description="规则名称")
    matched: bool = Field(description="是否命中规则（即检测到异常）")
    severity: str = Field(description="严重级别")
    confidence: float = Field(default=0.0, description="置信度")
    affected_entities: dict[str, Any] = Field(default_factory=dict)
    description: str = Field(default="", description="异常描述")
    suggested_actions: list[str] = Field(default_factory=list)


class BusinessAnomalyResult(BaseModel):
    """业务异常检测结果"""
    is_anomaly: bool = Field(description="是否检测到业务异常")
    rules_checked: int = Field(default=0)
    rules_matched: int = Field(default=0)
    results: list[BusinessRuleCheckResult] = Field(default_factory=list)
    summary: str = Field(default="", description="异常汇总")


# ==================== BusinessMonitorAgent ====================

class BusinessMonitorAgent(BaseAgent[BusinessMetricInput, AlertEvent]):
    """
    业务异常监控 Agent

    职责：
    - 执行业务规则检查（重复扣款、库存超卖、金额对账等）
    - 生成业务告警事件
    - 与 MonitorAgent 互补，覆盖基础设施监控无法发现的业务错误
    """

    def __init__(self) -> None:
        super().__init__()
        self._rules: dict[str, dict[str, Any]] = {
            r["id"]: r for r in BUSINESS_RULES
        }

    def get_name(self) -> str:
        return "business_monitor_agent"

    def get_description(self) -> str:
        return "业务异常监控 Agent - 基于业务规则的业务逻辑异常检测"

    # ==================== 核心处理 ====================

    async def process(
        self,
        input_data: BusinessMetricInput,
        context: AgentExecutionContext,
    ) -> AgentResult:
        """
        执行业务规则检查

        Args:
            input_data: 业务检查输入
            context: 执行上下文

        Returns:
            AgentResult: 包含业务异常检测结果
        """
        logger.info(
            "BusinessMonitorAgent processing",
            service=input_data.service_name,
            domain=input_data.business_domain,
        )

        # Step 1: 获取要检查的规则
        rules_to_check = self._select_rules(
            input_data.business_domain,
            input_data.check_rules,
        )

        # Step 2: 逐条执行规则检查
        check_results: list[BusinessRuleCheckResult] = []
        for rule in rules_to_check:
            result = self._check_rule(rule, input_data)
            check_results.append(result)

        # Step 3: 汇总结果
        matched = [r for r in check_results if r.matched]
        is_anomaly = len(matched) > 0

        anomaly_result = BusinessAnomalyResult(
            is_anomaly=is_anomaly,
            rules_checked=len(check_results),
            rules_matched=len(matched),
            results=check_results,
            summary=self._build_summary(check_results, input_data),
        )

        # Step 4: 生成告警事件
        output_data: dict[str, Any] = {
            "anomaly_result": anomaly_result.model_dump(),
            "alert_generated": False,
        }

        if is_anomaly:
            alerts = []
            for r in matched:
                alert = self._create_business_alert(r, input_data)
                alerts.append(alert.model_dump())

            output_data["alerts"] = alerts
            output_data["alert_generated"] = True

            logger.info(
                "Business anomaly detected",
                service=input_data.service_name,
                matched_rules=[r.rule_id for r in matched],
                total_alerts=len(alerts),
            )

        return AgentResult.success_result(
            agent_name=self.get_name(),
            output_data=output_data,
        )

    # ==================== 规则选择 ====================

    def _select_rules(
        self,
        domain: str,
        specific_rules: list[str],
    ) -> list[dict[str, Any]]:
        """选择要检查的规则"""
        if specific_rules:
            return [
                self._rules[rid] for rid in specific_rules
                if rid in self._rules
            ]

        if domain:
            return [
                r for r in self._rules.values()
                if r["category"] == domain
            ]

        # 默认：检查所有规则
        return list(self._rules.values())

    # ==================== 规则执行引擎 ====================

    def _check_rule(
        self,
        rule: dict[str, Any],
        input_data: BusinessMetricInput,
    ) -> BusinessRuleCheckResult:
        """
        执行单条规则检查。

        数据来源优先级:调用方经 context["business_events"] 传入真实业务事件
        ({数据源: [记录]}),否则回退到 context["scenario_id"] 对应的静态
        业务事件数据集(data/datasets.py 的 BUSINESS_EVENTS,默认 normal)。
        """
        matched, confidence = self._evaluate_rule(rule, input_data)

        return BusinessRuleCheckResult(
            rule_id=rule["id"],
            rule_name=rule["name"],
            matched=matched,
            severity=rule["severity"],
            confidence=confidence,
            affected_entities={
                "service": input_data.service_name,
                "domain": input_data.business_domain,
                "rule_category": rule["category"],
            },
            description=rule["description"] if matched else "",
            suggested_actions=rule["suggested_actions"] if matched else [],
        )

    @staticmethod
    def _resolve_events(input_data: BusinessMetricInput) -> dict[str, Any]:
        """解析本次检查可用的业务事件记录。"""
        injected = input_data.context.get("business_events")
        if isinstance(injected, dict) and injected:
            return injected
        from app.data.datasets import get_business_events
        return get_business_events(str(input_data.context.get("scenario_id", "normal")))

    @staticmethod
    def _parse_ts(value: Any) -> datetime | None:
        try:
            return datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None

    def _evaluate_rule(
        self,
        rule: dict[str, Any],
        input_data: BusinessMetricInput,
    ) -> tuple[bool, float]:
        """
        按规则 ID 对业务事件记录执行确定性检查。

        返回 (是否命中, 置信度)。数据源缺失时返回未命中——
        br_inventory_discrepancy(需 ES 对账)与 br_user_permission_error
        (需 auth_logs)在静态数据集中无数据,自然落入该分支。
        """
        events = self._resolve_events(input_data)
        rule_id = rule["id"]

        if rule_id == "br_duplicate_charge":
            # 同一 (user_id, order_id) 在 5 分钟窗口内扣款 > 1 次
            groups: dict[tuple[str, str], list[datetime | None]] = {}
            for txn in events.get("payment_transactions", []):
                key = (str(txn.get("user_id")), str(txn.get("order_id")))
                groups.setdefault(key, []).append(self._parse_ts(txn.get("charged_at")))
            for stamps in groups.values():
                if len(stamps) <= 1:
                    continue
                known = [s for s in stamps if s is not None]
                within_window = (
                    len(known) < 2
                    or (max(known) - min(known)).total_seconds() <= 300
                )
                if within_window:
                    return True, min(1.0, 0.5 + 0.25 * (len(stamps) - 1))
            return False, 0.0

        if rule_id == "br_oversell":
            # 已付/处理中订单合计数量 > 商品库存
            sold: dict[str, int] = {}
            for order in events.get("orders", []):
                if order.get("status") in {"paid", "processing"}:
                    pid = str(order.get("product_id"))
                    sold[pid] = sold.get(pid, 0) + int(order.get("quantity", 0))
            for product in events.get("products", []):
                pid = str(product.get("product_id"))
                stock = int(product.get("stock", 0))
                if sold.get(pid, 0) > stock:
                    over_ratio = (sold[pid] - stock) / max(stock, 1)
                    return True, min(1.0, 0.6 + over_ratio * 0.2)
            return False, 0.0

        if rule_id == "br_amount_mismatch":
            # 订单金额与成功支付金额不一致
            order_amounts = {
                str(o.get("order_id")): float(o.get("amount", 0.0))
                for o in events.get("orders", [])
            }
            mismatched = [
                p for p in events.get("payments", [])
                if p.get("status") == "success"
                and str(p.get("order_id")) in order_amounts
                and abs(float(p.get("amount", 0.0)) - order_amounts[str(p.get("order_id"))]) > 0.001
            ]
            if mismatched:
                return True, min(1.0, 0.7 + 0.1 * len(mismatched))
            return False, 0.0

        if rule_id == "br_coupon_abuse":
            # 同一 coupon_id 被使用 > 1 次
            usage_count: dict[str, int] = {}
            for usage in events.get("coupon_usage", []):
                cid = str(usage.get("coupon_id"))
                usage_count[cid] = usage_count.get(cid, 0) + 1
            worst = max(usage_count.values(), default=0)
            if worst > 1:
                return True, min(1.0, 0.5 + 0.25 * (worst - 1))
            return False, 0.0

        if rule_id == "br_order_stuck":
            # processing 状态停留超过 30 分钟(相对本次检查时间)
            now = input_data.timestamp
            for order in events.get("orders", []):
                if order.get("status") != "processing":
                    continue
                updated_at = self._parse_ts(order.get("updated_at"))
                if updated_at is not None and (now - updated_at).total_seconds() > 1800:
                    return True, 0.8
            return False, 0.0

        if rule_id == "br_payment_callback_loss":
            # 支付成功但对应订单状态未更新为已支付
            order_status = {
                str(o.get("order_id")): o.get("status")
                for o in events.get("orders", [])
            }
            lost = [
                p for p in events.get("payments", [])
                if p.get("status") == "success"
                and order_status.get(str(p.get("order_id"))) not in (None, "paid")
            ]
            if lost:
                return True, min(1.0, 0.7 + 0.1 * len(lost))
            return False, 0.0

        # 其余规则(库存双写对账/权限异常)所需数据源不在业务事件数据集中,
        # 数据缺失时如实返回未命中,不伪造结论
        return False, 0.0

    # ==================== 告警生成 ====================

    def _create_business_alert(
        self,
        result: BusinessRuleCheckResult,
        input_data: BusinessMetricInput,
    ) -> AlertEvent:
        """创建业务告警事件"""
        severity_map = {
            "critical": SeverityLevel.CRITICAL,
            "high": SeverityLevel.HIGH,
            "medium": SeverityLevel.MEDIUM,
            "low": SeverityLevel.LOW,
            "info": SeverityLevel.INFO,
        }

        return AlertEvent(
            source=self.get_name(),
            service=input_data.service_name,
            metric=f"business_{result.rule_id}",
            value=float(result.confidence * 100),
            threshold=50.0,  # 置信度 > 50% 视为告警
            operator=">",
            severity=severity_map.get(result.severity, SeverityLevel.MEDIUM),
            labels={
                "business_domain": input_data.business_domain,
                "rule_id": result.rule_id,
                "rule_category": "business",
                "tier": "critical",
            },
            annotations={
                "summary": f"业务异常: {result.rule_name}",
                "description": result.description,
                "suggested_actions": "; ".join(result.suggested_actions),
                "affected_entities": str(result.affected_entities),
            },
        )

    # ==================== 汇总 ====================

    @staticmethod
    def _build_summary(
        results: list[BusinessRuleCheckResult],
        input_data: BusinessMetricInput,
    ) -> str:
        """生成检测结果汇总"""
        matched = [r for r in results if r.matched]

        if not matched:
            return f"业务域 '{input_data.business_domain}' 未检测到异常"

        critical = [r for r in matched if r.severity == "critical"]
        high = [r for r in matched if r.severity == "high"]

        parts = [f"检测到 {len(matched)} 个业务异常"]
        if critical:
            parts.append(f"其中 {len(critical)} 个严重级别")
            parts.append(f"严重: {', '.join(r.rule_name for r in critical)}")
        if high:
            parts.append(f"高级: {', '.join(r.rule_name for r in high)}")

        return "；".join(parts)

    # ==================== 工具方法 ====================

    def get_all_rules(self) -> list[dict[str, Any]]:
        """获取所有业务规则"""
        return list(self._rules.values())

    def get_rules_by_domain(self, domain: str) -> list[dict[str, Any]]:
        """按业务域获取规则"""
        return [r for r in self._rules.values() if r["category"] == domain]

    def get_rule(self, rule_id: str) -> dict[str, Any] | None:
        """获取单条规则"""
        return self._rules.get(rule_id)
