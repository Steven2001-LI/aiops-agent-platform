"""
AIOps Agent Platform - Dependency Injection

FastAPI 依赖注入定义，提供配置、服务等共享资源的注入。
"""

from __future__ import annotations

from typing import AsyncGenerator

from fastapi import Request

from app.config import AppConfig, get_config
from app.utils.logging import get_logger

logger = get_logger(__name__)


async def get_config_dep() -> AsyncGenerator[AppConfig, None]:
    """
    配置依赖

    Yields:
        AppConfig: 应用配置实例
    """
    yield get_config()


async def get_logger_dep(request: Request) -> AsyncGenerator:
    """
    日志依赖 - 绑定请求上下文

    Args:
        request: FastAPI 请求对象

    Yields:
        BoundLogger: 绑定了请求上下文的日志记录器
    """
    logger = get_logger("api")
    logger.bind(
        method=request.method,
        path=request.url.path,
        client_host=request.client.host if request.client else "unknown",
    )
    yield logger


class CommonQueryParams:
    """
    通用查询参数

    用于列表查询的分页、排序和过滤。
    """

    def __init__(
        self,
        page: int = 1,
        page_size: int = 20,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        search: str = "",
    ) -> None:
        self.page = max(1, page)
        self.page_size = min(max(1, page_size), 100)
        self.sort_by = sort_by
        self.sort_order = sort_order.lower() if sort_order.lower() in ("asc", "desc") else "desc"
        self.search = search

    @property
    def offset(self) -> int:
        """计算偏移量"""
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        """返回限制数量"""
        return self.page_size
