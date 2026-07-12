"""
AIOps Agent Platform - Evaluation Data Models

定义评估系统的数据模型，用于 Agent 效果评估和系统优化。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_serializer


class EvaluationType(str, Enum):
    """评估类型"""
    END_TO_END = "end_to_end"         # 端到端评估
    REASONING = "reasoning"           # 推理能力评估
    TOOL_CALL = "tool_call"           # 工具调用评估
    RAG = "rag"                       # RAG 评估
    SAFETY = "safety"                 # 安全性评估
    PERFORMANCE = "performance"       # 性能评估


class EvaluationStatus(str, Enum):
    """评估状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class MetricScore(BaseModel):
    """指标得分"""
    metric_name: str = Field(default="", description="指标名称")
    score: float = Field(default=0.0, ge=0.0, le=1.0, description="得分")
    weight: float = Field(default=1.0, ge=0.0, description="权重")
    details: dict[str, Any] = Field(default_factory=dict, description="详细数据")

    @property
    def weighted_score(self) -> float:
        """加权得分"""
        return self.score * self.weight


class EvaluationResult(BaseModel):
    """
    评估结果

    单次评估的完整结果记录。
    """
    result_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="结果ID",
    )
    evaluation_type: EvaluationType = Field(default=EvaluationType.END_TO_END)
    eval_name: str = Field(default="", description="评估名称")
    description: str = Field(default="", description="评估描述")

    # Agent 信息
    agent_type: str = Field(default="", description="被评估的 Agent 类型")
    agent_version: str = Field(default="1.0.0", description="Agent 版本")

    # 状态与时间
    status: EvaluationStatus = Field(default=EvaluationStatus.PENDING)
    started_at: datetime | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)

    # 得分
    overall_score: float = Field(
        default=0.0, ge=0.0, le=1.0, description="总得分"
    )
    metric_scores: list[MetricScore] = Field(default_factory=list)

    # 样本结果
    total_samples: int = Field(default=0, description="总样本数")
    passed_samples: int = Field(default=0, description="通过样本数")
    failed_samples: int = Field(default=0, description="失败样本数")

    # 详细结果
    sample_results: list[dict[str, Any]] = Field(
        default_factory=list, description="各样本详细结果"
    )
    errors: list[str] = Field(default_factory=list, description="错误信息")

    # 元数据
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_serializer("started_at", "completed_at")
    def serialize_datetimes(self, value: datetime | None) -> str | None:
        if value is not None:
            return value.isoformat()
        return None

    @property
    def pass_rate(self) -> float:
        """通过率"""
        if self.total_samples == 0:
            return 0.0
        return self.passed_samples / self.total_samples

    @property
    def duration_seconds(self) -> float:
        """评估耗时"""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return 0.0

    @property
    def weighted_overall_score(self) -> float:
        """加权总分"""
        if not self.metric_scores:
            return self.overall_score
        total_weight = sum(m.weight for m in self.metric_scores)
        if total_weight == 0:
            return 0.0
        return sum(m.weighted_score for m in self.metric_scores) / total_weight


class EvaluationDataset(BaseModel):
    """
    评估数据集

    包含评估用的测试样本。
    """
    dataset_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="数据集ID",
    )
    name: str = Field(default="", description="数据集名称")
    description: str = Field(default="", description="数据集描述")
    evaluation_type: EvaluationType = Field(default=EvaluationType.END_TO_END)

    # 样本
    samples: list[dict[str, Any]] = Field(default_factory=list)

    # 元数据
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_serializer("created_at")
    def serialize_created_at(self, value: datetime) -> str:
        return value.isoformat()

    @property
    def sample_count(self) -> int:
        """样本数量"""
        return len(self.samples)


class BenchmarkReport(BaseModel):
    """
    基准测试报告

    汇总多次评估的对比报告。
    """
    report_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="报告ID",
    )
    report_name: str = Field(default="", description="报告名称")
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    # 对比结果
    results: list[EvaluationResult] = Field(default_factory=list)

    # 汇总统计
    summary: dict[str, Any] = Field(
        default_factory=dict, description="汇总统计"
    )

    # 改进建议
    recommendations: list[str] = Field(default_factory=list)

    @field_serializer("generated_at")
    def serialize_generated_at(self, value: datetime) -> str:
        return value.isoformat()

    def compute_summary(self) -> dict[str, Any]:
        """计算汇总统计"""
        if not self.results:
            return {}

        scores = [r.weighted_overall_score for r in self.results]
        return {
            "avg_score": sum(scores) / len(scores),
            "max_score": max(scores),
            "min_score": min(scores),
            "total_evaluations": len(self.results),
            "total_samples": sum(r.total_samples for r in self.results),
            "overall_pass_rate": (
                sum(r.passed_samples for r in self.results)
                / max(sum(r.total_samples for r in self.results), 1)
            ),
        }
