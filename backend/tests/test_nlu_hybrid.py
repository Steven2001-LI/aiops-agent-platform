"""
AIOps Agent Platform - NLU 快慢路径混合层测试(D4,任务书用例 1-13)

测试原则:
- 零真实 LLM 调用:复用 tests/test_llm_service.py 的脚本化 fake 传输层,
  快路径分类/抽取、闭集自修复、超时降级等业务逻辑全部真实执行
- 不 mock IntentClassifier/EntityExtractor/hybrid 任何业务方法;
  每个走慢路径的用例断言 fake 传输层真实收到请求
- 配置经 LLMConfig(_env_file=None) 依赖注入,不读 .env、不碰全局配置单例;
  未注入路径用例只 monkeypatch app.nlu.hybrid 命名空间符号
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any

import pytest

import app.nlu.hybrid as hybrid_module
from app.config import LLMConfig
from app.data.knowledge_base import SERVICE_TOPOLOGY
from app.nlu.entity_extractor import AIOpsEntities
from app.nlu.hybrid import KNOWN_SYMPTOMS, _fast_path_confident, hybrid_understand
from app.nlu.intent_classifier import IntentType, UserIntent
from app.services.llm_service import LLMService
from tests.test_llm_service import FakeCompletions, make_fake_response

KNOWN_SERVICES = list(SERVICE_TOPOLOGY.keys())

# 实测锚点(动手前已核实):
# "为什么下单这么慢" → fault_diagnosis/0.35 + order-service/high_latency
# "看一下cpu多少"    → metric_query/0.6(恰好 == 默认阈值)+ 双空实体
# "系统有点不对劲"   → general_question/0.0 + 双空实体
# "消息队列好像有点不对劲" → general_question/0.0 + services=['kafka'](拓扑闭集外)
CONFIDENT_QUERY = "为什么下单这么慢"
BOUNDARY_QUERY = "看一下cpu多少"
AMBIGUOUS_QUERY = "系统有点不对劲"
KAFKA_QUERY = "消息队列好像有点不对劲"


# =============================================================================
# 测试基建
# =============================================================================


class HangingCompletions:
    """create() 记录请求后永久挂起,用于验证 wait_for 超时降级。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        await asyncio.Event().wait()


def make_nlu_service(
    scripted: list[Any],
    *,
    enable_nlu: bool = True,
    max_retries: int = 1,
    fast_path_confidence: float = 0.6,
) -> tuple[LLMService, FakeCompletions]:
    """构造注入 fake 传输层、带 enable_nlu 开关的 LLMService(隔离配置)。"""
    svc = LLMService(
        config=LLMConfig(
            _env_file=None,
            api_key="test-key",
            model="test-model",
            temperature=0.0,
            timeout_seconds=5,
            max_retries=max_retries,
            enable_nlu=enable_nlu,
            nlu_fast_path_confidence=fast_path_confidence,
        )
    )
    fake = FakeCompletions(scripted)
    svc._client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    return svc, fake


def valid_nlu_json(**overrides: Any) -> str:
    """合法 LLM NLU 响应(修订 2:覆盖 LLMNLUResult 全部字段,含无默认值的
    intent/intent_confidence)。缺任何必填项,"合法响应"会意外触发自修复,
    fake.calls 计数全盘错位。"""
    payload: dict[str, Any] = {
        "intent": "fault_diagnosis",
        "intent_confidence": 0.85,
        "services": ["order-service"],
        "symptoms": ["high_latency"],
        "urgency": "medium",
        "time_range": "now",
        "business_domain": "infrastructure",
        "missing_info": [],
        "clarification_question": "",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def user_payload(call: dict[str, Any]) -> dict[str, Any]:
    """取某次请求的 user 消息 JSON(证据包形态)。"""
    return json.loads(call["messages"][1]["content"])


# =============================================================================
# 用例 1:fast_rule —— 高置信查询直接走快路径,零 LLM 请求
# =============================================================================


@pytest.mark.asyncio
async def test_fast_rule_high_confidence() -> None:
    # 实测 conf=0.35,注入阈值 0.3 使其"有把握"(默认 0.6 的校准留到 D7)
    svc, fake = make_nlu_service([], fast_path_confidence=0.3)

    intent, entities, info = await hybrid_understand(
        CONFIDENT_QUERY, known_services=KNOWN_SERVICES, llm_service=svc
    )

    assert info["path"] == "fast_rule"
    assert info["clarification"] == ""
    assert fake.calls == []
    assert intent.intent == IntentType.FAULT_DIAGNOSIS
    assert entities.services == ["order-service"]
    assert entities.symptoms == ["high_latency"]


# =============================================================================
# 用例 1b:fast_rule —— 服务+症状双非空即快路径,即便意图被误判 general_question
# (D4.5:D7 实测暴露分类器盲区,实体成功才是可执行性充分条件)
# =============================================================================


@pytest.mark.asyncio
async def test_fast_rule_when_entities_present_despite_weak_intent() -> None:
    # "订单服务报错了" 实测 general_question/0.0,但服务+症状都干净抽到 →
    # 应走快路径零成本(旧判据会因 general_question 误升级 LLM)
    svc, fake = make_nlu_service([], fast_path_confidence=0.3)

    intent, entities, info = await hybrid_understand(
        "订单服务报错了", known_services=KNOWN_SERVICES, llm_service=svc
    )

    assert entities.services == ["order-service"]
    assert entities.symptoms == ["high_error_rate"]
    assert info["path"] == "fast_rule"  # 双实体非空 → 快路径,不烧 LLM
    assert fake.calls == []


@pytest.mark.asyncio
async def test_service_only_no_symptom_still_slow() -> None:
    # 只提服务、没提症状的真模糊句仍须走慢路径(判据要求"双非空",非"任一非空")。
    # KAFKA_QUERY: general_question + services=[kafka] + symptoms=[]
    svc, fake = make_nlu_service(
        [make_fake_response(valid_nlu_json())], fast_path_confidence=0.3
    )

    # 规则侧抽取:只有服务、无症状(在返回值被 LLM 结果覆盖之前先验证)
    rule_ents = hybrid_module._extractor.extract(KAFKA_QUERY)
    assert rule_ents.services and not rule_ents.symptoms  # 服务非空、症状空

    _, _, info = await hybrid_understand(
        KAFKA_QUERY, known_services=KNOWN_SERVICES, llm_service=svc
    )

    assert info["path"] == "llm_enhanced"  # 不满足双非空 → 仍升级 LLM(补症状/反问)
    assert len(fake.calls) == 1


# =============================================================================
# 用例 2:llm_enhanced —— 模糊句升级 LLM,结果换成 LLM 值
# =============================================================================


@pytest.mark.asyncio
async def test_llm_enhanced_ambiguous_query() -> None:
    svc, fake = make_nlu_service([make_fake_response(valid_nlu_json())])

    intent, entities, info = await hybrid_understand(
        AMBIGUOUS_QUERY, known_services=KNOWN_SERVICES, llm_service=svc
    )

    assert info["path"] == "llm_enhanced"
    assert len(fake.calls) == 1
    assert info["llm_meta"]["attempts"] == 1
    # intent/entities 逐字段换成 LLM 结果
    assert intent.intent == IntentType.FAULT_DIAGNOSIS
    assert intent.confidence == 0.85
    assert intent.raw_query == AMBIGUOUS_QUERY
    assert entities.services == ["order-service"]
    assert entities.symptoms == ["high_latency"]
    assert entities.urgency == "medium"
    assert entities.time_range == "now"
    # LLM 的 "infrastructure" 转换为现有实体模型的空串语义
    assert entities.business_domain == ""
    assert entities.raw_query == AMBIGUOUS_QUERY


# =============================================================================
# 用例 3:llm_enhanced + 反问 —— 缺关键信息时反问原文透传
# =============================================================================


@pytest.mark.asyncio
async def test_clarification_passthrough() -> None:
    question = "请问是哪个服务出现问题?大概什么症状?"
    svc, fake = make_nlu_service([
        make_fake_response(valid_nlu_json(
            services=[],
            symptoms=[],
            missing_info=["service", "symptom"],
            clarification_question=question,
        ))
    ])

    intent, entities, info = await hybrid_understand(
        AMBIGUOUS_QUERY, known_services=KNOWN_SERVICES, llm_service=svc
    )

    assert info["path"] == "llm_enhanced"
    assert len(fake.calls) == 1
    assert info["clarification"] == question
    assert info["missing_info"] == ["service", "symptom"]
    assert entities.services == []
    assert entities.symptoms == []


# =============================================================================
# 用例 4:rule_fallback(传输错误)—— 降级结果与纯快路径逐一相等、无 LLM 残留
# =============================================================================


@pytest.mark.asyncio
async def test_rule_fallback_transport_error() -> None:
    # 对照:同输入、开关关闭的纯快路径结果
    ctl_svc, ctl_fake = make_nlu_service([], enable_nlu=False)
    ctl_intent, ctl_entities, _ = await hybrid_understand(
        AMBIGUOUS_QUERY, known_services=KNOWN_SERVICES, llm_service=ctl_svc
    )
    assert ctl_fake.calls == []

    # max_retries=0:单次尝试无退避,测试不真等
    svc, fake = make_nlu_service([ConnectionError("boom")], max_retries=0)
    intent, entities, info = await hybrid_understand(
        AMBIGUOUS_QUERY, known_services=KNOWN_SERVICES, llm_service=svc
    )

    assert info["path"] == "rule_fallback"
    assert len(fake.calls) == 1  # 请求真实到达传输层
    # 降级返回快路径已算出的结果
    assert intent == ctl_intent
    assert entities == ctl_entities
    # 降级污染防线:LLM 附属字段不许残留
    assert "llm_meta" not in info
    assert "missing_info" not in info
    assert info["clarification"] == ""


# =============================================================================
# 用例 5:rule_fallback(超时)—— 传输层永挂,wait_for 兜住并降级
# =============================================================================


@pytest.mark.asyncio
async def test_rule_fallback_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    svc, _ = make_nlu_service([])
    hanging = HangingCompletions()
    svc._client = SimpleNamespace(chat=SimpleNamespace(completions=hanging))
    monkeypatch.setattr(hybrid_module, "NLU_SLOW_PATH_TIMEOUT_SECONDS", 0.05)

    ctl_svc, _ctl_fake = make_nlu_service([], enable_nlu=False)
    ctl_intent, ctl_entities, _ = await hybrid_understand(
        AMBIGUOUS_QUERY, known_services=KNOWN_SERVICES, llm_service=ctl_svc
    )

    t0 = time.monotonic()
    intent, entities, info = await hybrid_understand(
        AMBIGUOUS_QUERY, known_services=KNOWN_SERVICES, llm_service=svc
    )
    elapsed = time.monotonic() - t0

    assert elapsed < 1.0
    assert info["path"] == "rule_fallback"
    assert len(hanging.calls) == 1  # 请求真实到达并挂在传输层
    assert intent == ctl_intent
    assert entities == ctl_entities
    assert "llm_meta" not in info
    assert "missing_info" not in info
    assert info["clarification"] == ""


# =============================================================================
# 用例 6:rule_fallback(误配)—— enable_nlu=True 但 api_key 为空,零传输调用
# =============================================================================


@pytest.mark.asyncio
async def test_rule_fallback_misconfig() -> None:
    svc = LLMService(
        config=LLMConfig(
            _env_file=None, api_key="", enable_nlu=True, max_retries=2
        )
    )
    assert svc.available is False  # 无 client,物理上零传输调用

    intent, entities, info = await hybrid_understand(
        AMBIGUOUS_QUERY, known_services=KNOWN_SERVICES, llm_service=svc
    )

    assert info["path"] == "rule_fallback"
    assert intent.intent == IntentType.GENERAL_QUESTION
    assert "llm_meta" not in info
    assert "missing_info" not in info
    assert info["clarification"] == ""


# =============================================================================
# 用例 6b:rule_fallback(兜底)—— 非 LLMUnavailableError/TimeoutError 的裸异常
# (验收补强:评审变异 M9 显示删掉兜底块 16 用例仍全绿,此用例钉住该分支)
# =============================================================================


class ExplodingLLMService:
    """structured_completion 直接抛非 LLMUnavailableError 异常的接口边界 stub。

    传输层 fake 抛的任何异常都会被 structured_completion 的重试环包装成
    LLMUnavailableError,兜底 except Exception 分支经传输层无法诚实触达;
    本 stub 打在 hybrid 与 llm_service 的接口边界上,钉住"任意异常降级"契约。
    """

    def __init__(self) -> None:
        self._cfg = LLMConfig(_env_file=None, api_key="test-key", enable_nlu=True)

    async def structured_completion(self, **kwargs: Any) -> Any:
        raise RuntimeError("unexpected internal bug")


@pytest.mark.asyncio
async def test_rule_fallback_unexpected_exception() -> None:
    """兜底分支:裸异常同样降级返回快路径结果且无 LLM 字段残留,绝不冒泡。"""
    intent, entities, info = await hybrid_understand(
        AMBIGUOUS_QUERY,
        known_services=KNOWN_SERVICES,
        llm_service=ExplodingLLMService(),  # type: ignore[arg-type]
    )

    assert info["path"] == "rule_fallback"
    assert intent.intent == IntentType.GENERAL_QUESTION
    assert "llm_meta" not in info
    assert "missing_info" not in info
    assert info["clarification"] == ""


# =============================================================================
# 用例 7:开关关闭 —— 模糊句也不升级,零 LLM 请求
# =============================================================================


@pytest.mark.asyncio
async def test_switch_off_fast_rule() -> None:
    svc, fake = make_nlu_service([], enable_nlu=False)

    intent, entities, info = await hybrid_understand(
        AMBIGUOUS_QUERY, known_services=KNOWN_SERVICES, llm_service=svc
    )

    assert info["path"] == "fast_rule"
    assert fake.calls == []
    assert intent.intent == IntentType.GENERAL_QUESTION


# =============================================================================
# 用例 8:开关翻转探针 —— 开关必须每次调用现读,不许退化为快照
# =============================================================================


@pytest.mark.asyncio
async def test_injected_switch_flips_between_calls() -> None:
    """同一注入 svc 两次调用之间翻转开关,必须立即生效。"""
    svc, fake = make_nlu_service(
        [make_fake_response(valid_nlu_json())], enable_nlu=False
    )

    _, _, info1 = await hybrid_understand(
        AMBIGUOUS_QUERY, known_services=KNOWN_SERVICES, llm_service=svc
    )
    assert info1["path"] == "fast_rule"
    assert fake.calls == []

    svc._cfg.enable_nlu = True
    _, _, info2 = await hybrid_understand(
        AMBIGUOUS_QUERY, known_services=KNOWN_SERVICES, llm_service=svc
    )
    assert info2["path"] == "llm_enhanced"
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_uninjected_switch_follows_global_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未注入(生产)路径:开关永远随全局配置走,不缓存快照。

    monkeypatch 只替换 hybrid 命名空间里的 get_config / get_llm_service 符号,
    不调用真实全局单例、不碰环境变量。
    """
    svc, fake = make_nlu_service(
        [make_fake_response(valid_nlu_json())] * 2, enable_nlu=True
    )
    global_cfg = SimpleNamespace(
        llm=SimpleNamespace(
            enable_nlu=True, nlu_fast_path_confidence=0.6, nlu_model=""
        )
    )
    monkeypatch.setattr(hybrid_module, "get_config", lambda: global_cfg)
    monkeypatch.setattr(hybrid_module, "get_llm_service", lambda: svc)

    _, _, info1 = await hybrid_understand(
        AMBIGUOUS_QUERY, known_services=KNOWN_SERVICES
    )
    assert info1["path"] == "llm_enhanced"
    assert len(fake.calls) == 1

    # 全局配置翻关必须立即生效。用全新配置对象整体替换而非原地改属性:
    # reload_config() 就是整体换对象;缓存了旧 cfg 对象引用的错误实现
    # 透过引用仍能看到原地翻转(评审变异 M7 存活路径),换新对象才杀得死
    fresh_cfg = SimpleNamespace(
        llm=SimpleNamespace(
            enable_nlu=False, nlu_fast_path_confidence=0.6, nlu_model=""
        )
    )
    monkeypatch.setattr(hybrid_module, "get_config", lambda: fresh_cfg)
    _, _, info2 = await hybrid_understand(
        AMBIGUOUS_QUERY, known_services=KNOWN_SERVICES
    )
    assert info2["path"] == "fast_rule"
    assert len(fake.calls) == 1


# =============================================================================
# 用例 9:闭集双向 —— 出集拒绝自修复 / 集内不常见值一次通过
# =============================================================================


@pytest.mark.asyncio
async def test_out_of_set_service_self_repair() -> None:
    svc, fake = make_nlu_service([
        make_fake_response(valid_nlu_json(services=["unknown-service"])),
        make_fake_response(valid_nlu_json()),
    ])

    intent, entities, info = await hybrid_understand(
        AMBIGUOUS_QUERY, known_services=KNOWN_SERVICES, llm_service=svc
    )

    assert len(fake.calls) == 2
    # 自修复真实发生:第 2 次请求多出 assistant(坏输出)+ user(闭集错误反馈)
    second_messages = fake.calls[1]["messages"]
    assert len(second_messages) == 4
    assert "unknown-service" in second_messages[2]["content"]
    assert "不在已知服务列表" in second_messages[3]["content"]

    assert info["path"] == "llm_enhanced"
    assert info["llm_meta"]["attempts"] == 2
    assert entities.services == ["order-service"]


@pytest.mark.asyncio
async def test_uncommon_closed_set_values_pass() -> None:
    """闭集"接受"方向:合法但不常见的组合必须一次通过,不触发自修复。"""
    assert "amount_mismatch" in KNOWN_SYMPTOMS and "oversold" in KNOWN_SYMPTOMS
    svc, fake = make_nlu_service([
        make_fake_response(valid_nlu_json(
            services=["elasticsearch"],
            symptoms=["amount_mismatch", "oversold"],
        ))
    ])

    _, entities, info = await hybrid_understand(
        AMBIGUOUS_QUERY, known_services=KNOWN_SERVICES, llm_service=svc
    )

    assert info["path"] == "llm_enhanced"
    assert info["llm_meta"]["attempts"] == 1
    assert len(fake.calls) == 1
    assert entities.services == ["elasticsearch"]
    assert entities.symptoms == ["amount_mismatch", "oversold"]


# =============================================================================
# 用例 10:schema 畸形响应 —— 非法枚举值走 schema 校验自修复
# =============================================================================


@pytest.mark.asyncio
async def test_malformed_schema_self_repair() -> None:
    svc, fake = make_nlu_service([
        make_fake_response(valid_nlu_json(intent="totally_bogus")),
        make_fake_response(valid_nlu_json()),
    ])

    intent, _, info = await hybrid_understand(
        AMBIGUOUS_QUERY, known_services=KNOWN_SERVICES, llm_service=svc
    )

    assert len(fake.calls) == 2
    assert "未通过校验" in fake.calls[1]["messages"][3]["content"]
    assert info["path"] == "llm_enhanced"
    assert info["llm_meta"]["attempts"] == 2
    assert intent.intent == IntentType.FAULT_DIAGNOSIS


# =============================================================================
# 用例 11:_fast_path_confident 判据单测(阈值经参数传入)
# =============================================================================


def test_fast_path_confident_unit() -> None:
    both = AIOpsEntities(services=["order-service"], symptoms=["high_latency"])
    svc_only = AIOpsEntities(services=["order-service"])  # 单实体,不触发 D4.5 短路
    empty = AIOpsEntities()

    # D4.5 新判据:服务+症状双非空 → 有把握,意图/置信度均不再一票否决
    weak = UserIntent(intent=IntentType.GENERAL_QUESTION, confidence=0.0)
    assert _fast_path_confident(weak, both, threshold=0.6) is True
    low_but_both = UserIntent(intent=IntentType.FAULT_DIAGNOSIS, confidence=0.1)
    assert _fast_path_confident(low_but_both, both, threshold=0.6) is True

    # 以下拒绝分支须用"非双实体"隔离(否则被 D4.5 短路成 True):
    # 低置信 + 单实体 → 没把握
    low = UserIntent(intent=IntentType.FAULT_DIAGNOSIS, confidence=0.35)
    assert _fast_path_confident(low, svc_only, threshold=0.6) is False

    # GENERAL_QUESTION + 单实体 → 置信度再高也没把握
    general = UserIntent(intent=IntentType.GENERAL_QUESTION, confidence=0.9)
    assert _fast_path_confident(general, svc_only, threshold=0.6) is False

    # 诊断类意图但服务/症状双空(下游没法构造告警)→ 没把握
    blind = UserIntent(intent=IntentType.FAULT_DIAGNOSIS, confidence=0.9)
    assert _fast_path_confident(blind, empty, threshold=0.6) is False

    # 正常:高置信 + 实体齐全 → 有把握
    good = UserIntent(intent=IntentType.FAULT_DIAGNOSIS, confidence=0.9)
    assert _fast_path_confident(good, both, threshold=0.6) is True

    # 修订 1 阈值边界:实测"看一下cpu多少" conf 恰好 0.6000 == 默认阈值,
    # 判据必须是 conf < threshold 才算没把握(== 阈值 → 有把握走快路径)。
    # metric_query 不在双空实体判据的意图集合里,空实体不碍事。
    boundary_intent = hybrid_module._classifier.classify(BOUNDARY_QUERY)
    boundary_entities = hybrid_module._extractor.extract(BOUNDARY_QUERY)
    assert boundary_intent.intent == IntentType.METRIC_QUERY
    assert boundary_intent.confidence == 0.6
    assert _fast_path_confident(
        boundary_intent, boundary_entities, threshold=0.6
    ) is True


# =============================================================================
# 用例 12:端点级 —— diagnose 响应含新字段、既有字段结构不变
# =============================================================================

EXPECTED_RESPONSE_KEYS = {
    # 既有 7 键
    "original_query", "intent", "understood_as", "diagnosis_plan",
    "explanation", "triggered_incident", "triggered_business_check",
    # D4 新增 2 键
    "nlu_path", "clarification_question",
}


def test_diagnose_endpoint_fast_rule_fields(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 未注入路径,monkeypatch hybrid 命名空间关掉开关 → 必走快路径
    global_cfg = SimpleNamespace(
        llm=SimpleNamespace(
            enable_nlu=False, nlu_fast_path_confidence=0.6, nlu_model=""
        )
    )
    monkeypatch.setattr(hybrid_module, "get_config", lambda: global_cfg)

    # metric_query 意图,不触发 incident 管道
    resp = client.post("/api/v1/incidents/diagnose", json={"query": BOUNDARY_QUERY})

    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == EXPECTED_RESPONSE_KEYS
    assert body["nlu_path"] == "fast_rule"
    assert body["clarification_question"] == ""
    assert body["intent"]["type"] == "metric_query"
    assert body["triggered_incident"] is None


def test_diagnose_endpoint_clarification(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    question = "请问是哪个服务出现问题?"
    svc, fake = make_nlu_service([
        make_fake_response(valid_nlu_json(
            services=[], symptoms=[],
            missing_info=["service", "symptom"],
            clarification_question=question,
        ))
    ])
    global_cfg = SimpleNamespace(
        llm=SimpleNamespace(
            enable_nlu=True, nlu_fast_path_confidence=0.6, nlu_model=""
        )
    )
    monkeypatch.setattr(hybrid_module, "get_config", lambda: global_cfg)
    monkeypatch.setattr(hybrid_module, "get_llm_service", lambda: svc)

    resp = client.post("/api/v1/incidents/diagnose", json={"query": AMBIGUOUS_QUERY})

    assert resp.status_code == 200
    assert len(fake.calls) == 1
    body = resp.json()
    assert set(body.keys()) == EXPECTED_RESPONSE_KEYS
    assert body["nlu_path"] == "llm_enhanced"
    assert body["clarification_question"] == question
    # symptoms 为空 → 不触发 incident 管道
    assert body["triggered_incident"] is None
    assert body["understood_as"]["services"] == []


# =============================================================================
# 用例 13:kafka 出集注记 —— 初步结果带注记,已知服务列表保持纯净
# =============================================================================


@pytest.mark.asyncio
async def test_kafka_out_of_set_note() -> None:
    """快路径同义词表含 kafka 但拓扑闭集没有:初步结果须加注记消除
    与系统提示"只能从已知服务选"的自相矛盾;闭集校验只管输出。"""
    assert "kafka" not in KNOWN_SERVICES
    svc, fake = make_nlu_service([make_fake_response(valid_nlu_json())])

    _, _, info = await hybrid_understand(
        KAFKA_QUERY, known_services=KNOWN_SERVICES, llm_service=svc
    )

    assert info["path"] == "llm_enhanced"
    assert len(fake.calls) == 1  # 合法响应一次通过
    payload = user_payload(fake.calls[0])
    preliminary = payload["规则快路径初步结果"]
    assert preliminary["services"] == ["kafka"]
    assert "kafka" in preliminary["note"]
    assert "仅供参考" in preliminary["note"]
    assert "kafka" not in payload["已知服务"]
