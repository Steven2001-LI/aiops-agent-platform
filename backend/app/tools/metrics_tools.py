"""
AIOps Agent Platform - Metrics Tools

指标查询工具，用于从监控系统获取指标数据。
提供基础查询工具和高级便捷的指标查询工具。
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

from app.infrastructure.prometheus_client import PrometheusClient
from app.tools.base import BaseTool, ToolParameter, ToolResult
from app.utils.logging import get_logger

logger = get_logger(__name__)


# ============================================================
# 基础指标查询工具（保留用于 LLM function calling）
# ============================================================


class QueryMetricsTool(BaseTool):
    """查询时序指标工具"""

    @property
    def name(self) -> str:
        return "query_metrics"

    @property
    def description(self) -> str:
        return "从监控系统查询时序指标数据，支持 PromQL 风格查询"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="query",
                description="指标查询表达式(如 cpu_usage{service='api'})",
                type="string",
                required=True,
            ),
            ToolParameter(
                name="start_time",
                description="查询起始时间(ISO 8601 格式)",
                type="string",
                required=True,
            ),
            ToolParameter(
                name="end_time",
                description="查询结束时间(ISO 8601 格式)",
                type="string",
                required=True,
            ),
            ToolParameter(
                name="step",
                description="采样间隔(如 1m, 5m, 1h)",
                type="string",
                required=False,
                default="1m",
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        """
        执行指标查询

        Args:
            query: 查询表达式
            start_time: 起始时间
            end_time: 结束时间
            step: 采样间隔

        Returns:
            ToolResult: 查询结果
        """
        query = kwargs.get("query", "")
        start_time = kwargs.get("start_time", "")
        end_time = kwargs.get("end_time", "")
        step = kwargs.get("step", "1m")

        logger.info(
            "Querying metrics",
            query=query,
            start=start_time,
            end=end_time,
        )

        # 真实调用 Prometheus
        try:
            client = PrometheusClient()
            from datetime import datetime, timezone, timedelta
            end = datetime.now(timezone.utc)
            start = end - timedelta(hours=1)
            values = await client.query_range(query, start=start, end=end, step=step)
            return ToolResult.ok(
                tool_name=self.name,
                data={
                    "query": query,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "step": step,
                    "values": values,
                    "count": len(values),
                    "source": "prometheus",
                },
            )
        except Exception as e:
            logger.error("Prometheus query failed", query=query, error=str(e))
            return ToolResult.error(
                tool_name=self.name,
                error_message=f"Prometheus query failed: {e}",
            )


class GetServiceMetricsTool(BaseTool):
    """获取服务关键指标工具"""

    @property
    def name(self) -> str:
        return "get_service_metrics"

    @property
    def description(self) -> str:
        return "获取指定服务的关键性能指标(CPU、内存、QPS、延迟、错误率)"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="service",
                description="服务名称",
                type="string",
                required=True,
            ),
            ToolParameter(
                name="time_range",
                description="时间范围(如 1h, 24h, 7d)",
                type="string",
                required=False,
                default="1h",
            ),
            ToolParameter(
                name="environment",
                description="环境(prod/staging/dev)",
                type="string",
                required=False,
                default="prod",
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        """获取服务指标"""
        service = kwargs.get("service", "")
        time_range = kwargs.get("time_range", "1h")
        environment = kwargs.get("environment", "prod")

        logger.info(
            "Getting service metrics",
            service=service,
            time_range=time_range,
        )

        try:
            client = PrometheusClient()
            from datetime import datetime, timezone, timedelta
            end = datetime.now(timezone.utc)
            start = end - timedelta(hours=1)
            cpu = await client.query_range(
                '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)',
                start=start, end=end, step="1m",
            )
            mem = await client.query_range(
                '(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100',
                start=start, end=end, step="1m",
            )
            return ToolResult.ok(
                tool_name=self.name,
                data={
                    "service": service,
                    "environment": environment,
                    "time_range": time_range,
                    "cpu_usage": cpu,
                    "memory_usage": mem,
                    "source": "prometheus",
                },
            )
        except Exception as e:
            return ToolResult.error(
                tool_name=self.name,
                error_message=f"Service metrics fetch failed: {e}",
            )


class DetectAnomaliesTool(BaseTool):
    """异常检测工具"""

    @property
    def name(self) -> str:
        return "detect_anomalies"

    @property
    def description(self) -> str:
        return "对指标数据进行异常检测，识别异常点"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="metric_data",
                description="指标数据列表 [(timestamp, value), ...]",
                type="array",
                required=True,
            ),
            ToolParameter(
                name="method",
                description="检测方法(zscore/percentile/isolation_forest)",
                type="string",
                required=False,
                default="zscore",
            ),
            ToolParameter(
                name="threshold",
                description="异常阈值",
                type="number",
                required=False,
                default=3.0,
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        """执行异常检测"""
        metric_data = kwargs.get("metric_data", [])
        method = kwargs.get("method", "zscore")
        threshold = kwargs.get("threshold", 3.0)

        logger.info(
            "Detecting anomalies",
            data_points=len(metric_data),
            method=method,
        )

        try:
            from app.agents.monitor_agent import MonitorAgent, MetricInput
            from app.agents.base import AgentExecutionContext
            values = [
                p[1] if isinstance(p, (list, tuple)) and len(p) > 1 else p
                for p in metric_data
            ]
            monitor = MonitorAgent()
            metric_input = MetricInput(
                metric_name="cpu_usage_percent",
                metric_value=values[-1] if values else 0,
                service_name="unknown",
                history_values=values or [],
            )
            ctx = AgentExecutionContext(incident_id="tool-detect-anomaly")
            result = await monitor.process(metric_input, ctx)
            detection = (result.output_data or {}).get("detection_result", {})
            return ToolResult.ok(
                tool_name=self.name,
                data={
                    "is_anomaly": detection.get("is_anomaly", False),
                    "score": detection.get("score", 0.0),
                    "confidence": detection.get("confidence", 0.0),
                    "source": "monitor_agent",
                },
            )
        except Exception as e:
            return ToolResult.error(
                tool_name=self.name,
                error_message=f"Anomaly detection failed: {e}",
            )


# ============================================================
# 高级指标查询工具 - MetricsQueryTool
# ============================================================


class MetricsQueryTool(BaseTool):
    """
    高级指标查询工具

    提供便捷的指标查询方法，支持从模拟数据集或真实数据源查询指标。
    自动将时间范围转换为时间序列数据点。
    """

    @property
    def name(self) -> str:
        return "query_metrics_advanced"

    @property
    def description(self) -> str:
        return (
            "查询指定服务的指标数据（CPU、内存、延迟、错误率等）。"
            "支持指定时间范围和数据源，返回带时间戳的时间序列数据。"
        )

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="service",
                description="服务名称（如 order-service, payment-service）",
                type="string",
                required=True,
            ),
            ToolParameter(
                name="metric",
                description="指标名称（cpu_usage_percent/memory_usage_percent/p99_latency_ms/error_rate_percent）",
                type="string",
                required=True,
            ),
            ToolParameter(
                name="duration",
                description="查询时间范围（如 1h, 6h, 24h, 7d）",
                type="string",
                required=False,
                default="1h",
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolResult:
        """
        执行高级指标查询

        Args:
            service: 服务名称
            metric: 指标名称
            duration: 时间范围

        Returns:
            ToolResult: 时间序列数据
        """
        service = kwargs.get("service", "")
        metric = kwargs.get("metric", "")
        duration = kwargs.get("duration", "1h")

        logger.info(
            "Advanced metrics query",
            service=service,
            metric=metric,
            duration=duration,
        )

        try:
            result = await self._run(service, metric, duration)
            return ToolResult.ok(
                tool_name=self.name,
                data=result,
            )
        except Exception as e:
            logger.error(
                "Metrics query failed",
                service=service,
                metric=metric,
                error=str(e),
            )
            return ToolResult.error(
                tool_name=self.name,
                error_message=f"Failed to query metrics: {e}",
            )

    async def _run(
        self,
        service: str,
        metric: str,
        duration: str = "1h",
    ) -> dict[str, Any]:
        """
        核心指标查询逻辑

        优先从数据集加载，否则生成模拟数据。
        """
        # 尝试从数据集加载
        from app.data.datasets import get_metric_data

        data_points = get_metric_data(service, metric, scenario="normal")

        # 生成时间戳
        timestamps = self._generate_timestamps(len(data_points), duration)

        return {
            "service": service,
            "metric": metric,
            "duration": duration,
            "timestamps": timestamps,
            "values": data_points,
            "unit": self._get_unit(metric),
            "data_points": len(data_points),
        }

    async def query_cpu_usage(
        self,
        service: str,
        duration: str = "1h",
    ) -> list[dict[str, Any]]:
        """
        查询 CPU 使用率

        Args:
            service: 服务名称
            duration: 时间范围

        Returns:
            时间序列数据点列表，每项包含 timestamp 和 value
        """
        result = await self._run(service, "cpu_usage_percent", duration)
        return [
            {"timestamp": ts, "value": val}
            for ts, val in zip(result["timestamps"], result["values"])
        ]

    async def query_memory_usage(
        self,
        service: str,
        duration: str = "1h",
    ) -> list[dict[str, Any]]:
        """
        查询内存使用率

        Args:
            service: 服务名称
            duration: 时间范围

        Returns:
            时间序列数据点列表
        """
        result = await self._run(service, "memory_usage_percent", duration)
        return [
            {"timestamp": ts, "value": val}
            for ts, val in zip(result["timestamps"], result["values"])
        ]

    async def query_latency(
        self,
        service: str,
        duration: str = "1h",
    ) -> list[dict[str, Any]]:
        """
        查询 P99 延迟

        Args:
            service: 服务名称
            duration: 时间范围

        Returns:
            时间序列数据点列表
        """
        result = await self._run(service, "p99_latency_ms", duration)
        return [
            {"timestamp": ts, "value": val}
            for ts, val in zip(result["timestamps"], result["values"])
        ]

    async def query_error_rate(
        self,
        service: str,
        duration: str = "1h",
    ) -> list[dict[str, Any]]:
        """
        查询错误率

        Args:
            service: 服务名称
            duration: 时间范围

        Returns:
            时间序列数据点列表
        """
        result = await self._run(service, "error_rate_percent", duration)
        return [
            {"timestamp": ts, "value": val}
            for ts, val in zip(result["timestamps"], result["values"])
        ]

    @staticmethod
    def _generate_timestamps(
        count: int,
        duration: str,
        end_time: datetime | None = None,
    ) -> list[str]:
        """
        生成时间戳列表

        Args:
            count: 数据点数量
            duration: 时间范围字符串
            end_time: 结束时间（默认为当前时间）

        Returns:
            ISO 格式时间戳列表
        """
        if end_time is None:
            end_time = datetime.now(timezone.utc)

        # 解析 duration
        total_seconds = MetricsQueryTool._parse_duration(duration)
        interval_seconds = total_seconds // max(count, 1)

        timestamps = []
        for i in range(count):
            ts = end_time - timedelta(seconds=interval_seconds * (count - 1 - i))
            timestamps.append(ts.isoformat())

        return timestamps

    @staticmethod
    def _parse_duration(duration: str) -> int:
        """
        解析时间范围字符串为秒数

        Args:
            duration: 如 "1h", "24h", "7d"

        Returns:
            秒数
        """
        if not duration:
            return 3600

        unit = duration[-1].lower()
        try:
            value = int(duration[:-1])
        except ValueError:
            return 3600

        multipliers = {
            "s": 1,
            "m": 60,
            "h": 3600,
            "d": 86400,
            "w": 604800,
        }
        return value * multipliers.get(unit, 3600)

    @staticmethod
    def _get_unit(metric: str) -> str:
        """获取指标单位"""
        unit_map = {
            "cpu_usage_percent": "%",
            "memory_usage_percent": "%",
            "p99_latency_ms": "ms",
            "error_rate_percent": "%",
            "qps": "req/s",
            "disk_usage_percent": "%",
            "network_io_mbps": "Mbps",
        }
        return unit_map.get(metric, "")


def register_metrics_tools() -> list[BaseTool]:
    """
    注册所有指标工具

    Returns:
        list[BaseTool]: 指标工具列表
    """
    return [
        QueryMetricsTool(),
        GetServiceMetricsTool(),
        DetectAnomaliesTool(),
        MetricsQueryTool(),
    ]
