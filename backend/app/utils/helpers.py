"""
AIOps Agent Platform - General Helpers

通用工具函数集合。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Coroutine, TypeVar

T = TypeVar("T")


def utc_now() -> datetime:
    """获取当前 UTC 时间"""
    return datetime.now(timezone.utc)


def iso_timestamp(dt: datetime | None = None) -> str:
    """
    生成 ISO 8601 格式时间戳

    Args:
        dt: 时间对象，None 表示当前时间

    Returns:
        str: ISO 8601 格式字符串
    """
    if dt is None:
        dt = utc_now()
    return dt.isoformat()


def generate_id(*parts: str) -> str:
    """
    基于输入生成确定性ID

    Args:
        *parts: 用于生成 ID 的字符串部分

    Returns:
        str: MD5 哈希ID
    """
    content = "|".join(parts)
    return hashlib.md5(content.encode()).hexdigest()[:12]


def truncate_string(text: str, max_length: int = 200, suffix: str = "...") -> str:
    """
    截断字符串

    Args:
        text: 原始字符串
        max_length: 最大长度
        suffix: 截断后缀

    Returns:
        str: 截断后的字符串
    """
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix


def safe_get(d: dict[str, Any], key: str, default: T | None = None) -> T | None:
    """
    安全获取字典值

    Args:
        d: 字典
        key: 键
        default: 默认值

    Returns:
        值或默认值
    """
    return d.get(key, default)


def safe_json_loads(
    data: str | bytes,
    default: dict[str, Any] | None = None,
) -> dict[str, Any] | list[Any] | None:
    """
    安全解析 JSON 字符串

    解析失败时返回默认值，不抛出异常。

    Args:
        data: JSON 字符串或字节
        default: 解析失败时的默认值

    Returns:
        解析后的对象或默认值

    Example:
        >>> safe_json_loads('{"a": 1}')
        {'a': 1}
        >>> safe_json_loads('invalid json')
        {}
    """
    if default is None:
        default = {}
    if not data:
        return default
    try:
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        return json.loads(data)
    except (json.JSONDecodeError, TypeError, ValueError):
        return default


def merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """
    递归合并字典

    Args:
        base: 基础字典
        override: 覆盖字典

    Returns:
        dict: 合并后的字典
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_dicts(result[key], value)
        else:
            result[key] = value
    return result


def with_timeout(seconds: float):
    """
    异步函数超时装饰器

    Args:
        seconds: 超时时间(秒)

    Example:
        @with_timeout(30)
        async def slow_operation():
            ...
    """
    def decorator(func: Callable[..., Coroutine[Any, Any, T]]) -> Callable[..., Coroutine[Any, Any, T]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            return await asyncio.wait_for(
                func(*args, **kwargs), timeout=seconds
            )
        return wrapper
    return decorator


def timing(func: Callable[..., Coroutine[Any, Any, T]]) -> Callable[..., Coroutine[Any, Any, T]]:
    """
    异步函数计时装饰器

    记录函数执行时间到日志。
    """
    from app.utils.logging import get_logger
    logger = get_logger(__name__)

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        start = time.perf_counter()
        try:
            return await func(*args, **kwargs)
        finally:
            elapsed = (time.perf_counter() - start) * 1000
            logger.debug(
                f"Function {func.__name__} executed in {elapsed:.2f}ms",
                function=func.__name__,
                duration_ms=elapsed,
            )
    return wrapper
