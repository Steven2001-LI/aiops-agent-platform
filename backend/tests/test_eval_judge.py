"""
AIOps Agent Platform - EvalAgent LLM-as-Judge 集成测试（D6，任务书用例 1-8）

测试原则（沿用 test_rca_llm_integration.py / test_nlu_hybrid.py 全套):
- 零真实 LLM 调用:复用 tests/test_llm_service.py 的脚本化 fake 传输层,
  schema 校验 / 自修复重试 / 超时降级等业务逻辑全部真实执行
- 不 mock EvalAgent 任何业务方法;走 LLM 路径的用例断言 fake 传输层收到请求
- 配置经 LLMConfig(_env_file=None) 依赖注入,不读 .env、不碰全局配置单例;
  enable_judge 开关随注入的 LLMService 配置生效
- 未注入路径只 monkeypatch app.agents.eval_agent 命名空间符号
- 三态精确断言:关闭 / 成功融合 / 降级,合成分逐位钉死
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.agents.eval_agent as eval_agent_module
import app.api.routes as routes_module
from app.agents.eval_agent import EvalAgent, EvalInput, EvalType
from app.config import LLMConfig
from app.main import app
from app.models.agent import AgentExecutionContext
from app.models.events import RCAEvent
from app.services.llm_service import LLMService
from tests.test_llm_service import FakeCompletions, make_fake_response


# =============================================================================
# 测试基建
# =============================================================================


class HangingCompletions:
    """create() 记录请求后永久挂起,用于验证 JUDGE_TIMEOUT_SECONDS 超时降级。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(dict(kwargs))
        await asyncio.Event().wait()


class ExplodingLLMService:
    """structured_completion 直接抛非 LLMUnavailableError 异常的接口边界 stub。

    传输层 fake 抛的任何异常都会被 structured_completion 的重试环包装成
    LLMUnavailableError,兜底 except Exception 分支经传输层无法诚实触达;
    本 stub 打在接口边界上,钉住"任意异常只跳过 judge、绝不冒泡"契约。
    """

    def __init__(self, *, enable_judge: bool = True) -> None:
        self._cfg = LLMConfig(_env_file=None, api_key="test-key", enable_judge=enable_judge)

    async def structured_completion(self, **kwargs: Any) -> Any:
        raise RuntimeError("unexpected internal bug")


def make_eval_service(
    scripted: list[Any],
    *,
    enable_judge: bool = True,
    max_retries: int = 1,
    transport: Any = None,
) -> tuple[LLMService, Any]:
    """构造注入 fake 传输层、带 enable_judge 开关的 LLMService(隔离配置)。"""
    svc = LLMService(
        config=LLMConfig(
            _env_file=None,
            api_key="test-key",
            model="test-model",
            # 全局默认取非零:让 judge 调用里显式的 temperature=0.0 成为真锚点。
            # 若同为 0.0,删掉显式 0.0 退回默认后断言仍成立,"裁判必须可复现"无从守护。
            temperature=0.7,
            timeout_seconds=5,
            max_retries=max_retries,
            enable_judge=enable_judge,
        )
    )
    fake = transport if transport is not None else FakeCompletions(scripted)
    svc._client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    return svc, fake


def valid_judge_json(
    logical_coherence: int,
    evidence_grounding: int,
    plausibility: int,
    *,
    hallucination: bool = False,
    critique: str = "推理链尚可,证据引用基本到位",
) -> str:
    """生成过 schema 的 JudgeVerdict 响应。

    三项 int 是 ge=1 le=5——给 0 会意外触发自修复重试(用例 5 专用),
    正常用例务必给 1-5,否则 fake.calls 计数错位。集中在此管理。
    """
    payload = {
        "logical_coherence": logical_coherence,
        "evidence_grounding": evidence_grounding,
        "plausibility": plausibility,
        "hallucination_detected": hallucination,
        "critique": critique,
    }
    return json.dumps(payload, ensure_ascii=False)


REASONING_CHAIN = [
    "[E2] 贝叶斯后验最高的候选是 memory_leak",
    "[E1] 拓扑影响链与该假设的传播方向一致",
]

REASONING_STEPS = [
    {"step": 1, "description": "检测到内存持续增长", "action": "analyze"},
    {"step": 2, "description": "定位泄漏点并确认根因", "action": "restart"},
]


def make_rca_result(*, with_chain: bool = True) -> dict[str, Any]:
    """构造一条 rca_agent 产物(evidence 里带/不带推理链)。"""
    evidence: dict[str, Any] = {
        "bayesian_top_causes": [{"cause": "memory_leak", "posterior": 0.72}],
        "rag_matches": [{"id": "kb-1", "category": "memory"}],
        "impact_chain_detail": [{"service": "order-service", "hop": 0}],
        "evidence_used": ["bayesian", "topology"],
        "reasoning_mode": "llm_hybrid" if with_chain else "rule_only",
    }
    if with_chain:
        evidence["reasoning_chain"] = list(REASONING_CHAIN)
    return {
        "agent_name": "rca_agent",
        "output_data": {
            "rca_event": {
                "root_cause": "memory_leak",
                "confidence": 0.8,
                "impact_chain": ["order-service", "db-service"],
                "evidence": evidence,
            },
            "root_cause": "memory_leak",
            "confidence": 0.8,
            "impact_chain": ["order-service", "db-service"],
            "suggested_actions": ["restart"],
            "reasoning_steps": list(REASONING_STEPS),
        },
    }


GROUND_TRUTH: dict[str, Any] = {
    "root_cause": "memory_leak",
    "impact_chain": ["order-service", "db-service"],
    "reasoning_steps": list(REASONING_STEPS),
    "suggested_actions": ["restart"],
}


def make_reasoning_input(*, with_chain: bool = True) -> EvalInput:
    return EvalInput(
        eval_type=EvalType.REASONING,
        target_agent="rca_agent",
        ground_truth=dict(GROUND_TRUTH),
        agent_results=[make_rca_result(with_chain=with_chain)],
    )


def make_ctx() -> AgentExecutionContext:
    return AgentExecutionContext(incident_id="test-eval")


def get_reasoning(result: Any) -> dict[str, Any]:
    return result.output_data["report"]["reasoning"]


# =============================================================================
# 用例 1:judge 关闭 → 无 judge 字段、无 LLM 调用
# =============================================================================


@pytest.mark.asyncio
async def test_judge_disabled_no_call() -> None:
    svc, fake = make_eval_service([], enable_judge=False)
    agent = EvalAgent(llm_service=svc)

    result = await agent.process(make_reasoning_input(), make_ctx())

    assert result.success is True
    reasoning = get_reasoning(result)
    # judge 未启用:judge 字段为 None,合成分回归纯规则分(reasoning_chain_quality 原样)
    assert reasoning["judge"] is None
    assert reasoning["reasoning_chain_quality"] > 0  # 规则路径确实跑了
    assert fake.calls == []


# =============================================================================
# 用例 2:judge 成功 → 合成分逐位钉死、透传、temperature=0.0
# =============================================================================


@pytest.mark.asyncio
async def test_judge_success_fusion() -> None:
    # 不平凡数字:4/3/5(不给全 3),judge_score = 12/15 = 0.8
    svc, fake = make_eval_service(
        [make_fake_response(valid_judge_json(4, 3, 5, hallucination=True))],
        enable_judge=True,
    )
    agent = EvalAgent(llm_service=svc)

    result = await agent.process(make_reasoning_input(), make_ctx())

    assert result.success is True
    reasoning = get_reasoning(result)
    judge = reasoning["judge"]
    assert judge is not None

    # judge 真实到达传输层,且裁判 temperature 显式为 0.0(不依赖全局默认)
    assert len(fake.calls) == 1
    assert fake.calls[0]["temperature"] == 0.0

    # judge_score 与融合分逐位钉死
    assert judge["judge_score"] == round(12 / 15, 4)
    rule = reasoning["reasoning_chain_quality"]
    assert rule > 0  # 规则项非平凡,融合真的用到了两边
    assert judge["rule_reasoning_score"] == rule
    assert judge["fused_reasoning_score"] == round(0.6 * rule + 0.4 * (12 / 15), 4)

    # verdict 明细透传(含 critique 评语——JudgeVerdict 必填字段,单独钉死)
    assert judge["logical_coherence"] == 4
    assert judge["evidence_grounding"] == 3
    assert judge["plausibility"] == 5
    assert judge["hallucination_detected"] is True
    assert judge["critique"] == "推理链尚可,证据引用基本到位"
    assert "llm_meta" in judge

    # 纯增量:overall_score 与 judge-off 同输入逐位相等(fused 不回写总分,留 D7)。
    # 只断言"存在"杀不死"把 fused 折进 overall_score"的假想变异,故用对照组钉死值。
    off_svc, _ = make_eval_service([], enable_judge=False)
    off_result = await EvalAgent(llm_service=off_svc).process(
        make_reasoning_input(), make_ctx()
    )
    assert result.output_data["overall_score"] == off_result.output_data["overall_score"]


# =============================================================================
# 用例 3:judge 降级三条(传输错 + 超时 + 裸异常)→ 报告完整、无 judge 残留
# =============================================================================


@pytest.mark.asyncio
async def test_judge_degrade_transport_error() -> None:
    # 传输层抛异常 → 重试环耗尽 → LLMUnavailableError → 只跳过 judge
    svc, fake = make_eval_service(
        [RuntimeError("transport boom")], enable_judge=True, max_retries=0
    )
    agent = EvalAgent(llm_service=svc)

    result = await agent.process(make_reasoning_input(), make_ctx())

    assert result.success is True  # 绝不冒到外层兜底
    reasoning = get_reasoning(result)
    assert reasoning["judge"] is None
    assert reasoning["reasoning_chain_quality"] > 0  # 规则分原样保留


@pytest.mark.asyncio
async def test_judge_degrade_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(EvalAgent, "JUDGE_TIMEOUT_SECONDS", 0.05)
    hanging = HangingCompletions()
    svc, _ = make_eval_service([], enable_judge=True, transport=hanging)
    agent = EvalAgent(llm_service=svc)

    result = await agent.process(make_reasoning_input(), make_ctx())

    assert result.success is True
    reasoning = get_reasoning(result)
    assert reasoning["judge"] is None
    assert reasoning["reasoning_chain_quality"] > 0
    assert len(hanging.calls) == 1  # 请求真实到达后才挂起(证明走了 LLM 路径)


@pytest.mark.asyncio
async def test_judge_degrade_unexpected_exception() -> None:
    # 裸异常经接口边界 stub 触达 except Exception 兜底分支
    agent = EvalAgent(llm_service=ExplodingLLMService())  # type: ignore[arg-type]

    result = await agent.process(make_reasoning_input(), make_ctx())

    assert result.success is True
    reasoning = get_reasoning(result)
    assert reasoning["judge"] is None
    assert reasoning["reasoning_chain_quality"] > 0


# =============================================================================
# 用例 3b:缺推理链(rule_only 产物)→ 跳过 judge(缺链评分不可靠)
# =============================================================================


@pytest.mark.asyncio
async def test_judge_skipped_when_no_reasoning_chain() -> None:
    svc, fake = make_eval_service([], enable_judge=True)
    agent = EvalAgent(llm_service=svc)

    result = await agent.process(make_reasoning_input(with_chain=False), make_ctx())

    assert result.success is True
    reasoning = get_reasoning(result)
    assert reasoning["judge"] is None
    assert fake.calls == []  # 缺链直接跳过,不发起 LLM 调用


# =============================================================================
# 用例 4:开关翻转探针(注入路径 + 未注入路径换全新配置对象)
# =============================================================================


@pytest.mark.asyncio
async def test_switch_flip_injected() -> None:
    svc, fake = make_eval_service(
        [make_fake_response(valid_judge_json(3, 3, 4))], enable_judge=False
    )
    agent = EvalAgent(llm_service=svc)

    r1 = await agent.process(make_reasoning_input(), make_ctx())
    assert get_reasoning(r1)["judge"] is None
    assert fake.calls == []

    # 运行时翻开关:同一 agent 第二次 process
    svc._cfg.enable_judge = True
    r2 = await agent.process(make_reasoning_input(), make_ctx())
    assert get_reasoning(r2)["judge"] is not None
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_switch_flip_uninjected(monkeypatch: pytest.MonkeyPatch) -> None:
    # 未注入路径:monkeypatch eval_agent 命名空间的 get_config / get_llm_service
    svc, fake = make_eval_service(
        [make_fake_response(valid_judge_json(3, 3, 4))], enable_judge=True
    )
    monkeypatch.setattr(eval_agent_module, "get_llm_service", lambda: svc)

    off_cfg = SimpleNamespace(llm=SimpleNamespace(enable_judge=False))
    monkeypatch.setattr(eval_agent_module, "get_config", lambda: off_cfg)
    agent = EvalAgent()  # 未注入

    r1 = await agent.process(make_reasoning_input(), make_ctx())
    assert get_reasoning(r1)["judge"] is None
    assert fake.calls == []

    # reload_config() 就是整体换对象:换全新配置对象(不是原地改属性)
    on_cfg = SimpleNamespace(llm=SimpleNamespace(enable_judge=True))
    monkeypatch.setattr(eval_agent_module, "get_config", lambda: on_cfg)
    r2 = await agent.process(make_reasoning_input(), make_ctx())
    assert get_reasoning(r2)["judge"] is not None
    assert len(fake.calls) == 1


# =============================================================================
# 用例 5:schema 自修复(首响应违 ge=1 → attempts==2 终态成功)
# =============================================================================


@pytest.mark.asyncio
async def test_judge_schema_self_repair() -> None:
    bad = valid_judge_json(0, 3, 3)  # logical_coherence=0 违 ge=1
    good = valid_judge_json(4, 3, 3)
    svc, fake = make_eval_service(
        [make_fake_response(bad), make_fake_response(good)],
        enable_judge=True,
        max_retries=1,
    )
    agent = EvalAgent(llm_service=svc)

    result = await agent.process(make_reasoning_input(), make_ctx())

    reasoning = get_reasoning(result)
    assert reasoning["judge"] is not None
    assert reasoning["judge"]["llm_meta"]["attempts"] == 2  # 自修复了一次
    assert len(fake.calls) == 2
    assert reasoning["judge"]["logical_coherence"] == 4  # 终态用的是修正后的值


# =============================================================================
# 用例 6:rubric 输入完整性(judge 真的看到了证据包与被评推理链)
# =============================================================================


@pytest.mark.asyncio
async def test_judge_payload_contains_evidence_and_chain() -> None:
    svc, fake = make_eval_service(
        [make_fake_response(valid_judge_json(4, 4, 4))], enable_judge=True
    )
    agent = EvalAgent(llm_service=svc)

    await agent.process(make_reasoning_input(), make_ctx())

    user_payload = fake.calls[0]["messages"][1]["content"]
    parsed = json.loads(user_payload)
    assert "evidence_pack" in parsed and "rca_output" in parsed
    # 被评推理链与其 [E*] 引用真的进了 payload
    assert parsed["evidence_pack"]["reasoning_chain"] == REASONING_CHAIN
    assert "[E2]" in user_payload
    # 被评根因与证据分节
    assert parsed["rca_output"]["root_cause"] == "memory_leak"
    assert parsed["evidence_pack"]["bayesian_top_causes"]


# =============================================================================
# 用例 6b:多 RCA 产物仍只评 [0] —— 把不变量 8 从"结构显然"升级为"测试钉死"
# =============================================================================


@pytest.mark.asyncio
async def test_judge_called_once_with_multiple_rca_results() -> None:
    """喂 2 条 rca_agent 产物,judge 仍只调 1 次(评 rca_results[0])。"""
    svc, fake = make_eval_service(
        [make_fake_response(valid_judge_json(4, 4, 4))], enable_judge=True
    )
    agent = EvalAgent(llm_service=svc)

    eval_input = EvalInput(
        eval_type=EvalType.REASONING,
        target_agent="rca_agent",
        ground_truth=dict(GROUND_TRUTH),
        agent_results=[make_rca_result(), make_rca_result()],
    )
    result = await agent.process(eval_input, make_ctx())

    assert result.success is True
    assert len(fake.calls) == 1  # 结构上只取 [0],不随产物数放大


# =============================================================================
# 用例 7:端点级(既有 4 key 原样 + 增量产出;judge 开/关两态)
# =============================================================================


def test_endpoint_baseline_keys_preserved() -> None:
    """end_to_end 维度不触 judge,但端点须保留既有 4 key 并增量给出 report。"""
    client = TestClient(app)
    resp = client.post("/api/v1/evaluations/run?eval_type=end_to_end")

    assert resp.status_code == 202
    data = resp.json()
    # 既有 4 key 原样(守住 test_api.py 绿基线)
    assert data["status"] == "started"
    assert "eval_id" in data
    assert data["eval_type"] == "end_to_end"
    assert "message" in data
    # 增量产出
    assert data.get("execution") == "completed"
    assert "report" in data


def test_endpoint_empty_store_still_202(monkeypatch: pytest.MonkeyPatch) -> None:
    """空库跑 reasoning:process 因无数据降级为 failure,端点仍 202 + 既有 4 key,增量键优雅缺席。"""
    monkeypatch.setattr(routes_module._incident_service, "_incidents", {})
    client = TestClient(app)
    resp = client.post("/api/v1/evaluations/run?eval_type=reasoning")

    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "started"
    assert "eval_id" in data
    assert "report" not in data  # 降级:增量键优雅缺席,不 500


def test_endpoint_judge_on_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """先注入带推理链的 RCA 产物,再 POST /evaluations/run,judge 开/关两态。"""
    rca_event = RCAEvent(
        root_cause="memory_leak",
        confidence=0.8,
        impact_chain=["order-service", "db-service"],
        evidence={
            "reasoning_chain": list(REASONING_CHAIN),
            "bayesian_top_causes": [{"cause": "memory_leak", "posterior": 0.7}],
        },
        recommended_actions=["restart"],
    )
    monkeypatch.setattr(
        routes_module._incident_service,
        "_incidents",
        {"inc-1": SimpleNamespace(rca_event=rca_event)},
    )

    # judge on:monkeypatch eval_agent 命名空间(端点内 EvalAgent() 走未注入路径)
    svc, _ = make_eval_service(
        [make_fake_response(valid_judge_json(4, 3, 5))], enable_judge=True
    )
    monkeypatch.setattr(eval_agent_module, "get_llm_service", lambda: svc)
    on_cfg = SimpleNamespace(llm=SimpleNamespace(enable_judge=True))
    monkeypatch.setattr(eval_agent_module, "get_config", lambda: on_cfg)

    client = TestClient(app)
    resp = client.post("/api/v1/evaluations/run?eval_type=reasoning")
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "started"
    judge = data["report"]["reasoning"]["judge"]
    assert judge is not None
    assert judge["logical_coherence"] == 4

    # judge off
    off_cfg = SimpleNamespace(llm=SimpleNamespace(enable_judge=False))
    monkeypatch.setattr(eval_agent_module, "get_config", lambda: off_cfg)
    resp2 = client.post("/api/v1/evaluations/run?eval_type=reasoning")
    data2 = resp2.json()
    assert data2["report"]["reasoning"]["judge"] is None


# =============================================================================
# 用例 8:确定性(同一 fake 脚本跑两次,融合分逐位相等)
# =============================================================================


@pytest.mark.asyncio
async def test_judge_determinism() -> None:
    async def run_once() -> Any:
        svc, _ = make_eval_service(
            [make_fake_response(valid_judge_json(4, 3, 5))], enable_judge=True
        )
        agent = EvalAgent(llm_service=svc)
        return await agent.process(make_reasoning_input(), make_ctx())

    r1 = await run_once()
    r2 = await run_once()

    f1 = get_reasoning(r1)["judge"]["fused_reasoning_score"]
    f2 = get_reasoning(r2)["judge"]["fused_reasoning_score"]
    assert f1 == f2
