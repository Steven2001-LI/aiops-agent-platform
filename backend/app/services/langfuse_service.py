"""
AIOps Agent Platform - Langfuse Integration Service

Langfuse 可观测性服务，提供完整的 Trace/Span/Generation/Score 管理。

观测层级设计:
- Orchestrator 级别: trace（整个 incident 处理流程）
- Agent 级别: span（每个 Agent 的执行）
- Tool 级别: span（工具调用）
- LLM 级别: generation（LLM 调用）

结构示例:
    Trace: incident_{incident_id}
    ├── Span: monitor_agent
    │   ├── Span: detect_anomaly (3-Sigma)
    │   ├── Span: detect_anomaly (EWMA)
    │   ├── Span: detect_anomaly (IsolationForest)
    │   └── Generation: summarize_alert
    ├── Span: rca_agent
    │   ├── Span: query_knowledge_graph
    │   ├── Span: bayesian_inference
    │   ├── Span: rag_retrieval
    │   └── Generation: analyze_root_cause
    ├── Span: heal_agent
    │   ├── Span: match_playbook
    │   ├── Span: dry_run
    │   └── Span: blast_radius_check
    └── Span: change_agent
        ├── Span: risk_scoring
        └── Span: approval_decision
"""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from app.config import LangfuseConfig, get_config
from app.utils.logging import get_logger

logger = get_logger(__name__)


class _NoOpTrace:
    """空操作 Trace 对象，当 Langfuse 禁用时使用。"""

    def __init__(self) -> None:
        self.id = f"noop-{uuid.uuid4().hex[:8]}"

    def span(self, **kwargs: Any) -> "_NoOpSpan":
        return _NoOpSpan()

    def generation(self, **kwargs: Any) -> "_NoOpGeneration":
        return _NoOpGeneration()

    def update(self, **kwargs: Any) -> None:
        pass

    def score(self, **kwargs: Any) -> None:
        pass


class _NoOpSpan:
    """空操作 Span 对象，当 Langfuse 禁用时使用。"""

    def __init__(self) -> None:
        self.id = f"noop-span-{uuid.uuid4().hex[:8]}"

    def span(self, **kwargs: Any) -> "_NoOpSpan":
        return _NoOpSpan()

    def generation(self, **kwargs: Any) -> "_NoOpGeneration":
        return _NoOpGeneration()

    def update(self, **kwargs: Any) -> None:
        pass

    def end(self, **kwargs: Any) -> None:
        pass

    def score(self, **kwargs: Any) -> None:
        pass


class _NoOpGeneration:
    """空操作 Generation 对象，当 Langfuse 禁用时使用。"""

    def __init__(self) -> None:
        self.id = f"noop-gen-{uuid.uuid4().hex[:8]}"

    def update(self, **kwargs: Any) -> None:
        pass

    def end(self, **kwargs: Any) -> None:
        pass


class LangfuseService:
    """
    Langfuse 服务（单例模式）

    提供完整的可观测性功能:
    - Trace 管理: 创建、获取、完成
    - Span 管理: 创建、更新、完成
    - 评分管理: trace/span 打分
    - 生成管理: 记录 LLM 调用

    当 Langfuse 未启用时，自动降级为 NoOp 实现，不影响业务逻辑。
    """

    # 类级别单例实例
    _instance: LangfuseService | None = None

    def __new__(cls, config: LangfuseConfig | None = None) -> "LangfuseService":
        """单例模式：确保全局只有一个 LangfuseService 实例。"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, config: LangfuseConfig | None = None) -> None:
        if self._initialized:
            return

        self._config = config or get_config().langfuse
        self._client: Any = None
        self._enabled = self._config.enabled and bool(self._config.public_key)

        # 内部追踪 ID 到 trace 对象的映射
        self._traces: dict[str, Any] = {}
        self._spans: dict[str, Any] = {}
        self._generations: dict[str, Any] = {}

        self._initialized = True
        logger.info(
            "LangfuseService created",
            enabled=self._enabled,
            host=self._config.host,
            release=self._config.release,
            environment=self._config.environment,
        )

    async def initialize(self) -> None:
        """
        异步初始化 Langfuse 客户端。

        在应用启动时调用，建立与 Langfuse 服务器的连接。
        如果 langfuse 包未安装或配置不完整，自动降级为禁用状态。
        """
        if not self._enabled:
            logger.info("Langfuse is disabled, using NoOp mode")
            return

        try:
            from langfuse import Langfuse

            self._client = Langfuse(
                public_key=self._config.public_key,
                secret_key=self._config.secret_key,
                host=self._config.host,
                release=self._config.release,
                environment=self._config.environment,
            )
            logger.info(
                "Langfuse client initialized",
                host=self._config.host,
                release=self._config.release,
                environment=self._config.environment,
            )
        except ImportError:
            logger.warning(
                "langfuse package not installed, Langfuse disabled. "
                "Install with: pip install langfuse"
            )
            self._enabled = False
        except Exception as e:
            logger.error("Failed to initialize Langfuse client", error=str(e))
            self._enabled = False

    def is_enabled(self) -> bool:
        """
        检查 Langfuse 是否可用。

        Returns:
            bool: True 如果 Langfuse 客户端已初始化且启用
        """
        return self._enabled and self._client is not None

    def shutdown(self) -> None:
        """
        关闭 Langfuse 客户端，刷新所有待发送的观测数据。

        在应用关闭时调用，确保所有 trace/span 数据都被发送到 Langfuse。
        """
        if self._client is not None and hasattr(self._client, "flush"):
            try:
                self._client.flush()
                logger.info("Langfuse client flushed and shutdown")
            except Exception as e:
                logger.error("Error flushing Langfuse client", error=str(e))

    # ========================================================================
    # Trace 管理
    # ========================================================================

    def create_trace(
        self,
        name: str,
        metadata: dict[str, Any] | None = None,
        user_id: str = "",
        session_id: str = "",
        tags: list[str] | None = None,
    ) -> Any:
        """
        创建一个新的 Trace。

        Trace 是整个 incident 处理流程的顶层追踪单元。

        Args:
            name: Trace 名称（如 incident_{incident_id}）
            metadata: 元数据字典
            user_id: 用户ID
            session_id: 会话ID
            tags: 标签列表

        Returns:
            Trace 对象（或 NoOpTrace 如果 Langfuse 未启用）
        """
        if not self.is_enabled():
            return _NoOpTrace()

        try:
            trace = self._client.trace(
                name=name,
                user_id=user_id or None,
                session_id=session_id or None,
                metadata=metadata or {},
                tags=tags or [],
            )
            trace_id = getattr(trace, "id", str(uuid.uuid4()))
            self._traces[trace_id] = trace
            logger.info(
                "Trace created",
                trace_id=trace_id,
                name=name,
                metadata_keys=list(metadata.keys()) if metadata else [],
            )
            return trace
        except Exception as e:
            logger.error("Failed to create trace", name=name, error=str(e))
            return _NoOpTrace()

    def get_trace(self, trace_id: str) -> Any:
        """
        获取已创建的 Trace 对象。

        Args:
            trace_id: Trace ID

        Returns:
            Trace 对象或 None
        """
        if not self.is_enabled():
            return _NoOpTrace()

        # 先从本地缓存查找
        trace = self._traces.get(trace_id)
        if trace is not None:
            return trace

        # 如果本地没有，尝试从 Langfuse 获取
        try:
            if hasattr(self._client, "get_trace"):
                trace = self._client.get_trace(trace_id)
                if trace:
                    self._traces[trace_id] = trace
                return trace
        except Exception as e:
            logger.error("Failed to get trace", trace_id=trace_id, error=str(e))

        return None

    def finalize_trace(
        self,
        trace_id: str,
        output: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        status_message: str = "",
    ) -> None:
        """
        完成 Trace，标记为结束状态。

        Args:
            trace_id: Trace ID
            output: 输出数据
            metadata: 额外元数据（会合并到现有元数据）
            status_message: 状态消息
        """
        trace = self._traces.get(trace_id)
        if trace is None:
            logger.warning("Trace not found for finalization", trace_id=trace_id)
            return

        if not self.is_enabled():
            return

        try:
            update_kwargs: dict[str, Any] = {}
            if output is not None:
                update_kwargs["output"] = output
            if metadata is not None:
                update_kwargs["metadata"] = metadata
            if status_message:
                update_kwargs["status_message"] = status_message

            trace.update(**update_kwargs)
            logger.info("Trace finalized", trace_id=trace_id, status_message=status_message)
        except Exception as e:
            logger.error("Failed to finalize trace", trace_id=trace_id, error=str(e))

    # ========================================================================
    # Span 管理
    # ========================================================================

    def create_span(
        self,
        trace_id: str,
        name: str,
        input_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """
        在指定 Trace 下创建一个 Span。

        Span 用于追踪 Agent 或 Tool 的执行。

        Args:
            trace_id: 父 Trace ID
            name: Span 名称（如 monitor_agent, query_metrics）
            input_data: 输入数据
            metadata: 元数据

        Returns:
            Span 对象（或 NoOpSpan 如果 Langfuse 未启用）
        """
        trace = self._traces.get(trace_id)
        if trace is None:
            logger.warning(
                "Cannot create span: trace not found",
                trace_id=trace_id,
                span_name=name,
            )
            return _NoOpSpan()

        if not self.is_enabled():
            return _NoOpSpan()

        try:
            span = trace.span(
                name=name,
                input=input_data or {},
                metadata=metadata or {},
            )
            span_id = getattr(span, "id", f"span-{uuid.uuid4().hex[:8]}")
            self._spans[span_id] = span
            logger.info(
                "Span created",
                span_id=span_id,
                name=name,
                trace_id=trace_id,
            )
            return span
        except Exception as e:
            logger.error("Failed to create span", name=name, trace_id=trace_id, error=str(e))
            return _NoOpSpan()

    def create_nested_span(
        self,
        parent_span_id: str,
        name: str,
        input_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """
        在指定 Span 下创建嵌套 Span。

        Args:
            parent_span_id: 父 Span ID
            name: Span 名称
            input_data: 输入数据
            metadata: 元数据

        Returns:
            Span 对象（或 NoOpSpan）
        """
        parent_span = self._spans.get(parent_span_id)
        if parent_span is None:
            logger.warning(
                "Cannot create nested span: parent span not found",
                parent_span_id=parent_span_id,
                span_name=name,
            )
            return _NoOpSpan()

        if not self.is_enabled():
            return _NoOpSpan()

        try:
            span = parent_span.span(
                name=name,
                input=input_data or {},
                metadata=metadata or {},
            )
            span_id = getattr(span, "id", f"span-{uuid.uuid4().hex[:8]}")
            self._spans[span_id] = span
            logger.info(
                "Nested span created",
                span_id=span_id,
                name=name,
                parent_span_id=parent_span_id,
            )
            return span
        except Exception as e:
            logger.error(
                "Failed to create nested span",
                name=name,
                parent_span_id=parent_span_id,
                error=str(e),
            )
            return _NoOpSpan()

    def update_span(
        self,
        span_id: str,
        output: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        status_message: str = "",
    ) -> None:
        """
        更新 Span 的输出和元数据。

        Args:
            span_id: Span ID
            output: 输出数据
            metadata: 额外元数据
            status_message: 状态消息
        """
        span = self._spans.get(span_id)
        if span is None:
            logger.warning("Span not found for update", span_id=span_id)
            return

        if not self.is_enabled():
            return

        try:
            update_kwargs: dict[str, Any] = {}
            if output is not None:
                update_kwargs["output"] = output
            if metadata is not None:
                update_kwargs["metadata"] = metadata
            if status_message:
                update_kwargs["status_message"] = status_message

            span.update(**update_kwargs)
            logger.debug("Span updated", span_id=span_id)
        except Exception as e:
            logger.error("Failed to update span", span_id=span_id, error=str(e))

    def finalize_span(
        self,
        span_id: str,
        output: dict[str, Any] | None = None,
        status_message: str = "",
    ) -> None:
        """
        完成 Span，标记为结束状态。

        Args:
            span_id: Span ID
            output: 最终输出数据
            status_message: 状态消息
        """
        span = self._spans.get(span_id)
        if span is None:
            logger.warning("Span not found for finalization", span_id=span_id)
            return

        if not self.is_enabled():
            return

        try:
            end_kwargs: dict[str, Any] = {}
            if output is not None:
                end_kwargs["output"] = output
            if status_message:
                end_kwargs["status_message"] = status_message

            span.end(**end_kwargs)
            logger.info("Span finalized", span_id=span_id, status_message=status_message)
        except Exception as e:
            logger.error("Failed to finalize span", span_id=span_id, error=str(e))

    # ========================================================================
    # 评分管理
    # ========================================================================

    def score_trace(
        self,
        trace_id: str,
        name: str,
        value: float,
        comment: str = "",
    ) -> None:
        """
        给 Trace 打分。

        Args:
            trace_id: Trace ID
            name: 评分名称（如 accuracy, helpfulness）
            value: 评分值（0-1 或自定义范围）
            comment: 评论
        """
        if not self.is_enabled():
            return

        try:
            self._client.score(
                trace_id=trace_id,
                name=name,
                value=value,
                comment=comment,
            )
            logger.info(
                "Trace scored",
                trace_id=trace_id,
                name=name,
                value=value,
                comment=comment[:100] if comment else "",
            )
        except Exception as e:
            logger.error(
                "Failed to score trace",
                trace_id=trace_id,
                name=name,
                error=str(e),
            )

    def score_span(
        self,
        span_id: str,
        name: str,
        value: float,
        comment: str = "",
    ) -> None:
        """
        给 Span 打分。

        Args:
            span_id: Span ID
            name: 评分名称
            value: 评分值
            comment: 评论
        """
        if not self.is_enabled():
            return

        try:
            # Langfuse SDK 的 score 方法在 trace 对象上也有
            # 对于 span score，我们需要通过 client 直接调用
            span = self._spans.get(span_id)
            if span is not None and hasattr(span, "score"):
                span.score(name=name, value=value, comment=comment)
            else:
                # Fallback: 尝试通过 client 的 score 方法
                self._client.score(
                    trace_id=span_id,
                    name=name,
                    value=value,
                    comment=comment,
                )
            logger.info(
                "Span scored",
                span_id=span_id,
                name=name,
                value=value,
            )
        except Exception as e:
            logger.error(
                "Failed to score span",
                span_id=span_id,
                name=name,
                error=str(e),
            )

    # ========================================================================
    # 生成管理
    # ========================================================================

    def log_generation(
        self,
        span_id: str,
        prompt: str | dict[str, Any],
        completion: str | dict[str, Any],
        model: str,
        usage: dict[str, int] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """
        记录 LLM 生成调用。

        Args:
            span_id: 父 Span ID
            prompt: 输入 prompt
            completion: LLM 输出
            model: 模型名称
            usage: Token 使用统计，如 {"input": 100, "output": 50, "total": 150}
            metadata: 额外元数据

        Returns:
            Generation 对象（或 NoOpGeneration）
        """
        span = self._spans.get(span_id)
        if span is None:
            logger.warning(
                "Cannot log generation: span not found",
                span_id=span_id,
            )
            return _NoOpGeneration()

        if not self.is_enabled():
            return _NoOpGeneration()

        try:
            # 构建 Langfuse 格式的 usage
            langfuse_usage: dict[str, Any] | None = None
            if usage:
                langfuse_usage = {
                    "input": usage.get("input", usage.get("prompt_tokens", 0)),
                    "output": usage.get("output", usage.get("completion_tokens", 0)),
                    "total": usage.get("total", usage.get("total_tokens", 0)),
                    "unit": usage.get("unit", "TOKENS"),
                }

            gen = span.generation(
                name="llm_generation",
                model=model,
                input=prompt,
                output=completion,
                usage=langfuse_usage,
                metadata=metadata or {},
            )
            gen_id = getattr(gen, "id", f"gen-{uuid.uuid4().hex[:8]}")
            self._generations[gen_id] = gen
            logger.info(
                "Generation logged",
                gen_id=gen_id,
                span_id=span_id,
                model=model,
                usage=langfuse_usage,
            )
            return gen
        except Exception as e:
            logger.error(
                "Failed to log generation",
                span_id=span_id,
                model=model,
                error=str(e),
            )
            return _NoOpGeneration()

    def log_generation_on_trace(
        self,
        trace_id: str,
        prompt: str | dict[str, Any],
        completion: str | dict[str, Any],
        model: str,
        usage: dict[str, int] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """
        直接在 Trace 下记录 LLM 生成调用（不绑定到特定 Span）。

        Args:
            trace_id: Trace ID
            prompt: 输入 prompt
            completion: LLM 输出
            model: 模型名称
            usage: Token 使用统计
            metadata: 额外元数据

        Returns:
            Generation 对象（或 NoOpGeneration）
        """
        trace = self._traces.get(trace_id)
        if trace is None:
            logger.warning(
                "Cannot log generation: trace not found",
                trace_id=trace_id,
            )
            return _NoOpGeneration()

        if not self.is_enabled():
            return _NoOpGeneration()

        try:
            langfuse_usage: dict[str, Any] | None = None
            if usage:
                langfuse_usage = {
                    "input": usage.get("input", usage.get("prompt_tokens", 0)),
                    "output": usage.get("output", usage.get("completion_tokens", 0)),
                    "total": usage.get("total", usage.get("total_tokens", 0)),
                    "unit": usage.get("unit", "TOKENS"),
                }

            gen = trace.generation(
                name="llm_generation",
                model=model,
                input=prompt,
                output=completion,
                usage=langfuse_usage,
                metadata=metadata or {},
            )
            gen_id = getattr(gen, "id", f"gen-{uuid.uuid4().hex[:8]}")
            self._generations[gen_id] = gen
            logger.info(
                "Generation logged on trace",
                gen_id=gen_id,
                trace_id=trace_id,
                model=model,
            )
            return gen
        except Exception as e:
            logger.error(
                "Failed to log generation on trace",
                trace_id=trace_id,
                error=str(e),
            )
            return _NoOpGeneration()

    # ========================================================================
    # 上下文管理器
    # ========================================================================

    @asynccontextmanager
    async def trace_context(
        self,
        name: str,
        metadata: dict[str, Any] | None = None,
        user_id: str = "",
        session_id: str = "",
        tags: list[str] | None = None,
    ) -> AsyncGenerator[Any, None]:
        """
        Trace 上下文管理器。

        使用 async with 语句自动管理 Trace 的生命周期:
            async with langfuse_service.trace_context("incident_123") as trace:
                # ... 执行操作
                pass

        Args:
            name: Trace 名称
            metadata: 元数据
            user_id: 用户ID
            session_id: 会话ID
            tags: 标签列表

        Yields:
            Trace 对象
        """
        trace = self.create_trace(
            name=name,
            metadata=metadata,
            user_id=user_id,
            session_id=session_id,
            tags=tags,
        )
        try:
            yield trace
        except Exception as e:
            # 如果发生异常，记录错误状态
            trace_id = getattr(trace, "id", None)
            if trace_id:
                self.finalize_trace(
                    trace_id=trace_id,
                    status_message=f"Error: {str(e)[:200]}",
                )
            raise

    @asynccontextmanager
    async def span_context(
        self,
        trace_id: str,
        name: str,
        input_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AsyncGenerator[Any, None]:
        """
        Span 上下文管理器。

        使用 async with 语句自动管理 Span 的生命周期:
            async with langfuse_service.span_context(trace_id, "monitor_agent") as span:
                # ... 执行操作
                pass

        Args:
            trace_id: 父 Trace ID
            name: Span 名称
            input_data: 输入数据
            metadata: 元数据

        Yields:
            Span 对象
        """
        span = self.create_span(
            trace_id=trace_id,
            name=name,
            input_data=input_data,
            metadata=metadata,
        )
        start_time = time.time()
        try:
            yield span
        except Exception as e:
            span_id = getattr(span, "id", None)
            if span_id:
                self.finalize_span(
                    span_id=span_id,
                    status_message=f"Error: {str(e)[:200]}",
                )
            raise
        finally:
            span_id = getattr(span, "id", None)
            if span_id and span_id not in ["", "noop"]:
                elapsed_ms = int((time.time() - start_time) * 1000)
                self.update_span(
                    span_id=span_id,
                    metadata={"execution_time_ms": elapsed_ms},
                )
                self.finalize_span(span_id=span_id)

    # ========================================================================
    # 便捷方法
    # ========================================================================

    def get_span(self, span_id: str) -> Any:
        """
        获取已创建的 Span 对象。

        Args:
            span_id: Span ID

        Returns:
            Span 对象或 None
        """
        return self._spans.get(span_id)

    def get_generation(self, gen_id: str) -> Any:
        """
        获取已创建的 Generation 对象。

        Args:
            gen_id: Generation ID

        Returns:
            Generation 对象或 None
        """
        return self._generations.get(gen_id)

    def clear_cache(self) -> None:
        """
        清空本地缓存的 trace/span/generation 对象。

        注意: 这不会删除 Langfuse 服务器上的数据。
        """
        self._traces.clear()
        self._spans.clear()
        self._generations.clear()
        logger.debug("LangfuseService local cache cleared")

    def get_stats(self) -> dict[str, int]:
        """
        获取本地缓存的统计信息。

        Returns:
            dict: 包含 traces, spans, generations 数量的字典
        """
        return {
            "traces": len(self._traces),
            "spans": len(self._spans),
            "generations": len(self._generations),
        }


# 全局服务实例获取函数
_langfuse_service: LangfuseService | None = None


def get_langfuse_service() -> LangfuseService:
    """
    获取 LangfuseService 单例实例。

    Returns:
        LangfuseService: 全局唯一的 LangfuseService 实例
    """
    global _langfuse_service
    if _langfuse_service is None:
        _langfuse_service = LangfuseService()
    return _langfuse_service


def reset_langfuse_service() -> None:
    """
    重置 LangfuseService 单例（主要用于测试）。
    """
    global _langfuse_service
    _langfuse_service = None
    LangfuseService._instance = None
    logger.debug("LangfuseService singleton reset")
