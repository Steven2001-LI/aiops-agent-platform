"""
AIOps Agent Platform - Entity Extractor

从自然语言中提取运维相关实体：
- 服务名（含大白话同义词映射）
- 症状描述（含大白话同义词映射）
- 紧急程度
- 时间范围
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field


class AIOpsEntities(BaseModel):
    """提取到的运维实体"""
    services: list[str] = Field(default_factory=list, description="涉及的服务名")
    symptoms: list[str] = Field(default_factory=list, description="识别的症状")
    urgency: str = Field(default="medium", description="紧急程度: low/medium/high/critical")
    time_range: str = Field(default="now", description="时间范围: now/1h/24h/recent")
    business_domain: str = Field(default="", description="业务域: financial/inventory/order/user")
    raw_query: str = Field(default="", description="原始查询")


class EntityExtractor:
    """
    运维实体抽取器。

    使用同义词字典 + 规则匹配从大白话中抽取服务名和症状。
    支持 LLM 增强模式（配置 enable_llm=True 时）。
    """

    # 服务名同义词映射（大白话 → 标准服务名）
    SERVICE_SYNONYMS: dict[str, str] = {
        # order-service
        "下单": "order-service",
        "订单": "order-service",
        "买": "order-service",
        "购物": "order-service",
        "成交": "order-service",
        # payment-service
        "支付": "payment-service",
        "付款": "payment-service",
        "扣款": "payment-service",
        "扣费": "payment-service",
        "缴费": "payment-service",
        "转账": "payment-service",
        # user-service
        "登录": "user-service",
        "注册": "user-service",
        "用户": "user-service",
        "账号": "user-service",
        "密码": "user-service",
        "认证": "user-service",
        "权限": "user-service",
        # inventory-service
        "库存": "inventory-service",
        "商品": "inventory-service",
        "货物": "inventory-service",
        "上架": "inventory-service",
        "下架": "inventory-service",
        "搜索": "inventory-service",
        # api-gateway
        "网关": "api-gateway",
        "入口": "api-gateway",
        "接口": "api-gateway",
        "API": "api-gateway",
        # 基础设施
        "数据库": "mysql-primary",
        "MySQL": "mysql-primary",
        "缓存": "redis-cache",
        "Redis": "redis-cache",
        "ES": "elasticsearch",
        "搜索服务": "elasticsearch",
        "消息队列": "kafka",
        "Kafka": "kafka",
    }

    # 症状同义词映射（大白话 → 标准症状）
    SYMPTOM_SYNONYMS: dict[str, str] = {
        # 延迟
        "慢": "high_latency",
        "卡": "high_latency",
        "卡顿": "high_latency",
        "转圈": "high_latency",
        "转圈圈": "high_latency",
        "加载": "high_latency",
        "响应慢": "high_latency",
        "等很久": "high_latency",
        # 超时
        "超时": "timeout",
        "连接超时": "timeout",
        "timeout": "timeout",
        # 服务状态
        "挂": "service_down",
        "挂了": "service_down",
        "崩": "service_down",
        "崩溃": "service_down",
        "宕机": "service_down",
        "不可用": "service_down",
        "访问不了": "service_down",
        "打不开": "service_down",
        # 错误
        "报错": "high_error_rate",
        "出错": "high_error_rate",
        "错误": "high_error_rate",
        "异常": "high_error_rate",
        "失败": "high_error_rate",
        "500": "high_error_rate",
        "503": "high_error_rate",
        # 认证
        "登录不上": "auth_failure",
        "登不上": "auth_failure",
        "登不进去": "auth_failure",
        "密码错误": "auth_failure",
        "无权": "auth_failure",
        "403": "auth_failure",
        # 业务异常
        "扣两次": "duplicate_charge",
        "多扣": "duplicate_charge",
        "重复扣": "duplicate_charge",
        "重复支付": "duplicate_charge",
        "超卖": "oversold",
        "没库存": "inventory_mismatch",
        "库存不对": "inventory_mismatch",
        "对账不平": "amount_mismatch",
        "金额不对": "amount_mismatch",
        # 资源
        "CPU高": "high_cpu",
        "cpu高": "high_cpu",
        "内存高": "high_memory",
        "内存泄漏": "memory_leak",
        "磁盘满": "disk_full",
    }

    # 紧急程度关键词
    URGENCY_KEYWORDS: dict[str, str] = {
        "紧急": "critical",
        "严重": "high",
        "马上": "high",
        "立刻": "high",
        "赶快": "high",
        "快点": "high",
        "全挂": "critical",
        "瘫痪": "critical",
        "大面积": "critical",
        "全部": "high",
    }

    def extract(self, query: str) -> AIOpsEntities:
        """
        从自然语言查询中提取运维实体。

        Args:
            query: 用户自然语言查询

        Returns:
            AIOpsEntities: 提取到的实体
        """
        query_lower = query.lower().strip()

        # 1. 提取服务名
        services = self._extract_services(query_lower)

        # 2. 提取症状
        symptoms = self._extract_symptoms(query_lower)

        # 3. 推断紧急程度
        urgency = self._infer_urgency(query_lower)

        # 4. 推断业务域
        business_domain = self._infer_business_domain(services, symptoms)

        # 5. 推断时间范围
        time_range = self._infer_time_range(query_lower)

        return AIOpsEntities(
            services=services,
            symptoms=symptoms,
            urgency=urgency,
            time_range=time_range,
            business_domain=business_domain,
            raw_query=query,
        )

    def _extract_services(self, query: str) -> list[str]:
        """从查询中提取服务名"""
        services: list[str] = []

        # 按关键词长度降序匹配（优先匹配长关键词）
        sorted_synonyms = sorted(
            self.SERVICE_SYNONYMS.items(),
            key=lambda x: len(x[0]),
            reverse=True,
        )

        matched_positions: set[int] = set()
        for keyword, svc_name in sorted_synonyms:
            for match in re.finditer(re.escape(keyword), query):
                start, end = match.start(), match.end()
                # 避免重叠匹配
                if not any(start <= p < end or p <= start < p + 1 for p in matched_positions):
                    if svc_name not in services:
                        services.append(svc_name)
                    for i in range(start, end):
                        matched_positions.add(i)

        return services

    def _extract_symptoms(self, query: str) -> list[str]:
        """从查询中提取症状"""
        symptoms: list[str] = []

        sorted_synonyms = sorted(
            self.SYMPTOM_SYNONYMS.items(),
            key=lambda x: len(x[0]),
            reverse=True,
        )

        for keyword, symptom_name in sorted_synonyms:
            if keyword in query:
                if symptom_name not in symptoms:
                    symptoms.append(symptom_name)

        return symptoms

    def _infer_urgency(self, query: str) -> str:
        """推断紧急程度"""
        scores = {"critical": 0, "high": 0, "medium": 0, "low": 0}

        for keyword, level in self.URGENCY_KEYWORDS.items():
            if keyword in query:
                # 关键词出现次数加权
                count = query.count(keyword)
                scores[level] += count

        if scores["critical"] > 0:
            return "critical"
        elif scores["high"] > 0:
            return "high"
        else:
            return "medium"

    def _infer_business_domain(
        self, services: list[str], symptoms: list[str]
    ) -> str:
        """从服务和症状推断业务域"""
        # 基于症状推断
        financial_symptoms = {
            "duplicate_charge", "amount_mismatch", "payment_failure",
        }
        inventory_symptoms = {
            "oversold", "inventory_mismatch", "stale_data",
        }
        order_symptoms = {"order_stuck", "order_backlog"}
        user_symptoms = {"auth_failure", "permission_error"}

        symptom_set = set(symptoms)

        if symptom_set & financial_symptoms:
            return "financial"
        if symptom_set & inventory_symptoms:
            return "inventory"
        if symptom_set & order_symptoms:
            return "order"
        if symptom_set & user_symptoms:
            return "user"

        # 基于服务推断
        for svc in services:
            if "payment" in svc:
                return "financial"
            if "inventory" in svc:
                return "inventory"
            if "order" in svc:
                return "order"
            if "user" in svc:
                return "user"

        return "infrastructure"

    @staticmethod
    def _infer_time_range(query: str) -> str:
        """推断时间范围"""
        if any(w in query for w in ["现在", "当前", "立刻", "刚刚", "马上"]):
            return "now"
        if any(w in query for w in ["1小时", "一小时", "最近一小时", "这个小时"]):
            return "1h"
        if any(w in query for w in ["今天", "24小时", "一天"]):
            return "24h"
        if any(w in query for w in ["最近", "近期", "这几天", "上周", "本月"]):
            return "recent"
        return "now"  # 默认
