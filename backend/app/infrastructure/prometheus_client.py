"""
AIOps Agent Platform — Prometheus HTTP Client

通过 Prometheus HTTP API 查询时序指标数据。
为 MonitorAgent 提供真实数据源接入能力。

API 文档: https://prometheus.io/docs/prometheus/latest/querying/api/
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from app.utils.logging import get_logger

logger = get_logger(__name__)


class PrometheusClient:
    """
    Prometheus HTTP API 客户端。

    支持：
    - instant query（即时查询当前值）
    - range query（时间范围查询，获取历史数据）
    - 告警规则查询
    - 指标元数据查询
    """

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("PROMETHEUS_URL", "http://localhost:9090")).rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ==================== Range Query ====================

    async def query_range(
        self,
        query: str,
        start: datetime | None = None,
        end: datetime | None = None,
        step: str = "1m",
    ) -> list[float]:
        """
        查询时间范围内的指标数据。

        示例 PromQL:
          - 'rate(node_cpu_seconds_total{mode="idle"}[5m])'
          - 'node_memory_MemAvailable_bytes'
          - 'node_filesystem_avail_bytes{mountpoint="/"}'

        Args:
            query: PromQL 查询表达式
            start: 开始时间（默认 1 小时前）
            end: 结束时间（默认现在）
            step: 步长（默认 1m）

        Returns:
            list[float]: 时间序列数据点列表
        """
        if end is None:
            end = datetime.now(timezone.utc)
        if start is None:
            start = end - timedelta(hours=1)

        url = f"{self.base_url}/api/v1/query_range"
        params = {
            "query": query,
            "start": start.timestamp(),
            "end": end.timestamp(),
            "step": step,
        }

        try:
            session = await self._get_session()
            async with session.get(url, params=params) as resp:
                data = await resp.json()

            if data["status"] != "success":
                logger.warning("Prometheus query_range failed", query=query, status=data["status"])
                return []

            results = data.get("data", {}).get("result", [])
            if not results:
                return []

            # 取第一个时间序列的值
            values = results[0].get("values", [])
            return [float(v[1]) for v in values]

        except Exception as e:
            logger.error("Prometheus query_range error", query=query, error=str(e))
            return []

    # ==================== Instant Query ====================

    async def query_instant(self, query: str, time: datetime | None = None) -> float | None:
        """
        即时查询当前指标值。

        Args:
            query: PromQL 查询表达式
            time: 查询时间点（默认现在）

        Returns:
            float | None: 当前值，查询失败返回 None
        """
        url = f"{self.base_url}/api/v1/query"
        params: dict[str, Any] = {"query": query}
        if time is not None:
            params["time"] = time.timestamp()

        try:
            session = await self._get_session()
            async with session.get(url, params=params) as resp:
                data = await resp.json()

            if data["status"] != "success":
                return None

            results = data.get("data", {}).get("result", [])
            if not results:
                return None

            return float(results[0]["value"][1])

        except Exception as e:
            logger.error("Prometheus query_instant error", query=query, error=str(e))
            return None

    # ==================== 预置便捷方法 ====================

    async def get_cpu_usage(self, lookback_minutes: int = 60) -> list[float]:
        """获取宿主机 CPU 使用率历史数据（百分比）"""
        query = '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)'
        return await self.query_range(
            query,
            start=datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes),
            step="1m",
        )

    async def get_memory_usage(self, lookback_minutes: int = 60) -> list[float]:
        """获取宿主机内存使用率历史数据（百分比）"""
        query = '(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100'
        return await self.query_range(
            query,
            start=datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes),
            step="1m",
        )

    async def get_disk_usage(self, mountpoint: str = "/", lookback_minutes: int = 60) -> list[float]:
        """获取磁盘使用率历史数据（百分比）"""
        query = f'(1 - (node_filesystem_avail_bytes{{mountpoint="{mountpoint}",fstype!="tmpfs"}} / node_filesystem_size_bytes{{mountpoint="{mountpoint}",fstype!="tmpfs"}})) * 100'
        return await self.query_range(
            query,
            start=datetime.now(timezone.utc) - timedelta(minutes=lookback_minutes),
            step="1m",
        )

    async def get_current_cpu(self) -> float | None:
        """获取当前 CPU 使用率"""
        query = '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[1m])) * 100)'
        return await self.query_instant(query)

    async def get_current_memory(self) -> float | None:
        """获取当前内存使用率"""
        query = '(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100'
        return await self.query_instant(query)

    # ==================== 健康检查 ====================

    async def health_check(self) -> dict[str, Any]:
        """检查 Prometheus 是否可达"""
        try:
            session = await self._get_session()
            async with session.get(f"{self.base_url}/-/healthy") as resp:
                return {
                    "reachable": resp.status == 200,
                    "status_code": resp.status,
                    "url": self.base_url,
                }
        except Exception as e:
            return {
                "reachable": False,
                "error": str(e),
                "url": self.base_url,
            }
