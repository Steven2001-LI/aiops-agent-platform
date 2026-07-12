# LLM 集成方案 —— 从"纯规则引擎"到"LLM 推理 + 规则兜底"混合架构

> 适用仓库:`aiops-agent-platform` · 撰写日期:2026-07-12
> 前提结论(已验证):当前 backend 全部代码无一次真实 LLM 调用;`LLMConfig` 已存在但无人消费;LangGraph 图因 GraphState 无类型注解实际走 sequential fallback(本方案不处理,属第 2 步)。

---

## 0. 目标与设计原则

**目标**:让"多智能体"名副其实——RCA 用 LLM 做证据融合推理,NLU 用 LLM 兜底大白话理解,评测加一路 LLM-as-judge。同时把 AI 应用岗面试必考的工程点全部做实:**结构化输出、校验重试、幻觉约束、降级、trace、成本核算**。

四条设计原则(每一条都是面试可讲的决策):

1. **LLM 增强而非替换**。现有规则路径全部保留,降级为 fallback。API Key 缺失、LLM 超时、输出校验连续失败时,系统行为与今天完全一致。卖点:"LLM 故障不影响系统可用性"。
2. **所有 LLM 输出必须结构化**。pydantic schema 校验,校验失败把错误信息喂回模型自修复重试(self-repair),重试耗尽即降级,任何时候不让自由文本流入下游。
3. **候选集约束防幻觉**。LLM 只能从系统给出的候选根因/已知服务/标准症状枚举中选择,出圈视为校验失败。卖点:"闭集约束 + 开放推理链"。
4. **成本分层**。规则是零成本快路径,LLM 只在低置信度时介入;每次调用记录 token 与延迟(Langfuse + Prometheus),成本可观测、可报告。

**功能开关全部默认关闭**(`LLM_ENABLE_RCA` / `LLM_ENABLE_NLU` / `LLM_ENABLE_JUDGE` 默认 `false`),合入即零风险,逐个打开逐个验收。

---

## 1. 总体架构

```
                       ┌──────────────────────────────────────────┐
                       │   app/services/llm_service.py  (新增)     │
                       │   AsyncOpenAI(openai/deepseek 兼容)       │
                       │   structured_completion():               │
                       │     schema 注入 → json mode → 校验        │
                       │     → 失败自修复重试 → 耗尽抛
                       │     LLMUnavailableError                  │
                       │   内置: Langfuse generation 埋点          │
                       │         Prometheus token/延迟指标          │
                       └────────┬──────────┬──────────┬───────────┘
                                │          │          │
              ┌─────────────────┘          │          └─────────────────┐
              ▼                            ▼                            ▼
 ┌─────────────────────────┐  ┌─────────────────────────┐  ┌─────────────────────────┐
 │ B. RCA Agent            │  │ C. NLU 混合层            │  │ D. Eval LLM-as-Judge    │
 │ rca_agent.py            │  │ nlu/hybrid.py (新增)     │  │ eval_agent.py           │
 │ 四路证据打包 →LLM 融合    │  │ 正则快路径(零成本)        │  │ 推理链质量评分(1-5 rubric)│
 │ 推理链+候选约束           │  │ 低置信 → LLM 慢路径       │  │ 与规则指标加权合成         │
 │ 失败→规则综合(现逻辑)     │  │ 失败→现有正则结果         │  │ 失败→纯规则评分(现逻辑)   │
 └─────────────────────────┘  └─────────────────────────┘  └─────────────────────────┘
```

改动面(共 2 个新文件 + 5 处修改):

| # | 文件 | 动作 |
|---|------|------|
| 1 | `app/services/llm_service.py` | **新增**,统一 LLM 客户端 |
| 2 | `app/nlu/hybrid.py` | **新增**,快慢路径 NLU |
| 3 | `app/config.py` | `LLMConfig` 加 7 个字段 |
| 4 | `app/agents/rca_agent.py` | `process()` 加 LLM 融合步骤,原 `_synthesize_analysis` 改名为 `_rule_based_synthesis` 留作兜底 |
| 5 | `app/api/routes.py` | `/incidents/diagnose` 端点换用 `hybrid_understand()` |
| 6 | `app/agents/eval_agent.py` | `process()` 里追加 judge 一路 |
| 7 | `requirements.txt` / `.env.example` | 加 `openai>=1.30.0` 与新环境变量 |

技术选型说明(面试必被问"为什么不用 LangChain 的 ChatOpenAI"):**直接用 OpenAI SDK**。理由:DeepSeek/OpenAI 同协议,一个 `base_url` 切换;重试、退避、校验、埋点全部自己控制,不隐藏在框架回调里;依赖面小,便于测量成本和写单测。LangChain 系列保留给 LangGraph 编排用,不冲突。

---

## 2. 基础设施:`app/services/llm_service.py`(完整代码)

```python
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
import uuid
from typing import Any, Type, TypeVar

from prometheus_client import Counter, Histogram
from pydantic import BaseModel, ValidationError

from app.config import get_config
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

    def __init__(self) -> None:
        cfg = get_config().llm
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

            except (ValidationError, ValueError):
                raise  # 不应到达,防御
            except Exception as e:  # 网络/超时/限流
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
            from app.services.langfuse_service import LangfuseService

            lf = LangfuseService()
            if not lf.is_enabled():
                return
            trace_id = lf.create_trace(
                name=f"llm.{call_name}",
                metadata={"latency_ms": meta.get("latency_ms"), "attempts": meta.get("attempts")},
            )
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
```

> 实现备注
> ① `response_format={"type":"json_object"}`:OpenAI 与 DeepSeek(`deepseek-chat`)都支持;JSON mode 要求 prompt 中出现 "JSON" 字样,system 模板里已保证。
> ② `max_retries=0` 是把 SDK 内建重试关掉,退避与统计自己做——面试可讲"为什么关掉 SDK 重试"。
> ③ `create_trace` 的返回值形态以你 `langfuse_service.py` 的实际签名为准(它返回 trace_id 或对象,按现有实现微调这两行)。
> ④ Prometheus 指标是模块级注册,与 `main.py` 现有写法一致;pytest 多次 import 不会重复注册(同一进程只 import 一次)。

---

## 3. 配置与依赖变更

### 3.1 `app/config.py` — `LLMConfig` 追加字段(第 16-32 行的类里加)

```python
class LLMConfig(BaseSettings):
    # ... 现有 7 个字段保持不变 ...

    base_url: str = Field(default="", description="自定义 API base_url,优先于 provider 预设")

    # 分模块模型选择:NLU/Judge 可用更便宜的模型,空 = 用主模型
    nlu_model: str = Field(default="", description="NLU 慢路径模型")
    judge_model: str = Field(default="", description="LLM-as-judge 模型")

    # 功能开关:默认全关,合入零风险,逐个打开验收
    enable_rca: bool = Field(default=False, description="RCA LLM 融合推理")
    enable_nlu: bool = Field(default=False, description="NLU LLM 慢路径")
    enable_judge: bool = Field(default=False, description="评测 LLM-as-judge")

    # NLU 快慢路径切换阈值
    nlu_fast_path_confidence: float = Field(default=0.6, ge=0.0, le=1.0)
```

### 3.2 `.env.example` 追加

```bash
# ============ LLM(建议 DeepSeek,便宜且国内直连)============
LLM_PROVIDER=deepseek
LLM_API_KEY=sk-your-key
LLM_MODEL=deepseek-chat
LLM_ENABLE_RCA=true
LLM_ENABLE_NLU=true
LLM_ENABLE_JUDGE=false
# 可选:NLU/Judge 单独用便宜模型
# LLM_NLU_MODEL=deepseek-chat
# LLM_JUDGE_MODEL=deepseek-chat
```

### 3.3 `requirements.txt` 追加

```
openai>=1.30.0
```

(langchain 系列保留;`sentence-transformers/torch` 与本方案无关,不动。)

---

## 4. 集成点 B:RCA Agent —— LLM 证据融合推理

### 4.1 现状与改造思路

`rca_agent.py` 的 `process()`(第 174-275 行)已经产出四路证据:BFS 影响链(Step 1)、贝叶斯后验 Top-N(Step 2)、静态知识库 RAG 匹配(Step 3)、ChromaDB 历史记忆(Step 3a),然后由 `_synthesize_analysis`(第 668 行)用固定权重公式合成。

改造:**四路证据 + 规则引擎初步结论一起打包成"证据包",交给 LLM 生成根因假设、推理链、备选假设;规则合成逻辑原样保留,改名 `_rule_based_synthesis`,承担两个角色——LLM 的一路输入 + 降级兜底。**

```
Step 1-3a(不动) → Step 4a: _rule_based_synthesis(原逻辑,改名)
                → Step 4b: enable_rca 且 LLM 可用?
                            ├─ 是 → _llm_synthesize(证据包) ──成功──> 采用 LLM 结论(带一致性校准)
                            │            └─ LLMUnavailableError → 用 4a 结果,evidence 标记 rule_fallback
                            └─ 否 → 用 4a 结果
```

### 4.2 输出 Schema(加在 `rca_agent.py` 顶部 model 区)

```python
class LLMRootCauseAnalysis(BaseModel):
    """LLM 根因融合推理输出(闭集约束在运行时校验)"""
    root_cause: str = Field(description="根因,必须从候选列表中选择")
    confidence: float = Field(ge=0.0, le=1.0, description="置信度")
    reasoning_chain: list[str] = Field(
        min_length=2, max_length=8,
        description="推理链,每步一句话,须引用证据编号如 [E1]",
    )
    evidence_used: list[str] = Field(
        description="使用的证据路:topology/bayesian/rag/memory/rules 的子集",
    )
    alternative_hypothesis: str = Field(default="", description="次优假设(候选列表内)")
    alternative_rejected_because: str = Field(default="", description="排除次优假设的理由")
    suggested_actions: list[str] = Field(default_factory=list, max_length=6)
    needs_human_review: bool = Field(default=False, description="证据矛盾/置信度不足时置 true")
```

### 4.3 Prompt 与调用(新增方法 `_llm_synthesize`)

```python
RCA_SYSTEM_PROMPT = """你是资深 SRE 根因分析专家。你将收到一次线上故障的多路诊断证据,\
请融合所有证据推理出最可能的根因。

规则:
1. root_cause 只能从"候选根因"列表中选择,禁止编造列表外的根因。
2. reasoning_chain 每一步必须引用证据编号([E1]、[E2]...),不得使用证据中不存在的事实。
3. 各路证据可能互相矛盾:贝叶斯先验反映统计规律,历史记忆反映本系统真实发生过的案例,\
拓扑决定传播方向。矛盾时优先解释矛盾,而不是忽略。
4. 若最高候选与次优候选证据强度接近,如实降低 confidence 并给出 alternative_hypothesis。
5. 证据不足以定位时,needs_human_review 置 true。"""


async def _llm_synthesize(
    self,
    alert: AlertEvent,
    symptoms: list[str],
    impact_chain: list[ServiceImpact],
    bayesian_results: list[BayesianNode],
    rag_results: list[dict[str, Any]],
    memory_results: list[dict[str, Any]],
    rule_result: tuple[str, float, dict[str, Any]],
) -> tuple[LLMRootCauseAnalysis, dict[str, Any]]:
    """LLM 证据融合。抛 LLMUnavailableError 由调用方降级。"""
    from app.services.llm_service import get_llm_service

    rule_cause, rule_conf, _ = rule_result

    # 闭集候选:贝叶斯全部先验类别 + RAG 命中条目的 root_causes
    candidates = list(self.PRIOR_PROBABILITIES.keys())
    for m in rag_results[:3]:
        for c in m.get("root_causes", []):
            if c not in candidates:
                candidates.append(c)

    evidence_pack = {
        "alert": {
            "service": alert.service, "metric": alert.metric,
            "value": alert.value, "threshold": alert.threshold,
            "severity": alert.severity.value, "symptoms": symptoms,
        },
        "E1_topology_impact": [
            {"service": i.service, "hop": i.hop_distance,
             "tier": i.tier, "level": i.impact_level}
            for i in impact_chain[:10]
        ],
        "E2_bayesian_top5": [
            {"cause": r.name, "posterior": round(r.posterior, 4),
             "likelihood": round(r.likelihood, 3)}
            for r in bayesian_results[:5]
        ],
        "E3_knowledge_base_matches": [
            {"id": m["id"], "category": m.get("category"),
             "match_score": m.get("match_score"),
             "root_causes": m.get("root_causes", []),
             "solutions": m.get("solutions", [])[:3]}
            for m in rag_results[:3]
        ],
        "E4_historical_memory": [
            {"incident": m.get("source_incident"),
             "summary": (m.get("summary") or m.get("content", ""))[:200],
             "similarity": m.get("semantic_similarity")}
            for m in memory_results[:3]
        ],
        "E5_rule_engine_preliminary": {
            "root_cause": rule_cause, "confidence": rule_conf,
        },
        "candidate_root_causes": candidates,
    }

    def _check_closed_set(parsed: LLMRootCauseAnalysis) -> str | None:
        if parsed.root_cause not in candidates:
            return (
                f"root_cause '{parsed.root_cause}' 不在候选列表中,"
                f"必须从 {candidates} 中选择"
            )
        if parsed.alternative_hypothesis and parsed.alternative_hypothesis not in candidates:
            return "alternative_hypothesis 不在候选列表中"
        return None

    llm = get_llm_service()
    return await llm.structured_completion(
        system=RCA_SYSTEM_PROMPT,
        user=json.dumps(evidence_pack, ensure_ascii=False),
        schema=LLMRootCauseAnalysis,
        call_name="rca_synthesize",
        max_tokens=1200,
        validate_extra=_check_closed_set,
    )
```

### 4.4 `process()` 的 Step 4 改写(替换第 213-217 行附近)

```python
# Step 4a: 规则综合(原 _synthesize_analysis,改名后逻辑不动)
rule_result = self._rule_based_synthesis(
    bayesian_results, rag_results, impact_chain, alert,
    memory_results=memory_results,
)
root_cause, confidence, evidence = rule_result
reasoning_mode = "rule_only"
llm_analysis: LLMRootCauseAnalysis | None = None

# Step 4b: LLM 融合(开关开启且可用时)
llm_cfg = get_config().llm
if llm_cfg.enable_rca:
    try:
        llm_analysis, llm_meta = await self._llm_synthesize(
            alert, symptoms, impact_chain,
            bayesian_results, rag_results, memory_results, rule_result,
        )
        # 一致性校准:LLM 与规则引擎结论一致 → 置信度取高值并小幅增强;
        # 不一致 → 采用 LLM 结论但按分歧打折,并完整记录分歧供审计
        if llm_analysis.root_cause == root_cause:
            confidence = min(0.98, max(confidence, llm_analysis.confidence) + 0.05)
        else:
            confidence = round(llm_analysis.confidence * 0.85, 4)
            evidence["rule_llm_disagreement"] = {
                "rule": root_cause, "llm": llm_analysis.root_cause,
            }
        root_cause = llm_analysis.root_cause
        reasoning_mode = "llm_hybrid"
        evidence.update({
            "reasoning_chain": llm_analysis.reasoning_chain,
            "evidence_used": llm_analysis.evidence_used,
            "alternative_hypothesis": llm_analysis.alternative_hypothesis,
            "alternative_rejected_because": llm_analysis.alternative_rejected_because,
            "needs_human_review": llm_analysis.needs_human_review,
            "llm_meta": llm_meta,   # tokens/latency/attempts,评测和成本报告用
        })
    except LLMUnavailableError as e:
        reasoning_mode = "rule_fallback"
        logger.warning("RCA LLM unavailable, rule fallback", error=str(e)[:200])

evidence["reasoning_mode"] = reasoning_mode
```

其余不动:`RCAEvent` 模型不需要改字段(推理链放进 `evidence` dict);`suggested_actions` 生成逻辑可在 `llm_analysis.suggested_actions` 非空时合并去重。

**设计讲点(面试话术)**:一致性校准是"用便宜的规则引擎给 LLM 结论做交叉验证";分歧不隐藏而是写入 evidence 全程可审计;闭集约束把幻觉从"生成问题"降维成"校验问题"。

---

## 5. 集成点 C:NLU —— 快慢路径混合理解

### 5.1 思路

现有 `IntentClassifier`(正则+关键词加权)与 `EntityExtractor`(同义词字典)保留为**零成本快路径**。只有当快路径"没把握"时才升级 LLM:

```
query ─→ 正则意图分类 + 字典实体抽取(现有代码,不动)
           │
           ├─ 有把握(见判据)──────────────→ 直接返回,nlu_path = "fast_rule"
           │
           └─ 没把握 且 enable_nlu ─→ LLM 结构化抽取 ─成功→ nlu_path = "llm_enhanced"
                                          └─ LLMUnavailableError → 返回快路径结果
                                                nlu_path = "rule_fallback"
```

**判据**(没把握 = 任一成立):`intent.confidence < nlu_fast_path_confidence(默认0.6)`;意图为 `general_question`(兜底类,说明正则没匹配上);`fault_diagnosis / heal_request / business_check` 意图但 services 与 symptoms 双空(下游没法构造告警)。

### 5.2 新文件 `app/nlu/hybrid.py`(完整代码)

```python
"""
AIOps NLU 混合层:正则快路径 + LLM 慢路径。

返回类型与现有 (UserIntent, AIOpsEntities) 完全一致,下游零改动。
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from app.config import get_config
from app.nlu.entity_extractor import AIOpsEntities, EntityExtractor
from app.nlu.intent_classifier import IntentClassifier, IntentType, UserIntent
from app.services.llm_service import LLMUnavailableError, get_llm_service
from app.utils.logging import get_logger

logger = get_logger(__name__)

_classifier = IntentClassifier()
_extractor = EntityExtractor()

# 闭集:标准症状枚举(取自 EntityExtractor 同义词表的值域)
KNOWN_SYMPTOMS = sorted(set(EntityExtractor.SYMPTOM_SYNONYMS.values()))


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


def _fast_path_confident(intent: UserIntent, entities: AIOpsEntities) -> bool:
    """快路径是否有把握(判据见方案 §5.1)。"""
    threshold = get_config().llm.nlu_fast_path_confidence
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
) -> tuple[UserIntent, AIOpsEntities, dict]:
    """
    混合 NLU 主入口。

    Returns:
        (intent, entities, nlu_info)
        nlu_info = {"path": "fast_rule"|"llm_enhanced"|"rule_fallback",
                    "clarification": str, "llm_meta": {...}}
    """
    intent = _classifier.classify(query)
    entities = _extractor.extract(query)
    info: dict = {"path": "fast_rule", "clarification": ""}

    cfg = get_config().llm
    if _fast_path_confident(intent, entities) or not cfg.enable_nlu:
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

    try:
        llm = get_llm_service()
        result, meta = await llm.structured_completion(
            system=NLU_SYSTEM_PROMPT,
            user=json.dumps({
                "user_query": query,
                "已知服务": known_services,
                "标准症状": KNOWN_SYMPTOMS,
                "规则快路径初步结果": {
                    "intent": intent.intent.value,
                    "confidence": round(intent.confidence, 3),
                    "services": entities.services,
                    "symptoms": entities.symptoms,
                },
            }, ensure_ascii=False),
            schema=LLMNLUResult,
            call_name="nlu_understand",
            model=cfg.nlu_model or None,
            max_tokens=500,
            validate_extra=_check_closed_set,
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
    except LLMUnavailableError as e:
        info["path"] = "rule_fallback"
        logger.warning("NLU LLM unavailable, using rule result", error=str(e)[:200])

    return intent, entities, info
```

### 5.3 `routes.py` 端点改动(第 1418-1433 行)

```python
# 原来的三行分类/抽取替换为:
from app.data.knowledge_base import SERVICE_TOPOLOGY
from app.nlu.hybrid import hybrid_understand

intent, entities, nlu_info = await hybrid_understand(
    query, known_services=list(SERVICE_TOPOLOGY.keys()),
)
```

响应体加两个字段(放在 `"intent"` 旁):

```python
"nlu_path": nlu_info["path"],
"clarification_question": nlu_info.get("clarification", ""),
```

`clarification_question` 非空时,前端可以直接把反问展示给用户——这就是简历里"反问补全"故事的 LLM 版:**一次问全所有缺失项,而不是多轮挤牙膏**,补全轮次的下降从此有真实机制支撑。

**量化点**:`nlu_path` 落在每个响应里,跑一遍评测集就能统计"x% 请求走零成本快路径、y% 升级 LLM"——这是简历上可写、面试可复现的真数字。

---

## 6. 集成点 D:评测 —— LLM-as-Judge(P1,前三项跑通后再做)

### 6.1 Schema 与 rubric

```python
class JudgeVerdict(BaseModel):
    """LLM 裁判对一次 RCA 推理的质量评分(1-5)"""
    logical_coherence: int = Field(ge=1, le=5, description="推理链逻辑连贯性")
    evidence_grounding: int = Field(ge=1, le=5, description="结论是否被引用的证据支撑")
    plausibility: int = Field(ge=1, le=5, description="根因在运维语义上的合理性")
    hallucination_detected: bool = Field(description="推理中是否使用了证据外的事实")
    critique: str = Field(description="一句话评语,指出最主要缺陷或亮点")


JUDGE_SYSTEM_PROMPT = """你是严格的 AIOps 评测裁判。给你一次故障的:证据包、系统输出的根因和推理链。\
请按 rubric 独立打分,不要偏向系统结论。

rubric:
- logical_coherence:推理步骤间是否成立,有无跳跃/循环论证。
- evidence_grounding:每步引用的证据编号是否真实存在且被正确使用;引用了不存在的证据 → 1 分并置 hallucination_detected=true。
- plausibility:结论是否符合分布式系统故障传播常识。
打分要拉开区分度:平庸=3,明显缺陷≤2,无可挑剔才是 5。"""
```

### 6.2 接入 `eval_agent.py`

`_eval_reasoning`(第 490 行)保持纯规则不动。在 `process()` 里 reasoning 维度算完后追加:

```python
if get_config().llm.enable_judge:
    try:
        verdict, meta = await llm.structured_completion(
            system=JUDGE_SYSTEM_PROMPT,
            user=json.dumps({"evidence_pack": ..., "rca_output": ...}, ensure_ascii=False),
            schema=JudgeVerdict,
            call_name="eval_judge",
            model=cfg.judge_model or None,
            temperature=0.0,           # 裁判必须确定性
            max_tokens=400,
        )
        judge_score = (
            verdict.logical_coherence + verdict.evidence_grounding + verdict.plausibility
        ) / 15.0
        # 合成:规则指标 0.6 + judge 0.4;judge 不可用时权重自动回归规则
        report.reasoning_metrics.overall = round(
            0.6 * rule_reasoning_score + 0.4 * judge_score, 4
        )
        report.reasoning_metrics.judge = verdict.model_dump()
    except LLMUnavailableError:
        pass   # 纯规则分数原样保留
```

两个防偏置细节(面试讲点):**judge 用独立模型配置**(`LLM_JUDGE_MODEL` 可指向不同于 RCA 的模型,避免"自己给自己打分"的同源偏置);**temperature=0** 保证评测可复现。

---

## 7. Langfuse 埋点位置汇总

`llm_service` 内置埋点后,所有调用自动带 trace,无需在各 Agent 里手写:

| 调用点 | trace 名 | 记录内容 |
|--------|----------|----------|
| RCA 融合 | `llm.rca_synthesize` | 证据包 prompt、推理链输出、tokens、latency、attempts |
| NLU 慢路径 | `llm.nlu_understand` | 原始 query + 快路径初判、结构化理解结果、tokens |
| 评测裁判 | `llm.eval_judge` | 证据+被评输出、评分与评语、tokens |

进阶(可选,改造完基础版之后):在 `/incidents/diagnose` 入口用 `LangfuseService.create_trace(name="incident_diagnosis")` 建**一条贯穿 NLU→RCA→Heal→Eval 的主 trace**,把 `trace_id` 放进 `AgentExecutionContext.metadata` 逐层传递,`llm_service.structured_completion` 加可选参数 `trace_id`,有则 `log_generation_on_trace` 挂到主 trace,无则退回独立 trace。做完这个,Langfuse 后台能看到一次故障从大白话到根因报告的完整调用树——**面试现场演示的最佳素材**。

---

## 8. 测试策略

原则:**不消耗真实 API 的测试为主**,真实调用只留一个可跳过的 smoke。

```python
# tests/test_llm_service.py 关键用例

class FakeCompletions:
    """按脚本依次返回预设响应,模拟 LLM"""
    def __init__(self, scripted: list[str | Exception]):
        self.scripted = list(scripted)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.scripted.pop(0)
        if isinstance(item, Exception):
            raise item
        return make_fake_response(item)   # 构造 choices/usage 结构


# 1. 坏 JSON → 自修复重试成功
async def test_structured_self_repair(monkeypatch):
    svc = make_service_with_fake(scripted=[
        '{"root_cause": "resource_exhaustion", "confidence": "not-a-number"}',  # 校验失败
        '{"root_cause": "resource_exhaustion", "confidence": 0.8, ...}',        # 修复成功
    ])
    parsed, meta = await svc.structured_completion(..., schema=LLMRootCauseAnalysis)
    assert meta["attempts"] == 2
    # 断言第二次请求的 messages 里带了校验错误反馈
    assert "未通过校验" in fake.calls[1]["messages"][-1]["content"]


# 2. 候选集约束:root_cause 出圈 → 重试 → 仍出圈 → LLMUnavailableError
async def test_closed_set_violation_exhausts(...)

# 3. 网络错误耗尽 → LLMUnavailableError(且退避被调用)
async def test_transport_error_backoff(...)

# 4. RCA 降级:LLM 不可用时 process() 仍成功,evidence["reasoning_mode"] == "rule_fallback"
async def test_rca_fallback_keeps_working(...)

# 5. NLU 快路径:高置信度 query 不产生任何 LLM 调用(fake.calls == [])
async def test_nlu_fast_path_zero_llm_calls(...)

# 6. 开关全关时:全套现有测试必须原样通过(回归保障,不用写新代码,跑 pytest 即是)

# 7. smoke(唯一真实调用,CI 跳过)
@pytest.mark.skipif(not os.getenv("LLM_API_KEY"), reason="no api key")
async def test_real_llm_smoke(...)
```

---

## 9. 实施顺序与验收标准

| 天 | 任务 | 验收标准(可观察、面试可演示) |
|----|------|------------------------------|
| D1 | `llm_service.py` + config + 单测 1-3 | `pytest tests/test_llm_service.py` 全绿;smoke 用真 key 跑通一次 |
| D2-3 | RCA 集成 + 单测 4 | 开 `LLM_ENABLE_RCA`,`POST /api/v1/incidents/trigger` 后查询 incident:`evidence.reasoning_mode == "llm_hybrid"`、`reasoning_chain` 每步带 [E*] 引用;**拔掉 API key 重发同请求 → 仍 200,`reasoning_mode == "rule_fallback"`** |
| D4 | NLU 集成 + 单测 5 | `POST /incidents/diagnose` body `{"query":"系统有点不对劲"}`(模糊句)→ `nlu_path == "llm_enhanced"` 且 `clarification_question` 非空;`{"query":"为什么下单这么慢"}` → `nlu_path == "fast_rule"` |
| D5 | Langfuse 验证 | Langfuse 后台能看到三类 trace 与 token 数;截图存档(面试素材) |
| D6 | Judge + 评测集跑分 | `POST /evaluations/run`,报告出现 `judge` 字段;temperature=0 下两次评分一致 |
| D7 | 全量回归 + 数字采集 | 开关全关 pytest 全绿;开关全开跑完整评测集,记录:快路径占比、RCA 单次平均 token、平均延迟、规则/LLM 结论一致率 |

**成本量级**(按上面 max_tokens 与证据包尺寸估算,以 DeepSeek 官网现价换算为准):RCA 单次约 1.5-2.5k input + ≤1.2k output tokens;NLU 慢路径约 0.6k + 0.5k;judge 约 1.5k + 0.4k。用 deepseek-chat 单次完整诊断(NLU 慢路径 + RCA + judge)在**分币级**,开发期跑几百次评测也只是几块钱——这个账本身就值得写进面试话术("我算过成本")。

---

## 10. 改造完成后的简历与面试口径

改造完成、数字实测后,简历上原来站不住的两条可以替换为(空位用 D7 实测值填,**不许预填**):

> **混合根因推理**:四路诊断证据(拓扑 BFS/贝叶斯先验/RAG/历史记忆)结构化打包,LLM 生成带证据引用的推理链与根因假设;闭集候选约束 + schema 校验失败自修复重试防幻觉,LLM 结论与规则引擎交叉校准、分歧全量审计;LLM 不可用自动降级规则路径,评测集上服务可用性不受 LLM 故障影响。

> **成本分层 NLU**:正则快路径零成本处理 __% 常规请求,低置信请求升级 LLM 结构化抽取(实体受已知服务/标准症状闭集约束),关键信息缺失时单轮生成聚合反问;全链路 Langfuse trace,单次诊断平均 __ tokens/__ ms。

必考八问(每问答案都对应本方案的具体代码位置):

1. LLM 输出格式不稳定怎么办?→ json mode + schema 注入 + `model_validate_json` + 校验错误喂回自修复(`llm_service.structured_completion`)
2. 幻觉怎么控制?→ 闭集候选约束(`validate_extra`)+ 推理链强制证据引用 + judge 的 `hallucination_detected`
3. LLM 挂了/超时系统会怎样?→ `LLMUnavailableError` → 规则兜底,`reasoning_mode` 可观测;现场拔 key 演示
4. 为什么不全用 LLM?→ 成本分层:__% 请求快路径零 token;规则结果还充当 LLM 的交叉验证器
5. 成本多少?→ Prometheus `llm_tokens_total` + Langfuse usage,报得出单次/日均数字
6. 为什么不用 LangChain 调模型?→ 薄封装可控(重试/退避/校验/埋点自己做),DeepSeek 同协议一个 base_url 切换
7. 怎么评测 LLM 效果?→ 四维规则指标 + LLM-as-judge(独立模型、temperature=0、rubric 拉开区分度),规则/LLM 一致率做监控
8. prompt 怎么迭代?→ 评测集回归:改 prompt → 跑评测 → 对比 judge 分与一致率(评测框架已有,这是它第一次有真对象)

---

## 11. 与第 2 步(修 LangGraph)的衔接

本方案刻意不动 orchestrator。等第 2 步把 `GraphState` 换成 `TypedDict` 修好图执行后,RCA 节点自动获得本方案的 LLM 能力,无需二次改造。建议顺序:先本方案 D1-D4(系统"有脑子"),再修图(编排真实化),最后 D5-D7(可观测与评测收尾)。


