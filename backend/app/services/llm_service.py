"""
AIOps Agent Platform - LLM Service

统一 LLM 客户端:
- OpenAI / DeepSeek 兼容(OpenAI SDK + base_url 切换)
- structured_completion: pydantic schema 校验 + 错误反馈自修复重试
- 指数退避;耗尽后抛 LLMUnavailableError,调用方降级规则路径
- 每次调用: Langfuse generation 埋点 + Prometheus token/延迟指标
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Type, TypeVar

from prometheus_client import Counter, Histogram
from pydantic import BaseModel, ValidationError

from app.config import LLMConfig, get_config
from app.utils.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

PROVIDER_BASE_URLS: dict[str, str | None] = {
    "openai": None,                          # SDK 默认
    "deepseek": "https://api.deepseek.com",
    "azure-openai": None,                    # 走 base_url 显式配置
}

LLM_REQUESTS_TOTAL = Counter(
    "llm_requests_total", "LLM requests", ["call_name", "model", "status"],
)
LLM_TOKENS_TOTAL = Counter(
    "llm_tokens_total", "LLM tokens", ["model", "direction"],
)
LLM_LATENCY_SECONDS = Histogram(
    "llm_latency_seconds", "LLM call latency",
    buckets=(0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0),
)


class LLMUnavailableError(Exception):
    """LLM 不可用(未配置 / 网络失败 / 校验重试耗尽)。调用方必须降级规则路径。"""


def _strip_code_fence(text: str) -> str:
    """容错:剥掉模型偶尔包上的 ```json ... ``` 围栏。"""
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    return m.group(1) if m else text.strip()


class LLMService:
    """单例。所有 Agent/路由共用一个连接池。"""

    _instance: "LLMService | None" = None

    def __init__(self, config: LLMConfig | None = None) -> None:
        """
        Args:
            config: 显式注入的 LLM 配置;None 时读全局配置(生产路径)。
                    测试用它注入隔离配置,避免碰全局配置单例与 .env。
        """
        cfg = config or get_config().llm
        self._cfg = cfg
        self._client: Any = None
        if cfg.api_key:
            try:
                from openai import AsyncOpenAI

                base_url = cfg.base_url or PROVIDER_BASE_URLS.get(cfg.provider)
                self._client = AsyncOpenAI(
                    api_key=cfg.api_key,
                    base_url=base_url,
                    timeout=cfg.timeout_seconds,
                    max_retries=0,   # 重试自己做,便于退避与统计
                )
                logger.info("LLM client ready", provider=cfg.provider, model=cfg.model)
            except ImportError:
                logger.warning("openai SDK not installed; LLM disabled")
        else:
            logger.info("LLM_API_KEY empty; LLM disabled, rule-only mode")

    @classmethod
    def get_instance(cls) -> "LLMService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def available(self) -> bool:
        return self._client is not None

    # ------------------------------------------------------------------
    # 核心方法
    # ------------------------------------------------------------------
    async def structured_completion(
        self,
        *,
        system: str,
        user: str,
        schema: Type[T],
        call_name: str,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int = 1024,
        validate_extra: Any = None,   # 可选:候选集约束校验回调 (parsed) -> str | None
    ) -> tuple[T, dict[str, Any]]:
        """
        结构化补全。返回 (schema实例, usage元信息)。

        validate_extra: 额外业务校验,返回错误描述字符串则视为校验失败
                        (与 schema 校验同等对待:错误喂回模型重试)。
        失败语义:网络类错误按指数退避重试;校验类错误把错误信息追加进对话重试;
                 总次数耗尽抛 LLMUnavailableError。
        """
        if not self.available:
            raise LLMUnavailableError("LLM not configured")

        use_model = model or self._cfg.model
        schema_json = json.dumps(
            schema.model_json_schema(), ensure_ascii=False, indent=None
        )
        sys_prompt = (
            f"{system}\n\n"
            f"输出要求:只输出一个合法 JSON 对象(无 markdown、无解释文字),"
            f"且必须符合此 JSON Schema:\n{schema_json}"
        )
        messages: list[dict[str, str]] = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user},
        ]

        total_usage = {"input": 0, "output": 0, "total": 0}
        last_err: Exception | None = None
        attempts = self._cfg.max_retries + 1

        for attempt in range(attempts):
            try:
                t0 = time.monotonic()
                resp = await self._client.chat.completions.create(
                    model=use_model,
                    messages=messages,
                    temperature=(
                        temperature if temperature is not None else self._cfg.temperature
                    ),
                    max_tokens=max_tokens,
                    response_format={"type": "json_object"},
                )
                latency = time.monotonic() - t0
                LLM_LATENCY_SECONDS.observe(latency)

                raw = resp.choices[0].message.content or ""
                if resp.usage:
                    total_usage["input"] += resp.usage.prompt_tokens
                    total_usage["output"] += resp.usage.completion_tokens
                    total_usage["total"] += resp.usage.total_tokens
                    LLM_TOKENS_TOTAL.labels(use_model, "input").inc(resp.usage.prompt_tokens)
                    LLM_TOKENS_TOTAL.labels(use_model, "output").inc(resp.usage.completion_tokens)

                # --- schema 校验 + 业务校验,失败自修复 ---
                try:
                    parsed = schema.model_validate_json(_strip_code_fence(raw))
                    extra_err = validate_extra(parsed) if validate_extra else None
                    if extra_err:
                        raise ValueError(extra_err)
                except (ValidationError, ValueError) as ve:
                    last_err = ve
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({
                        "role": "user",
                        "content": (
                            f"你的输出未通过校验:{ve}\n"
                            f"请修正后重新只输出 JSON 对象。"
                        ),
                    })
                    LLM_REQUESTS_TOTAL.labels(call_name, use_model, "invalid_output").inc()
                    logger.warning(
                        "LLM output validation failed, self-repair retry",
                        call=call_name, attempt=attempt, error=str(ve)[:200],
                    )
                    continue

                LLM_REQUESTS_TOTAL.labels(call_name, use_model, "success").inc()
                meta = {
                    "usage": total_usage,
                    "model": use_model,
                    "latency_ms": round(latency * 1000, 1),
                    "attempts": attempt + 1,
                }
                self._log_langfuse(call_name, messages, raw, use_model, total_usage, meta)
                logger.info(
                    "LLM call ok", call=call_name, model=use_model,
                    tokens=total_usage["total"], latency_ms=meta["latency_ms"],
                    attempts=attempt + 1,
                )
                return parsed, meta

            except Exception as e:  # 网络/超时/限流(含 ValueError 子类,如非 ASCII key 的头编码错误)
                last_err = e
                LLM_REQUESTS_TOTAL.labels(call_name, use_model, "error").inc()
                logger.warning(
                    "LLM transport error, backoff retry",
                    call=call_name, attempt=attempt, error=str(e)[:200],
                )
                if attempt < attempts - 1:
                    await asyncio.sleep(min(2 ** attempt, 8))

        LLM_REQUESTS_TOTAL.labels(call_name, use_model, "exhausted").inc()
        raise LLMUnavailableError(
            f"{call_name} failed after {attempts} attempts: {last_err}"
        )

    # ------------------------------------------------------------------
    # Langfuse 埋点(独立 trace 模式;进阶模式见方案 §6)
    # ------------------------------------------------------------------
    def _log_langfuse(
        self,
        call_name: str,
        messages: list[dict[str, str]],
        completion: str,
        model: str,
        usage: dict[str, int],
        meta: dict[str, Any],
    ) -> None:
        try:
            from app.services.langfuse_service import get_langfuse_service

            lf = get_langfuse_service()
            if not lf.is_enabled():
                return
            # create_trace 返回 trace 对象(禁用/异常时为 _NoOpTrace),取 id 再挂 generation
            trace = lf.create_trace(
                name=f"llm.{call_name}",
                metadata={"latency_ms": meta.get("latency_ms"), "attempts": meta.get("attempts")},
            )
            trace_id = getattr(trace, "id", "")
            if trace_id:
                lf.log_generation_on_trace(
                    trace_id=trace_id,
                    prompt=messages,
                    completion=completion,
                    model=model,
                    usage=usage,
                )
        except Exception as e:  # 埋点绝不影响主流程
            logger.debug("langfuse logging skipped", error=str(e))


def get_llm_service() -> LLMService:
    return LLMService.get_instance()
