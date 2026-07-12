"""
AIOps Agent Platform - Logging Utilities

使用 structlog 构建结构化日志系统，支持 JSON 输出和上下文追踪。
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.config import AppConfig, get_config


def configure_logging(config: AppConfig | None = None) -> None:
    """
    配置结构化日志系统

    Args:
        config: 应用配置，为 None 时自动加载
    """
    if config is None:
        config = get_config()

    # 配置标准库 logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, config.log_level.upper(), logging.INFO),
    )

    # 配置 structlog
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.ExtraAdder(),
    ]

    if config.is_development:
        # 开发环境：彩色输出
        processors = [
            *shared_processors,
            structlog.dev.ConsoleRenderer(colors=True),
        ]
    else:
        # 生产环境：JSON 输出
        processors = [
            *shared_processors,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, config.log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    获取结构化日志记录器

    Args:
        name: 日志器名称（通常使用 __name__）

    Returns:
        structlog.stdlib.BoundLogger: 结构化日志记录器
    """
    return structlog.get_logger(name)


class LogContext:
    """
    日志上下文管理器

    用于在代码块中添加上下文信息到所有日志记录。

    Example:
        with LogContext(correlation_id="abc-123", incident_id="inc-001"):
            logger.info("Processing incident")
            # 所有在此块内的日志都会包含 correlation_id 和 incident_id
    """

    def __init__(self, **context: Any) -> None:
        self.context = context
        self._token: Any = None

    def __enter__(self) -> LogContext:
        structlog.contextvars.bind_contextvars(**self.context)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        for key in self.context:
            structlog.contextvars.unbind_contextvars(key)


def bind_logger(**kwargs: Any) -> None:
    """
    绑定上下文变量到当前日志记录器

    Args:
        **kwargs: 要绑定的键值对
    """
    structlog.contextvars.bind_contextvars(**kwargs)


def unbind_logger(*keys: str) -> None:
    """
    解绑上下文变量

    Args:
        *keys: 要解绑的键名
    """
    structlog.contextvars.unbind_contextvars(*keys)
