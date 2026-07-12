"""
AIOps NLU 混合层:正则快路径 + LLM 慢路径。

返回类型与现有 (UserIntent, AIOpsEntities) 完全一致,下游零改动。
所有降级在本模块内闭环:hybrid_understand 永不 raise,
diagnose 是用户干等的同步端点,任何异常冒出去就是 500。
"""

from __future__ import annotations

import asyncio
import json
from typing import Literal

from pydantic import BaseModel, Field

from app.config import get_config
from app.nlu.entity_extractor import AIOpsEntities, EntityExtractor
from app.nlu.intent_classifier import IntentClassifier, IntentType, UserIntent
from app.services.llm_service import LLMService, LLMUnavailableError, get_llm_service
from app.utils.logging import get_logger

logger = get_logger(__name__)

_classifier = IntentClassifier()
_extractor = EntityExtractor()

# 闭集:标准症状枚举(取自 EntityExtractor 同义词表的值域)
KNOWN_SYMPTOMS: list[str] = sorted(set(EntityExtractor.SYMPTOM_SYNONYMS.values()))

# 交互式同步端点的慢路径超时预算,比 RCA 的 30s 更紧。
# 函数体内引用模块全局名,测试可 monkeypatch 覆盖。
NLU_SLOW_PATH_TIMEOUT_SECONDS = 10.0


class LLMNLUResult(BaseModel):
    """LLM 慢路径输出。services/symptoms 受闭集约束(运行时校验)。"""

    intent: IntentType
    intent_confidence: float = Field(ge=0.0, le=1.0)
    services: list[str] = Field(default_factory=list)
    symptoms: list[str] = Field(default_factory=list)
    urgency: Literal["low", "medium", "high", "critical"] = "medium"
    time_range: Literal["now", "1h", "24h", "recent"] = "now"
    business_domain: Literal[
        "financial", "inventory", "order", "user", "infrastructure"
    ] = "infrastructure"
    missing_info: list[str] = Field(
        default_factory=list, description="仍缺失的关键信息项"
    )
    clarification_question: str = Field(
        default="", description="缺关键信息时,给用户的一句反问"
    )


NLU_SYSTEM_PROMPT = """你是运维平台的自然语言理解模块。用户会用大白话描述系统问题,\
你要输出结构化理解结果。

规则:
1. services 只能从"已知服务"列表中选;用户描述映射不到任何已知服务时给空列表,\
并把 "service" 加入 missing_info。
2. symptoms 只能从"标准症状"列表中选,选最贴切的,不超过 3 个。
3. 用户没提到的信息不要脑补:说"有点慢"不代表 critical。
4. 若 intent 是故障诊断/修复请求,但缺服务名或症状,生成一句自然的 clarification_question,\
一次把所有缺失项问全(不要挤牙膏式追问)。"""


def _fast_path_confident(
    intent: UserIntent, entities: AIOpsEntities, threshold: float
) -> bool:
    """快路径是否有把握(判据见方案 §5.1)。

    阈值由调用方传入:注入路径读注入配置,生产路径读全局配置,
    本函数自己不碰 get_config。confidence == threshold 算有把握。
    """
    if intent.intent == IntentType.GENERAL_QUESTION:
        return False
    if intent.confidence < threshold:
        return False
    if intent.intent in (
        IntentType.FAULT_DIAGNOSIS, IntentType.HEAL_REQUEST, IntentType.BUSINESS_CHECK
    ) and not entities.services and not entities.symptoms:
        return False
    return True


async def hybrid_understand(
    query: str,
    known_services: list[str],
    llm_service: LLMService | None = None,
) -> tuple[UserIntent, AIOpsEntities, dict]:
    """
    混合 NLU 主入口。永不 raise,LLM 任何故障都降级返回快路径结果。

    Args:
        query: 用户自然语言查询
        known_services: 服务名闭集(SERVICE_TOPOLOGY.keys())
        llm_service: 测试注入口;None 时(生产)每次调用现读全局配置与单例,
                     不做模块级缓存(防僵尸快照)。

    Returns:
        (intent, entities, nlu_info)
        nlu_info = {"path": "fast_rule"|"llm_enhanced"|"rule_fallback",
                    "clarification": str, ...慢路径成功时另有 missing_info/llm_meta}
    """
    intent = _classifier.classify(query)
    entities = _extractor.extract(query)
    info: dict = {"path": "fast_rule", "clarification": ""}

    cfg = llm_service._cfg if llm_service is not None else get_config().llm
    if not cfg.enable_nlu or _fast_path_confident(
        intent, entities, cfg.nlu_fast_path_confidence
    ):
        return intent, entities, info

    # ---- LLM 慢路径 ----
    def _check_closed_set(r: LLMNLUResult) -> str | None:
        bad_svc = [s for s in r.services if s not in known_services]
        if bad_svc:
            return f"services {bad_svc} 不在已知服务列表中"
        bad_sym = [s for s in r.symptoms if s not in KNOWN_SYMPTOMS]
        if bad_sym:
            return f"symptoms {bad_sym} 不在标准症状列表中"
        return None

    preliminary: dict = {
        "intent": intent.intent.value,
        "confidence": round(intent.confidence, 3),
        "services": entities.services,
        "symptoms": entities.symptoms,
    }
    out_of_set = [s for s in entities.services if s not in known_services]
    if out_of_set:
        # 快路径同义词表比拓扑闭集大(如 kafka),初步结果可能带出集服务。
        # 加注记消除与系统提示"只能从已知服务选"的自相矛盾;闭集校验只管输出。
        preliminary["note"] = (
            f"services 中 {out_of_set} 不在已知服务列表,仅供参考;"
            f"你输出的 services 仍只能从已知服务列表中选择"
        )

    try:
        llm = llm_service if llm_service is not None else get_llm_service()
        result, meta = await asyncio.wait_for(
            llm.structured_completion(
                system=NLU_SYSTEM_PROMPT,
                user=json.dumps({
                    "user_query": query,
                    "已知服务": known_services,
                    "标准症状": KNOWN_SYMPTOMS,
                    "规则快路径初步结果": preliminary,
                }, ensure_ascii=False),
                schema=LLMNLUResult,
                call_name="nlu_understand",
                model=cfg.nlu_model or None,
                max_tokens=500,
                validate_extra=_check_closed_set,
            ),
            timeout=NLU_SLOW_PATH_TIMEOUT_SECONDS,
        )
        intent = UserIntent(
            intent=result.intent,
            confidence=result.intent_confidence,
            raw_query=query,
        )
        entities = AIOpsEntities(
            services=result.services,
            symptoms=result.symptoms,
            urgency=result.urgency,
            time_range=result.time_range,
            business_domain=(
                "" if result.business_domain == "infrastructure"
                else result.business_domain
            ),
            raw_query=query,
        )
        info = {
            "path": "llm_enhanced",
            "clarification": result.clarification_question,
            "missing_info": result.missing_info,
            "llm_meta": meta,
        }
    except (LLMUnavailableError, TimeoutError) as e:
        # wait_for 的 TimeoutError 无 message,error_type 是唯一有效信息
        info["path"] = "rule_fallback"
        logger.warning(
            "NLU LLM unavailable, using rule result",
            error=str(e)[:200], error_type=type(e).__name__,
        )
    except Exception as e:
        info["path"] = "rule_fallback"
        logger.error(
            "NLU slow path unexpected error, using rule result",
            error_type=type(e).__name__, exc_info=True,
        )

    return intent, entities, info
