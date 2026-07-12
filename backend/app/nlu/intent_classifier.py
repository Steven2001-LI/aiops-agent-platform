"""
AIOps Agent Platform - Intent Classifier

识别用户自然语言问题的意图类型。
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class IntentType(str, Enum):
    """用户意图类型"""
    FAULT_DIAGNOSIS = "fault_diagnosis"      # 故障诊断："为什么下单这么慢？"
    METRIC_QUERY = "metric_query"             # 指标查询："CPU 使用率多少？"
    HEAL_REQUEST = "heal_request"             # 修复请求："帮我重启一下订单服务"
    HISTORY_LOOKUP = "history_lookup"         # 历史查询："上次类似的故障怎么处理的？"
    BUSINESS_CHECK = "business_check"         # 业务检查："有没有重复扣款？"
    GENERAL_QUESTION = "general_question"     # 一般问题


class UserIntent(BaseModel):
    """用户意图识别结果"""
    intent: IntentType = Field(description="识别到的意图类型")
    confidence: float = Field(default=0.0, description="置信度")
    sub_intent: str = Field(default="", description="子意图")
    raw_query: str = Field(default="", description="原始查询")


# 意图匹配规则（正则 + 关键词）
# 注意：query 已被转为小写（query_lower），所有模式用小写
INTENT_PATTERNS: dict[IntentType, list[str]] = {
    IntentType.FAULT_DIAGNOSIS: [
        r"为什么.*(慢|卡|挂|不行|出错|报错|超时|崩|异常|故障)",
        r"(是不是|是不是已经|已经).*(挂|崩|坏|死|宕|瘫|不行|有问题|出问题|故障)",
        r".*(出问题|出故障|出异常|不行)了",
        r".*(挂了|崩了|坏了|死了|宕了|瘫了)",
        r"帮我看下.*怎么回事",
        r"排查.*",
        r"什么原因.*",
        r"怎么.*(这么慢|这么卡|报错|挂了|回事)",
        r"(慢|卡|超时|错误|异常|故障|挂了|崩了)$",
        r".*(一直|老是|总是|经常).*(转圈|卡|慢|超时|报错)",
    ],
    IntentType.METRIC_QUERY: [
        r".*(cpu|内存|延迟|qps|tps|吞吐|磁盘|网络).*多少",
        r".*(cpu|内存|延迟|qps|tps|吞吐|磁盘|网络).*(多少|怎么样|什么|如何|查询|查看|看看|看下)",
        r"查.*指标",
        r".*当前.*(状态|值|情况)",
        r"看一下.*(cpu|内存|延迟)",
    ],
    IntentType.HEAL_REQUEST: [
        r"帮我.*(修|重启|扩容|回滚|恢复|处理|解决|改)",
        r"怎么.*(处理|解决|修|恢复|修好)",
        r"(重启|回滚|扩容|缩容).*",
        r".*执行.*(重启|回滚|扩容)",
    ],
    IntentType.HISTORY_LOOKUP: [
        r"上次.*(故障|问题|异常).*(怎么|如何).*",
        r"最近.*(告警|故障|异常)",
        r"历史.*(记录|故障|告警)",
        r"以前.*类似.*",
        r".*以前.*(怎么|如何).*处理",
    ],
    IntentType.BUSINESS_CHECK: [
        r"有没有.*(重复扣款|超卖|对账不平|优惠券|卡单|多扣|少扣)",
        r"检查.*(业务|交易|订单|支付|库存|扣款)",
        r".*(扣.*两次|多.*扣|少.*扣|重复.*扣)",
        r".*(超卖|少货|库存不对|对账不平)",
        r".*业务.*(异常|问题|故障|错误)",
    ],
}


class IntentClassifier:
    """
    用户意图分类器。

    使用正则匹配 + 关键词权重的方式进行意图分类，
    不依赖 LLM，可在离线环境运行。
    """

    # 意图优先级加权关键词（解决歧义）
    INTENT_BOOST_KEYWORDS: dict[IntentType, list[str]] = {
        IntentType.HISTORY_LOOKUP: [
            "上次", "以前", "历史", "最近", "之前", "曾经", "过去", "前几天",
        ],
        IntentType.BUSINESS_CHECK: [
            "有没有", "检查一下", "超卖", "重复扣款", "对账不平",
        ],
        IntentType.FAULT_DIAGNOSIS: [
            "为什么", "是不是", "怎么回事", "什么原因", "排查",
        ],
    }

    def classify(self, query: str) -> UserIntent:
        """
        对用户查询进行意图分类。

        Args:
            query: 用户自然语言查询

        Returns:
            UserIntent: 分类结果
        """
        if not query or not query.strip():
            return UserIntent(
                intent=IntentType.GENERAL_QUESTION,
                confidence=0.0,
                raw_query=query,
            )

        query_lower = query.lower().strip()
        scores: dict[IntentType, float] = {}

        for intent, patterns in INTENT_PATTERNS.items():
            score = 0.0
            for pattern in patterns:
                if re.search(pattern, query_lower):
                    score += 1.0
            # 归一化
            scores[intent] = score / max(len(patterns), 1)

        # 关键词加权（解决意图歧义）
        for intent, keywords in self.INTENT_BOOST_KEYWORDS.items():
            if intent in scores:
                boost = sum(1.0 for kw in keywords if kw in query_lower)
                scores[intent] += boost * 0.15  # 每个关键词 +0.15

        # 选出最高分意图
        if not scores:
            return UserIntent(
                intent=IntentType.GENERAL_QUESTION,
                confidence=0.0,
                raw_query=query,
            )

        best_intent = max(scores, key=lambda k: scores[k])
        best_score = scores[best_intent]

        if best_score < 0.1:
            return UserIntent(
                intent=IntentType.GENERAL_QUESTION,
                confidence=0.0,
                raw_query=query,
            )

        return UserIntent(
            intent=best_intent,
            confidence=min(best_score, 1.0),
            raw_query=query,
        )
