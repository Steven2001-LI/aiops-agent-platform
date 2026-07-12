"""
AIOps Agent Platform - RCA Agent LLM 集成测试（D2-3，任务书用例 1-7 + 回炉修订单 B1-B6/C）

测试原则：
- 零真实 LLM 调用：复用 tests/test_llm_service.py 的脚本化 fake 传输层，
  schema 校验 / 闭集自修复 / 超时降级 / 交叉校准等业务逻辑全部真实执行
- 不 mock RCAAgent 任何业务方法；每个走 LLM 路径的用例断言 fake 传输层
  收到请求，证明请求真实到达
- 配置经 LLMConfig(_env_file=None) 依赖注入，不读 .env、不碰全局配置单例；
  enable_rca 开关随注入的 LLMService 配置生效
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

import app.agents.rca_agent as rca_agent_module
from app.agents.rca_agent import RCAAgent, RCAInput
from app.config import LLMConfig
from app.models.agent import AgentExecutionContext
from app.models.events import AlertEvent, SeverityLevel
from app.services.llm_service import LLMService
from tests.test_llm_service import FakeCompletions, make_fake_response


# =============================================================================
# 测试基建
# =============================================================================


class HangingCompletions:
    """create() 记录请求后永久挂起，用于验证 wait_for 超时降级。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        await asyncio.Event().wait()


def make_rca_service(
    scripted: list[Any],
    *,
    enable_rca: bool = True,
    max_retries: int = 1,
) -> tuple[LLMService, FakeCompletions]:
    """构造注入 fake 传输层、带 enable_rca 开关的 LLMService（隔离配置）。"""
    svc = LLMService(
        config=LLMConfig(
            _env_file=None,
            api_key="test-key",
            model="test-model",
            temperature=0.0,
            timeout_seconds=5,
            max_retries=max_retries,
            enable_rca=enable_rca,
        )
    )
    fake = FakeCompletions(scripted)
    svc._client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    return svc, fake


def make_agent(svc: LLMService) -> RCAAgent:
    """注入 LLMService 的 RCAAgent。

    _memory_init_attempted 置 True 走既有惰性初始化开关跳过 MemorySystem
    （ChromaDB），保证规则路径确定性；不是 mock 业务方法。
    """
    agent = RCAAgent(llm_service=svc)
    agent._memory_init_attempted = True
    return agent


def make_alert() -> AlertEvent:
    return AlertEvent(
        service="order-service",
        metric="memory_usage_percent",
        value=95.0,
        threshold=80.0,
        severity=SeverityLevel.HIGH,
        labels={"environment": "production"},
    )


def valid_llm_json(
    root_cause: str,
    confidence: float,
    *,
    chain: list[str] | None = None,
) -> str:
    """合法 LLM 响应。

    reasoning_chain 至少两步且每步引用 [E*]（schema min_length=2），
    否则"合法响应"会意外触发自修复重试，fake.calls 计数全盘错位。
    """
    payload = {
        "root_cause": root_cause,
        "confidence": confidence,
        "reasoning_chain": chain or [
            f"[E2] 贝叶斯后验最高的候选是 {root_cause}",
            "[E1] 拓扑影响链与该假设的传播方向一致",
        ],
        "evidence_used": ["bayesian", "topology"],
        "alternative_hypothesis": "",
        "alternative_rejected_because": "",
        "suggested_actions": [],
        "needs_human_review": False,
    }
    return json.dumps(payload, ensure_ascii=False)


async def run_rule_only(
    alert: AlertEvent, context: AgentExecutionContext
) -> tuple[str, float]:
    """同输入的纯规则对照跑（enable_rca=False），返回 (root_cause, confidence)。"""
    svc, fake = make_rca_service([], enable_rca=False)
    agent = make_agent(svc)
    result = await agent.process(RCAInput(alert=alert, incident_id="ctl"), context)
    assert result.success is True
    assert fake.calls == []
    return result.output_data["root_cause"], result.output_data["confidence"]


def rca_evidence(result: Any) -> dict[str, Any]:
    """取 model_dump 后形态的 evidence（API 序列化出去的唯一位置）。"""
    return result.output_data["rca_event"]["evidence"]


# =============================================================================
# 用例 1：llm_hybrid —— 合法响应，断言锚点打到 model_dump 后形态
# =============================================================================


@pytest.mark.asyncio
async def test_llm_hybrid_end_to_end(agent_context: AgentExecutionContext) -> None:
    svc, fake = make_rca_service(
        [make_fake_response(valid_llm_json("resource_exhaustion", 0.9))]
    )
    agent = make_agent(svc)

    result = await agent.process(
        RCAInput(alert=make_alert(), incident_id="inc-hybrid"), agent_context
    )

    assert result.success is True
    # 断言锚点：GET /incidents/{id} 序列化出去的形态，不是内部方法返回值
    evidence = rca_evidence(result)
    assert evidence["reasoning_mode"] == "llm_hybrid"
    assert len(fake.calls) == 1
    # 不变量 7：output_data 顶层禁止出现 "evidence" 键
    # （eval_agent.py:573 存在按该键的沉睡读取，唤醒即事故）
    assert "evidence" not in result.output_data

    assert len(evidence["reasoning_chain"]) >= 2
    for step in evidence["reasoning_chain"]:
        assert "[E" in step
    assert evidence["evidence_used"] == ["bayesian", "topology"]
    assert evidence["needs_human_review"] is False
    assert evidence["llm_meta"]["attempts"] == 1
    assert result.output_data["root_cause"] == "resource_exhaustion"


# =============================================================================
# 用例 2：rule_fallback —— 传输异常耗尽重试，结果与纯规则一致
# =============================================================================


@pytest.mark.asyncio
async def test_rule_fallback_on_transport_error(
    agent_context: AgentExecutionContext,
) -> None:
    ctl_cause, ctl_conf = await run_rule_only(make_alert(), agent_context)

    # max_retries=0：attempts=1，无退避等待，测试不真等
    svc, fake = make_rca_service([ConnectionError("boom")], max_retries=0)
    agent = make_agent(svc)

    result = await agent.process(
        RCAInput(alert=make_alert(), incident_id="inc-fallback"), agent_context
    )

    assert result.success is True
    evidence = rca_evidence(result)
    assert evidence["reasoning_mode"] == "rule_fallback"
    # 降级污染防线：LLM 附属字段不许出现在降级结果里
    assert "llm_meta" not in evidence
    assert "reasoning_chain" not in evidence
    assert len(fake.calls) == 1
    # 降级后 root_cause/confidence 与同输入纯规则路径一致
    assert result.output_data["root_cause"] == ctl_cause
    assert result.output_data["confidence"] == ctl_conf


# =============================================================================
# 用例 3：rule_only —— 开关关闭，零 LLM 请求
# =============================================================================


@pytest.mark.asyncio
async def test_rule_only_when_disabled(agent_context: AgentExecutionContext) -> None:
    svc, fake = make_rca_service([], enable_rca=False)
    agent = make_agent(svc)

    result = await agent.process(
        RCAInput(alert=make_alert(), incident_id="inc-ruleonly"), agent_context
    )

    assert result.success is True
    evidence = rca_evidence(result)
    assert evidence["reasoning_mode"] == "rule_only"
    assert "llm_meta" not in evidence
    assert "reasoning_chain" not in evidence
    assert fake.calls == []


# =============================================================================
# 用例 4：闭集自修复 —— 集外值被 validate_extra 拦下，错误反馈喂回模型
# =============================================================================


@pytest.mark.asyncio
async def test_closed_set_self_repair(agent_context: AgentExecutionContext) -> None:
    svc, fake = make_rca_service(
        [
            make_fake_response(valid_llm_json("alien_root_cause", 0.9)),
            make_fake_response(valid_llm_json("resource_exhaustion", 0.9)),
        ],
        max_retries=1,
    )
    agent = make_agent(svc)

    result = await agent.process(
        RCAInput(alert=make_alert(), incident_id="inc-selfrepair"), agent_context
    )

    assert result.success is True
    assert len(fake.calls) == 2

    # 自修复真实发生：第 2 次请求多出 assistant（坏输出）+ user（闭集错误反馈）
    second_messages = fake.calls[1]["messages"]
    assert len(second_messages) == 4
    assert "alien_root_cause" in second_messages[2]["content"]
    feedback = second_messages[3]["content"]
    assert "alien_root_cause" in feedback
    assert "不在候选列表中" in feedback

    # 最终结果取第 2 次合法响应
    assert result.output_data["root_cause"] == "resource_exhaustion"
    assert rca_evidence(result)["reasoning_mode"] == "llm_hybrid"


# =============================================================================
# 用例 5：交叉校准 —— 一致增强 / 分歧打折 + 审计字段
# =============================================================================


@pytest.mark.asyncio
async def test_agreement_calibration(agent_context: AgentExecutionContext) -> None:
    ctl_cause, ctl_conf = await run_rule_only(make_alert(), agent_context)

    svc, fake = make_rca_service([make_fake_response(valid_llm_json(ctl_cause, 0.9))])
    agent = make_agent(svc)
    result = await agent.process(
        RCAInput(alert=make_alert(), incident_id="inc-agree"), agent_context
    )

    assert result.success is True
    assert len(fake.calls) == 1
    evidence = rca_evidence(result)
    assert evidence["reasoning_mode"] == "llm_hybrid"
    # 一致：confidence 取 max + 0.05，封顶 0.98
    assert result.output_data["confidence"] == round(
        min(0.98, max(ctl_conf, 0.9) + 0.05), 4
    )
    assert "rule_llm_disagreement" not in evidence


@pytest.mark.asyncio
async def test_disagreement_calibration(agent_context: AgentExecutionContext) -> None:
    ctl_cause, _ = await run_rule_only(make_alert(), agent_context)

    # 动态挑一个与规则结论（规范化后）不同的先验候选，保证真分歧
    llm_cause = next(
        k for k in RCAAgent.PRIOR_PROBABILITIES
        if k != RCAAgent._normalize_root_cause(ctl_cause)
    )
    svc, fake = make_rca_service([make_fake_response(valid_llm_json(llm_cause, 0.9))])
    agent = make_agent(svc)
    result = await agent.process(
        RCAInput(alert=make_alert(), incident_id="inc-disagree"), agent_context
    )

    assert result.success is True
    assert len(fake.calls) == 1
    evidence = rca_evidence(result)
    assert evidence["reasoning_mode"] == "llm_hybrid"
    # 分歧：采用 LLM 结论，confidence = llm × 0.85
    assert result.output_data["root_cause"] == llm_cause
    assert result.output_data["confidence"] == round(0.9 * 0.85, 4)
    # 审计字段存原始串（未规范化）
    assert evidence["rule_llm_disagreement"] == {"rule": ctl_cause, "llm": llm_cause}


# =============================================================================
# 用例 6：规则结论出集 —— E5 注记存在、候选集保持纯净（修正 A 选项 b）
# =============================================================================


@pytest.mark.asyncio
async def test_out_of_set_rule_result_noted_in_e5() -> None:
    svc, fake = make_rca_service(
        [make_fake_response(valid_llm_json("resource_exhaustion", 0.9))]
    )
    agent = make_agent(svc)
    alert = make_alert()
    symptoms = agent._extract_symptoms(alert)
    impact_chain = agent.bfs_traverse(alert.service)
    bayesian_results = agent.bayesian_inference(symptoms)
    rag_results = agent._rag_retrieve(alert, symptoms)
    # 记忆拼接串型规则结论（真实 MemorySystem 命中历史故障时的产物），不在候选集内
    out_of_set_cause = "resource_exhaustion (historical_match: INC-42)"
    rule_result = (out_of_set_cause, 0.8, {})

    parsed, meta = await agent._llm_synthesize(
        alert, symptoms, impact_chain, bayesian_results, rag_results, [], rule_result,
    )

    assert len(fake.calls) == 1
    evidence_pack = json.loads(fake.calls[0]["messages"][1]["content"])
    e5 = evidence_pack["E5_rule_engine_preliminary"]
    assert e5["root_cause"] == out_of_set_cause
    assert "仅供参考" in e5["note"]
    # 候选集保持纯净：拼接串没有被并入
    assert out_of_set_cause not in evidence_pack["candidate_root_causes"]
    assert parsed.root_cause == "resource_exhaustion"
    assert meta["attempts"] == 1


@pytest.mark.asyncio
async def test_in_set_rule_result_has_no_e5_note() -> None:
    """对照：规则结论在候选集内时不加注记。"""
    svc, fake = make_rca_service(
        [make_fake_response(valid_llm_json("resource_exhaustion", 0.9))]
    )
    agent = make_agent(svc)
    alert = make_alert()
    symptoms = agent._extract_symptoms(alert)

    await agent._llm_synthesize(
        alert,
        symptoms,
        agent.bfs_traverse(alert.service),
        agent.bayesian_inference(symptoms),
        agent._rag_retrieve(alert, symptoms),
        [],
        ("resource_exhaustion", 0.8, {}),
    )

    assert len(fake.calls) == 1
    evidence_pack = json.loads(fake.calls[0]["messages"][1]["content"])
    assert "note" not in evidence_pack["E5_rule_engine_preliminary"]


# =============================================================================
# 用例 7：超时降级 —— 传输层永挂，wait_for 兜住并降级规则路径
# =============================================================================


@pytest.mark.asyncio
async def test_timeout_fallback(agent_context: AgentExecutionContext) -> None:
    svc, _ = make_rca_service([])
    hanging = HangingCompletions()
    svc._client = SimpleNamespace(chat=SimpleNamespace(completions=hanging))
    agent = make_agent(svc)
    # 实例属性覆盖类常量，测试不真等 30s
    agent.LLM_SYNTHESIS_TIMEOUT_SECONDS = 0.05

    result = await agent.process(
        RCAInput(alert=make_alert(), incident_id="inc-timeout"), agent_context
    )

    assert result.success is True
    assert rca_evidence(result)["reasoning_mode"] == "rule_fallback"
    # 请求真实到达传输层并挂在那里
    assert len(hanging.calls) == 1


# =============================================================================
# 修订单 B1：enable_rca 开关必须每次 process() 调用时读，不许退化为快照
# =============================================================================


@pytest.mark.asyncio
async def test_injected_switch_flips_between_calls(
    agent_context: AgentExecutionContext,
) -> None:
    """同一 agent 实例两次 process() 之间翻转注入配置的开关，必须立即生效。

    杀死"__init__ 快照开关"类变异：快照实现下第二次调用仍走 rule_only。
    """
    svc, fake = make_rca_service(
        [make_fake_response(valid_llm_json("resource_exhaustion", 0.9))],
        enable_rca=False,
    )
    agent = make_agent(svc)

    r1 = await agent.process(
        RCAInput(alert=make_alert(), incident_id="inc-flip-1"), agent_context
    )
    assert rca_evidence(r1)["reasoning_mode"] == "rule_only"
    assert fake.calls == []

    svc._cfg.enable_rca = True
    r2 = await agent.process(
        RCAInput(alert=make_alert(), incident_id="inc-flip-2"), agent_context
    )
    assert rca_evidence(r2)["reasoning_mode"] == "llm_hybrid"
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_uninjected_switch_follows_global_config(
    agent_context: AgentExecutionContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """未注入（生产懒加载）路径：开关永远随全局配置走。

    杀死"懒加载缓存后 _llm_enabled 走注入分支读 _cfg 僵尸快照"缺陷（修订单 A1）：
    svc._cfg.enable_rca 恒为 True，若首次调用后开关退化为读该快照，
    全局配置翻关就失效，第二次调用会误走 LLM 路径。
    monkeypatch 只替换 rca_agent 命名空间里的 get_config / get_llm_service 符号，
    不调用真实全局单例、不碰环境变量。
    """
    svc, fake = make_rca_service(
        [make_fake_response(valid_llm_json("resource_exhaustion", 0.9))] * 2,
        enable_rca=True,
    )
    global_cfg = SimpleNamespace(llm=SimpleNamespace(enable_rca=True))
    monkeypatch.setattr(rca_agent_module, "get_config", lambda: global_cfg)
    monkeypatch.setattr(rca_agent_module, "get_llm_service", lambda: svc)

    agent = RCAAgent()  # 无参构造：走生产懒加载路径
    agent._memory_init_attempted = True

    r1 = await agent.process(
        RCAInput(alert=make_alert(), incident_id="inc-glb-1"), agent_context
    )
    assert rca_evidence(r1)["reasoning_mode"] == "llm_hybrid"
    assert len(fake.calls) == 1

    # 懒加载已把单例写回 agent._llm_service；全局配置翻关必须立即生效
    global_cfg.llm.enable_rca = False
    r2 = await agent.process(
        RCAInput(alert=make_alert(), incident_id="inc-glb-2"), agent_context
    )
    assert rca_evidence(r2)["reasoning_mode"] == "rule_only"
    assert len(fake.calls) == 1


# =============================================================================
# 修订单 B2：规范化比较钉死——单元断言 + 拼接串规则结论走增强分支
# =============================================================================


def test_normalize_root_cause_unit() -> None:
    """杀死 _normalize_root_cause 恒等函数变异。"""
    n = RCAAgent._normalize_root_cause
    assert n("Root cause: resource_exhaustion") == "resource_exhaustion"
    assert n("resource_exhaustion (historical_match: INC-42)") == "resource_exhaustion"
    assert n("Resource_Exhaustion, pod oom") == "resource_exhaustion"
    assert n("  Network_Issue  ") == "network_issue"


class FakeMemorySystem:
    """伪外部记忆存储层（与 fake LLM 传输层同理，不是 mock 业务方法）。

    返回一条 retrieval_score>0.5 且 content 含 "Root cause:" 的历史记忆，
    驱动 _rule_based_synthesis 真实走历史命中分支，产出拼接串型根因
    "<bayesian_top> (historical_match: INC-42)"。
    """

    async def search(self, **kwargs: Any) -> Any:
        entry = SimpleNamespace(
            memory_id="mem-42",
            content="Root cause: resource_exhaustion. Pod OOM after traffic ramp.",
            summary="INC-42 内存耗尽",
            source_agent="rca_agent",
            source_incident_id="INC-42",
            importance_score=0.9,
            retrieval_score=0.8,
            tags=["root_cause"],
        )
        return SimpleNamespace(results=[entry], similarities=[0.83], total_found=1)


@pytest.mark.asyncio
async def test_concat_rule_cause_agrees_with_clean_llm_cause(
    agent_context: AgentExecutionContext,
) -> None:
    """规则结论为记忆拼接串、LLM 返回干净同义串 → 必须判一致走增强分支。

    杀死"规范化被换成恒等函数"变异：裸串比较会把同义不同串误判成分歧，
    走打折分支并留下 rule_llm_disagreement。
    """
    # 对照跑（同 fake 记忆、开关关）：验证前提——规则结论确实是拼接串
    ctl_svc, ctl_fake = make_rca_service([], enable_rca=False)
    ctl_agent = make_agent(ctl_svc)
    ctl_agent._memory_system = FakeMemorySystem()
    ctl = await ctl_agent.process(
        RCAInput(alert=make_alert(), incident_id="ctl-mem"), agent_context
    )
    ctl_cause = ctl.output_data["root_cause"]
    ctl_conf = ctl.output_data["confidence"]
    assert "(historical_match: INC-42)" in ctl_cause
    assert ctl_fake.calls == []

    svc, fake = make_rca_service(
        [make_fake_response(valid_llm_json("resource_exhaustion", 0.93))]
    )
    agent = make_agent(svc)
    agent._memory_system = FakeMemorySystem()
    result = await agent.process(
        RCAInput(alert=make_alert(), incident_id="inc-mem"), agent_context
    )

    assert result.success is True
    assert len(fake.calls) == 1
    # 同一跑内规则侧确实产出拼接串（进了证据包 E5）
    e5 = json.loads(fake.calls[0]["messages"][1]["content"])["E5_rule_engine_preliminary"]
    assert e5["root_cause"] == ctl_cause

    evidence = rca_evidence(result)
    assert evidence["reasoning_mode"] == "llm_hybrid"
    # 规范化后判一致：增强分支，无分歧审计字段，采用 LLM 干净串
    assert "rule_llm_disagreement" not in evidence
    assert result.output_data["root_cause"] == "resource_exhaustion"
    assert result.output_data["confidence"] == round(
        min(0.98, max(ctl_conf, 0.93) + 0.05), 4
    )


# =============================================================================
# 修订单 B4：闭集"接受"方向——RAG 贡献的候选（PRIOR 之外）一次通过
# =============================================================================


@pytest.mark.asyncio
async def test_rag_contributed_candidate_accepted(
    agent_context: AgentExecutionContext,
) -> None:
    """杀死"candidates 漏掉 RAG 并集"变异：该实现下 memory_leak 被闭集校验
    反复拒绝直至降级，永远到不了 llm_hybrid/attempts==1。
    """
    # memory_leak 来自 kb_002 的 root_causes，不在 PRIOR_PROBABILITIES 里
    assert "memory_leak" not in RCAAgent.PRIOR_PROBABILITIES
    svc, fake = make_rca_service(
        [make_fake_response(valid_llm_json("memory_leak", 0.9))]
    )
    agent = make_agent(svc)

    result = await agent.process(
        RCAInput(alert=make_alert(), incident_id="inc-rag-accept"), agent_context
    )

    assert result.success is True
    evidence = rca_evidence(result)
    assert evidence["reasoning_mode"] == "llm_hybrid"
    assert evidence["llm_meta"]["attempts"] == 1
    assert len(fake.calls) == 1
    assert result.output_data["root_cause"] == "memory_leak"

    # 证据包候选集同时含 PRIOR 键与 RAG 贡献键
    candidates = json.loads(fake.calls[0]["messages"][1]["content"])[
        "candidate_root_causes"
    ]
    assert "memory_leak" in candidates
    assert set(RCAAgent.PRIOR_PROBABILITIES) <= set(candidates)


# =============================================================================
# 修订单 B5：真实 schema（LLMRootCauseAnalysis）的畸形响应自修复
# =============================================================================


@pytest.mark.asyncio
async def test_real_schema_self_repair(agent_context: AgentExecutionContext) -> None:
    """首响应缺必填字段 evidence_used → schema 校验失败反馈喂回 → 次响应合法。

    杀死"删掉 LLMRootCauseAnalysis 必填/边界约束"变异：约束没了，
    畸形首响应直接通过，attempts 变 1。
    """
    bad_payload = json.loads(valid_llm_json("resource_exhaustion", 0.9))
    del bad_payload["evidence_used"]
    svc, fake = make_rca_service(
        [
            make_fake_response(json.dumps(bad_payload, ensure_ascii=False)),
            make_fake_response(valid_llm_json("resource_exhaustion", 0.9)),
        ],
        max_retries=1,
    )
    agent = make_agent(svc)

    result = await agent.process(
        RCAInput(alert=make_alert(), incident_id="inc-schema-repair"), agent_context
    )

    assert result.success is True
    assert len(fake.calls) == 2
    feedback = fake.calls[1]["messages"][3]["content"]
    assert "未通过校验" in feedback
    assert "evidence_used" in feedback

    evidence = rca_evidence(result)
    assert evidence["reasoning_mode"] == "llm_hybrid"
    assert evidence["llm_meta"]["attempts"] == 2


# =============================================================================
# 修订单 B6：alternative_* 透传 + 闭集校验第二分支（集外 alternative）
# =============================================================================


@pytest.mark.asyncio
async def test_alternative_fields_passthrough(
    agent_context: AgentExecutionContext,
) -> None:
    payload = json.loads(valid_llm_json("resource_exhaustion", 0.9))
    payload["alternative_hypothesis"] = "traffic_spike"  # 候选集内（PRIOR 键）
    payload["alternative_rejected_because"] = "[E2] traffic_spike 后验显著更低"
    svc, fake = make_rca_service(
        [make_fake_response(json.dumps(payload, ensure_ascii=False))]
    )
    agent = make_agent(svc)

    result = await agent.process(
        RCAInput(alert=make_alert(), incident_id="inc-alt"), agent_context
    )

    assert result.success is True
    assert len(fake.calls) == 1
    evidence = rca_evidence(result)
    assert evidence["reasoning_mode"] == "llm_hybrid"
    assert evidence["alternative_hypothesis"] == "traffic_spike"
    assert evidence["alternative_rejected_because"] == "[E2] traffic_spike 后验显著更低"


@pytest.mark.asyncio
async def test_out_of_set_alternative_self_repair(
    agent_context: AgentExecutionContext,
) -> None:
    """root_cause 在集内但 alternative_hypothesis 出集 → 第二校验分支拦下并自修复。"""
    bad_payload = json.loads(valid_llm_json("resource_exhaustion", 0.9))
    bad_payload["alternative_hypothesis"] = "quantum_flux"
    svc, fake = make_rca_service(
        [
            make_fake_response(json.dumps(bad_payload, ensure_ascii=False)),
            make_fake_response(valid_llm_json("resource_exhaustion", 0.9)),
        ],
        max_retries=1,
    )
    agent = make_agent(svc)

    result = await agent.process(
        RCAInput(alert=make_alert(), incident_id="inc-alt-repair"), agent_context
    )

    assert result.success is True
    assert len(fake.calls) == 2
    feedback = fake.calls[1]["messages"][3]["content"]
    assert "alternative_hypothesis 不在候选列表中" in feedback

    evidence = rca_evidence(result)
    assert evidence["reasoning_mode"] == "llm_hybrid"
    assert evidence["llm_meta"]["attempts"] == 2


# =============================================================================
# 修订单 C：生产最常见误配——enable_rca=True 但 api_key 为空
# =============================================================================


@pytest.mark.asyncio
async def test_enabled_without_api_key_falls_back(
    agent_context: AgentExecutionContext,
) -> None:
    """api_key 为空时 client 为 None，structured_completion 入口立即抛
    LLMUnavailableError（不进重试环、零传输调用），process 降级 rule_fallback。
    """
    svc = LLMService(
        config=LLMConfig(
            _env_file=None,
            api_key="",
            enable_rca=True,
            max_retries=2,
        )
    )
    assert svc.available is False  # 无 client，物理上零传输调用
    agent = make_agent(svc)

    result = await agent.process(
        RCAInput(alert=make_alert(), incident_id="inc-miscfg"), agent_context
    )

    assert result.success is True
    evidence = rca_evidence(result)
    assert evidence["reasoning_mode"] == "rule_fallback"
    assert "llm_meta" not in evidence
    assert "reasoning_chain" not in evidence
