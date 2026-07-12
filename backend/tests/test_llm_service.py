"""
AIOps Agent Platform - LLM Service 单元测试(方案 §8 用例 1-3 + 加测 A/B + smoke)

测试原则(CLAUDE.md / 方案 §8):
- 不消耗真实 LLM API:用脚本化 FakeCompletions 在传输层伪造响应,
  schema 校验 / 自修复重试 / 指数退避 / 降级等业务逻辑全部真实执行,
  不用 mock 注入业务结果。
- 唯一例外 test_real_llm_smoke:环境变量 LLM_API_KEY 未导出时自动跳过。
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, Field

from app.config import LLMConfig
from app.services.llm_service import LLMService, LLMUnavailableError


class DemoAnalysis(BaseModel):
    """测试本地最小 schema(不依赖后续阶段的业务模型)"""

    root_cause: str
    confidence: float = Field(ge=0.0, le=1.0)


# 闭集候选(用例 2 的约束来源)
CANDIDATES = ["resource_exhaustion", "network_partition"]


# =============================================================================
# 测试基建:脚本化假 LLM 传输层
# =============================================================================


class FakeCompletions:
    """按脚本依次返回预设响应/抛异常,并记录每次请求参数。

    messages 在记录时做快照拷贝:llm_service 复用同一 list 对象做自修复追加,
    不拷贝的话各次 calls 会互相混叠,无法断言"第 N 次请求当时带了什么"。
    """

    def __init__(self, scripted: list[Any]) -> None:
        self.scripted = list(scripted)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        recorded = dict(kwargs)
        if "messages" in recorded:
            recorded["messages"] = [dict(m) for m in recorded["messages"]]
        self.calls.append(recorded)
        item = self.scripted.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def make_fake_response(
    content: str, *, prompt_tokens: int = 100, completion_tokens: int = 20
) -> SimpleNamespace:
    """构造与 openai ChatCompletion 响应同构的对象(choices/usage)"""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


def make_service(
    scripted: list[Any], *, max_retries: int = 2
) -> tuple[LLMService, FakeCompletions]:
    """构造注入 fake 传输层的 LLMService。

    配置走 __init__ 依赖注入(_env_file=None 不读 .env,不碰全局配置单例),
    仅替换 _client 为假传输层,其余逻辑真实执行。
    """
    svc = LLMService(
        config=LLMConfig(
            _env_file=None,
            api_key="test-key",
            model="test-model",
            temperature=0.0,
            timeout_seconds=5,
            max_retries=max_retries,
        )
    )
    fake = FakeCompletions(scripted)
    svc._client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    return svc, fake


# =============================================================================
# 用例 1:坏 JSON → 校验错误喂回模型 → 自修复重试成功
# =============================================================================


@pytest.mark.asyncio
async def test_structured_self_repair() -> None:
    bad = '{"root_cause": "resource_exhaustion", "confidence": "not-a-number"}'
    good = '{"root_cause": "resource_exhaustion", "confidence": 0.8}'
    svc, fake = make_service([make_fake_response(bad), make_fake_response(good)])

    parsed, meta = await svc.structured_completion(
        system="你是根因分析器",
        user="分析证据",
        schema=DemoAnalysis,
        call_name="self_repair_test",
    )

    # 最终拿到修复后的合法结果
    assert parsed.root_cause == "resource_exhaustion"
    assert parsed.confidence == 0.8
    assert meta["attempts"] == 2
    assert meta["model"] == "test-model"
    assert len(fake.calls) == 2

    # 自修复真实发生的证据:第一次请求只有 system+user 两条消息,
    # 第二次请求变为四条——多出 assistant(原始坏输出)+ user(校验错误反馈)
    assert len(fake.calls[0]["messages"]) == 2
    second_messages = fake.calls[1]["messages"]
    assert len(second_messages) == 4
    assert second_messages[2] == {"role": "assistant", "content": bad}
    assert second_messages[3]["role"] == "user"
    assert "未通过校验" in second_messages[3]["content"]

    # JSON mode + schema 注入:请求带 response_format,system prompt 含 schema
    assert fake.calls[0]["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in fake.calls[0]["messages"][0]["content"]
    assert "root_cause" in fake.calls[0]["messages"][0]["content"]

    # usage 跨两次尝试累计:2 × (100 输入 + 20 输出)
    assert meta["usage"] == {"input": 200, "output": 40, "total": 240}


# =============================================================================
# 用例 2:闭集约束持续违规 → 自修复重试耗尽 → LLMUnavailableError
# =============================================================================


@pytest.mark.asyncio
async def test_closed_set_violation_exhausts() -> None:
    out_of_set = '{"root_cause": "alien_invasion", "confidence": 0.9}'
    svc, fake = make_service(
        [make_fake_response(out_of_set) for _ in range(3)], max_retries=2
    )

    def check_closed_set(parsed: DemoAnalysis) -> str | None:
        if parsed.root_cause not in CANDIDATES:
            return f"root_cause '{parsed.root_cause}' 不在候选列表 {CANDIDATES} 中"
        return None

    with pytest.raises(LLMUnavailableError, match="3 attempts"):
        await svc.structured_completion(
            system="你是根因分析器",
            user="分析证据",
            schema=DemoAnalysis,
            call_name="closed_set_test",
            validate_extra=check_closed_set,
        )

    # 共尝试 max_retries+1=3 次,且第 2/3 次请求都带上了闭集违规反馈
    assert len(fake.calls) == 3
    for call in fake.calls[1:]:
        assert call["messages"][-1]["role"] == "user"
        assert "不在候选列表" in call["messages"][-1]["content"]


# =============================================================================
# 用例 3:传输错误指数退避重试耗尽 → LLMUnavailableError
# =============================================================================


@pytest.mark.asyncio
async def test_transport_error_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    svc, fake = make_service(
        [ConnectionError("boom-1"), ConnectionError("boom-2"), ConnectionError("boom-3")],
        max_retries=2,
    )

    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with pytest.raises(LLMUnavailableError, match="3 attempts: boom-3"):
        await svc.structured_completion(
            system="s",
            user="u",
            schema=DemoAnalysis,
            call_name="transport_test",
        )

    assert len(fake.calls) == 3
    # 指数退避:2^0=1s、2^1=2s;最后一次尝试失败后直接抛错,不再退避
    assert sleep_calls == [1, 2]


# =============================================================================
# 回归:传输层抛 ValueError 子类(UnicodeEncodeError)必须退避重试并降级
# =============================================================================


def _real_unicode_encode_error() -> UnicodeEncodeError:
    """对含中文的假 key 真实触发 encode 失败,保留 ValueError 继承链事故现场"""
    try:
        "Bearer sk-你的key".encode("ascii")
    except UnicodeEncodeError as e:
        return e
    raise AssertionError("expected UnicodeEncodeError")


@pytest.mark.asyncio
async def test_transport_valueerror_subclass_backoff_and_degrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """事故回归(2026-07-12 smoke):非 ASCII api_key 令 httpx 头编码抛
    UnicodeEncodeError(ValueError 子类),曾被"不应到达"的防御分支裸抛,
    绕过退避重试与 LLMUnavailableError 降级契约。
    """
    errs = [_real_unicode_encode_error() for _ in range(3)]
    assert all(isinstance(e, ValueError) for e in errs)  # 事故根源:继承链

    svc, fake = make_service(errs, max_retries=2)

    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    with pytest.raises(LLMUnavailableError, match="3 attempts"):
        await svc.structured_completion(
            system="s",
            user="u",
            schema=DemoAnalysis,
            call_name="unicode_key_test",
        )

    # 三次真实尝试 + 两次指数退避:杀死"吞掉异常不重试"的错误实现
    assert len(fake.calls) == 3
    assert sleep_calls == [1, 2]


# =============================================================================
# 加测 A:新增配置字段默认值——三开关默认全关(CLAUDE.md 红线)
# =============================================================================


def test_llm_config_new_field_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "LLM_BASE_URL",
        "LLM_NLU_MODEL",
        "LLM_JUDGE_MODEL",
        "LLM_ENABLE_RCA",
        "LLM_ENABLE_NLU",
        "LLM_ENABLE_JUDGE",
        "LLM_NLU_FAST_PATH_CONFIDENCE",
    ):
        monkeypatch.delenv(var, raising=False)

    cfg = LLMConfig(_env_file=None)

    assert cfg.enable_rca is False
    assert cfg.enable_nlu is False
    assert cfg.enable_judge is False
    assert cfg.base_url == ""
    assert cfg.nlu_model == ""
    assert cfg.judge_model == ""
    assert cfg.nlu_fast_path_confidence == 0.6


# =============================================================================
# 加测 B:未配置 api_key → 立即抛 LLMUnavailableError(降级地基语义)
# =============================================================================


@pytest.mark.asyncio
async def test_not_configured_raises_immediately() -> None:
    svc = LLMService(config=LLMConfig(_env_file=None, api_key=""))

    assert svc.available is False
    with pytest.raises(LLMUnavailableError):
        await svc.structured_completion(
            system="s",
            user="u",
            schema=DemoAnalysis,
            call_name="unconfigured_test",
        )


# =============================================================================
# 用例 7:真实 smoke(唯一真实调用;无 key 自动跳过,本地带 key 手动跑)
# =============================================================================


@pytest.mark.skipif(not os.getenv("LLM_API_KEY"), reason="no api key")
@pytest.mark.asyncio
async def test_real_llm_smoke() -> None:
    class SmokeEcho(BaseModel):
        answer: str

    # 走默认配置源(环境变量 + backend/.env),与真实运行时一致
    svc = LLMService(config=LLMConfig())
    assert svc.available

    parsed, meta = await svc.structured_completion(
        system="你是回声服务,把用户要求的内容放进 answer 字段返回。",
        user='请返回 JSON:{"answer": "pong"}',
        schema=SmokeEcho,
        call_name="smoke",
        max_tokens=50,
    )

    assert parsed.answer
    assert meta["usage"]["total"] > 0
    assert meta["attempts"] >= 1
