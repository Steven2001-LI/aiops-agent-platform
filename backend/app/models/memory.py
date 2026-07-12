"""
AIOps Agent Platform - Memory Data Models

定义记忆系统的数据模型，包括短期记忆、长期记忆和工作记忆的存储结构。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_serializer


class MemoryType(str, Enum):
    """记忆类型"""
    EPISODIC = "episodic"        # 情景记忆（具体事件）
    SEMANTIC = "semantic"        # 语义记忆（知识、概念）
    PROCEDURAL = "procedural"    # 程序记忆（操作步骤）
    OBSERVATION = "observation"  # 观察记忆（原始观测）


class MemoryLevel(str, Enum):
    """记忆层级"""
    SHORT_TERM = "short_term"    # 短期记忆
    LONG_TERM = "long_term"      # 长期记忆
    WORKING = "working"          # 工作记忆


class MemoryEntry(BaseModel):
    """
    记忆条目

    记忆系统的基本存储单元，支持向量化检索。
    """
    memory_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="记忆唯一ID",
    )
    memory_type: MemoryType = Field(default=MemoryType.OBSERVATION)
    memory_level: MemoryLevel = Field(default=MemoryLevel.SHORT_TERM)

    # 内容
    content: str = Field(default="", description="记忆文本内容")
    content_vector: list[float] | None = Field(
        default=None, description="内容向量表示"
    )
    summary: str = Field(default="", description="内容摘要")

    # 来源
    source_agent: str = Field(default="", description="产生记忆的 Agent")
    source_incident_id: str = Field(default="", description="关联故障ID")
    source_event_id: str = Field(default="", description="关联事件ID")

    # 时间
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    expires_at: datetime | None = Field(
        default=None, description="过期时间",
    )
    last_accessed_at: datetime | None = Field(default=None)
    access_count: int = Field(default=0, description="被访问次数")

    # 权重与关联
    importance_score: float = Field(
        default=0.5, ge=0.0, le=1.0, description="重要性得分"
    )
    confidence_score: float = Field(
        default=1.0, ge=0.0, le=1.0, description="置信度得分"
    )
    related_memory_ids: list[str] = Field(
        default_factory=list, description="关联记忆ID列表"
    )
    tags: list[str] = Field(default_factory=list, description="标签")

    # 元数据
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_serializer("created_at", "expires_at", "last_accessed_at")
    def serialize_datetimes(self, value: datetime | None) -> str | None:
        if value is not None:
            return value.isoformat()
        return None

    def touch(self) -> None:
        """更新访问时间"""
        self.last_accessed_at = datetime.now(timezone.utc)
        self.access_count += 1

    @property
    def is_expired(self) -> bool:
        """是否已过期"""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at

    @property
    def retrieval_score(self) -> float:
        """
        综合检索得分

        综合考虑重要性、访问频率和时效性。
        """
        import math

        # 基础重要性
        score = self.importance_score

        # 访问频率增益（访问越多越重要）
        access_boost = min(math.log10(self.access_count + 1) * 0.1, 0.3)
        score += access_boost

        # 时效性衰减
        if self.last_accessed_at:
            hours_since_access = (
                datetime.now(timezone.utc) - self.last_accessed_at
            ).total_seconds() / 3600
            recency_boost = max(0, 0.2 * (1 - hours_since_access / 168))  # 一周内衰减
            score += recency_boost

        return min(score, 1.0)


class MemoryQuery(BaseModel):
    """
    记忆查询

    用于向记忆系统发起检索请求。
    """
    query_id: str = Field(default_factory=lambda: str(uuid4()))
    query_text: str = Field(default="", description="查询文本")
    query_vector: list[float] | None = Field(
        default=None, description="查询向量(可选)"
    )
    memory_types: list[MemoryType] = Field(
        default_factory=list, description="限定记忆类型"
    )
    memory_levels: list[MemoryLevel] = Field(
        default_factory=list, description="限定记忆层级"
    )
    source_agent: str = Field(
        default="", description="限定来源 Agent"
    )
    source_incident_id: str = Field(
        default="", description="限定关联故障"
    )
    tags: list[str] = Field(default_factory=list, description="限定标签")
    top_k: int = Field(default=10, ge=1, le=100, description="返回数量")
    similarity_threshold: float = Field(
        default=0.7, ge=0.0, le=1.0, description="相似度阈值"
    )
    time_range_start: datetime | None = Field(default=None)
    time_range_end: datetime | None = Field(default=None)


class MemoryQueryResult(BaseModel):
    """记忆查询结果"""
    query_id: str = Field(default="")
    results: list[MemoryEntry] = Field(default_factory=list)
    similarities: list[float] = Field(
        default_factory=list, description="各结果的相似度得分"
    )
    total_found: int = Field(default=0, description="总匹配数")
    query_time_ms: float = Field(default=0.0, description="查询耗时(ms)")

    @property
    def is_empty(self) -> bool:
        """结果是否为空"""
        return len(self.results) == 0


class WorkingMemorySlot(BaseModel):
    """
    工作记忆槽位

    工作记忆是有限的临时存储，用于当前正在处理的信息。
    """
    slot_id: str = Field(default_factory=lambda: str(uuid4()))
    name: str = Field(default="", description="槽位名称")
    content: Any = Field(default=None, description="槽位内容")
    memory_entry: MemoryEntry | None = Field(
        default=None, description="关联的记忆条目"
    )
    priority: int = Field(default=5, ge=1, le=10, description="优先级")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    ttl_seconds: int = Field(
        default=300, description="生存时间(秒)"
    )

    @property
    def is_expired(self) -> bool:
        """是否已过期"""
        elapsed = (datetime.now(timezone.utc) - self.created_at).total_seconds()
        return elapsed > self.ttl_seconds
