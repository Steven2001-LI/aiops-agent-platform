"""
AIOps Agent Platform - Time Series Analysis

时序数据分析工具，用于异常检测、趋势分析等。
"""

from __future__ import annotations

from typing import Any

import numpy as np
from pydantic import BaseModel, Field


class TimeSeriesPoint(BaseModel):
    """时序数据点"""
    timestamp: float = Field(description="Unix 时间戳")
    value: float = Field(description="数值")
    labels: dict[str, str] = Field(default_factory=dict, description="标签")


class AnomalyResult(BaseModel):
    """异常检测结果"""
    is_anomaly: bool = Field(default=False, description="是否为异常")
    score: float = Field(default=0.0, description="异常得分")
    method: str = Field(default="", description="检测方法")
    details: dict[str, Any] = Field(default_factory=dict, description="详细信息")


class TimeSeriesAnalyzer:
    """
    时序数据分析器

    提供常用的时序分析方法，包括异常检测、趋势分析等。
    """

    @staticmethod
    def zscore_detect(
        values: list[float],
        threshold: float = 3.0,
    ) -> list[AnomalyResult]:
        """
        Z-Score 异常检测

        Args:
            values: 数值序列
            threshold: Z-Score 阈值

        Returns:
            list[AnomalyResult]: 每个点的异常检测结果
        """
        if len(values) < 2:
            return [AnomalyResult(is_anomaly=False, score=0.0, method="zscore")]

        arr = np.array(values, dtype=np.float64)
        mean = np.mean(arr)
        std = np.std(arr)

        if std == 0:
            return [
                AnomalyResult(is_anomaly=False, score=0.0, method="zscore")
                for _ in values
            ]

        z_scores = np.abs((arr - mean) / std)

        return [
            AnomalyResult(
                is_anomaly=bool(z > threshold),
                score=float(min(z / threshold, 10.0)),
                method="zscore",
                details={"z_score": float(z), "mean": float(mean), "std": float(std)},
            )
            for z in z_scores
        ]

    @staticmethod
    def moving_average(
        values: list[float],
        window: int = 5,
    ) -> list[float]:
        """
        移动平均

        Args:
            values: 数值序列
            window: 窗口大小

        Returns:
            list[float]: 移动平均序列
        """
        if window <= 0 or len(values) == 0:
            return values

        arr = np.array(values, dtype=np.float64)
        weights = np.ones(window) / window
        ma = np.convolve(arr, weights, mode="same")
        return ma.tolist()

    @staticmethod
    def rate_of_change(values: list[float]) -> list[float]:
        """
        变化率计算

        Args:
            values: 数值序列

        Returns:
            list[float]: 变化率序列
        """
        if len(values) < 2:
            return [0.0]

        arr = np.array(values, dtype=np.float64)
        roc = np.diff(arr) / arr[:-1]
        #  prepend 0 for the first element
        return [0.0] + roc.tolist()

    @staticmethod
    def percentile_bounds(
        values: list[float],
        lower_pct: float = 5.0,
        upper_pct: float = 95.0,
    ) -> tuple[float, float]:
        """
        百分位数边界

        Args:
            values: 数值序列
            lower_pct: 下百分位
            upper_pct: 上百分位

        Returns:
            tuple[float, float]: (下边界, 上边界)
        """
        if not values:
            return (0.0, 0.0)

        arr = np.array(values, dtype=np.float64)
        return (
            float(np.percentile(arr, lower_pct)),
            float(np.percentile(arr, upper_pct)),
        )
