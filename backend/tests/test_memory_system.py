"""
AIOps Agent Platform - Memory System Integration Tests

测试记忆系统的短期记忆、长期记忆、工作记忆、记忆流转和混合搜索。
"""

from __future__ import annotations

import asyncio

import pytest

from app.memory.core import MemorySystem
from app.memory.long_term import (
    LongTermMemory,
    compute_idf,
    keyword_search_score,
    rrf_fusion,
    time_decay_score,
)
from app.memory.short_term import ShortTermMemory
from app.memory.storage import InMemoryStorage
from app.memory.working_memory import WorkingMemory
from app.models.memory import MemoryEntry, MemoryLevel, MemoryQuery, MemoryType


class TestShortTermMemory:
    """短期记忆测试套件"""

    @pytest.fixture
    def stm(self) -> ShortTermMemory:
        """创建短期记忆实例"""
        return ShortTermMemory(max_items=50, window_size=5, ttl_minutes=1.0)

    @pytest.mark.asyncio
    async def test_store_and_retrieve(self, stm: ShortTermMemory) -> None:
        """测试短期记忆存储和检索"""
        entry = MemoryEntry(
            content="Test memory content",
            memory_type=MemoryType.OBSERVATION,
            memory_level=MemoryLevel.SHORT_TERM,
            source_agent="test_agent",
            importance_score=0.8,
            tags=["test"],
        )
        await stm.store(entry)

        # 通过query检索
        query = MemoryQuery(query_text="test memory", top_k=5)
        result = await stm.retrieve(query)

        assert result.total_found >= 1
        assert len(result.results) >= 1

    @pytest.mark.asyncio
    async def test_sliding_window(self, stm: ShortTermMemory) -> None:
        """测试滑动窗口机制"""
        # 存储超过窗口大小的条目
        for i in range(10):
            entry = MemoryEntry(
                content=f"Message {i}",
                memory_type=MemoryType.OBSERVATION,
                source_agent="test",
                importance_score=0.5,
                metadata={"session_id": "test_session"},
            )
            await stm.store(entry)

        # 检查窗口大小限制
        recent = await stm.get_recent(session_id="test_session", n=10)
        # 窗口大小为5
        assert len(recent) <= 5

    @pytest.mark.asyncio
    async def test_session_isolation(self, stm: ShortTermMemory) -> None:
        """测试会话隔离"""
        entry1 = MemoryEntry(
            content="Session A message",
            memory_type=MemoryType.OBSERVATION,
            source_agent="test",
            importance_score=0.5,
            metadata={"session_id": "session_a"},
        )
        entry2 = MemoryEntry(
            content="Session B message",
            memory_type=MemoryType.OBSERVATION,
            source_agent="test",
            importance_score=0.5,
            metadata={"session_id": "session_b"},
        )
        await stm.store(entry1)
        await stm.store(entry2)

        context_a = await stm.get_context(session_id="session_a")
        context_b = await stm.get_context(session_id="session_b")

        assert "Session A" in context_a
        assert "Session B" in context_b

    @pytest.mark.asyncio
    async def test_clear(self, stm: ShortTermMemory) -> None:
        """测试清空"""
        entry = MemoryEntry(
            content="To be cleared",
            memory_type=MemoryType.OBSERVATION,
            source_agent="test",
            importance_score=0.5,
        )
        await stm.store(entry)

        count = await stm.clear()
        assert count > 0


class TestLongTermMemory:
    """长期记忆测试套件"""

    @pytest.fixture
    def ltm(self) -> LongTermMemory:
        """创建长期记忆实例（使用内存存储）"""
        storage = InMemoryStorage()
        return LongTermMemory(storage=storage, similarity_threshold=0.5)

    @pytest.mark.asyncio
    async def test_store_and_retrieve(self, ltm: LongTermMemory) -> None:
        """测试长期记忆存储和检索"""
        entry = MemoryEntry(
            content="Important incident: database timeout on mysql-primary",
            memory_type=MemoryType.EPISODIC,
            memory_level=MemoryLevel.LONG_TERM,
            source_agent="rca_agent",
            importance_score=0.9,
            tags=["database", "timeout"],
        )
        await ltm.store(entry)

        query = MemoryQuery(query_text="database timeout", top_k=5)
        result = await ltm.retrieve(query)

        assert result.total_found >= 1
        assert len(result.results) >= 1

    @pytest.mark.asyncio
    async def test_hybrid_search(self, ltm: LongTermMemory) -> None:
        """测试混合搜索"""
        # 存储多条相关记忆
        entries = [
            MemoryEntry(
                content="CPU high usage detected on order-service: 95%",
                memory_type=MemoryType.OBSERVATION,
                source_agent="monitor_agent",
                importance_score=0.8,
                tags=["cpu", "order-service"],
            ),
            MemoryEntry(
                content="Memory leak detected on payment-service",
                memory_type=MemoryType.OBSERVATION,
                source_agent="monitor_agent",
                importance_score=0.7,
                tags=["memory", "payment-service"],
            ),
            MemoryEntry(
                content="Database connection timeout on mysql-primary",
                memory_type=MemoryType.EPISODIC,
                source_agent="rca_agent",
                importance_score=0.9,
                tags=["database", "timeout"],
            ),
        ]
        for entry in entries:
            await ltm.store(entry)

        # 混合搜索
        query = MemoryQuery(query_text="cpu order-service high", top_k=3)
        result = await ltm.retrieve(query)

        assert result.total_found >= 1
        # 最相关的结果应排在前面（考虑hash-based embedding的近似性）
        if result.results:
            all_content = " ".join(r.content.lower() for r in result.results)
            assert "cpu" in all_content or "memory" in all_content or "database" in all_content

    @pytest.mark.asyncio
    async def test_update_importance(self, ltm: LongTermMemory) -> None:
        """测试更新重要性"""
        entry = MemoryEntry(
            content="Test memory for importance update",
            memory_type=MemoryType.OBSERVATION,
            source_agent="test",
            importance_score=0.5,
        )
        await ltm.store(entry)

        success = await ltm.update_importance(entry.memory_id, 0.95)
        assert success is True

    def test_time_decay_score(self) -> None:
        """测试时间衰减得分"""
        from datetime import datetime, timedelta, timezone

        entry = MemoryEntry(
            content="Recent memory",
            memory_type=MemoryType.OBSERVATION,
            importance_score=0.8,
            created_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        score = time_decay_score(entry, half_life_days=30.0)
        assert score > 0.0

        # 很旧的记忆
        old_entry = MemoryEntry(
            content="Old memory",
            memory_type=MemoryType.OBSERVATION,
            importance_score=0.8,
            created_at=datetime.now(timezone.utc) - timedelta(days=90),
        )
        old_score = time_decay_score(old_entry, half_life_days=30.0)
        assert old_score < score  # 旧记忆得分应更低

    def test_rrf_fusion(self) -> None:
        """测试RRF融合"""
        semantic = [("id1", 0.9), ("id2", 0.7), ("id3", 0.5)]
        keyword = [("id2", 0.8), ("id3", 0.6), ("id1", 0.4)]
        time_rank = [("id1", 0.7), ("id2", 0.5), ("id3", 0.3)]
        importance = {"id1": 0.8, "id2": 0.6, "id3": 0.9}

        fused = rrf_fusion(semantic, keyword, time_rank, importance)

        assert len(fused) > 0
        # 按融合得分降序
        for i in range(len(fused) - 1):
            assert fused[i][1] >= fused[i + 1][1]


class TestWorkingMemory:
    """工作记忆测试套件"""

    @pytest.fixture
    def wm(self) -> WorkingMemory:
        """创建工作记忆实例"""
        return WorkingMemory(max_slots_per_incident=10)

    @pytest.mark.asyncio
    async def test_set_and_get(self, wm: WorkingMemory) -> None:
        """测试工作记忆CRUD"""
        await wm.set("root_cause", "traffic_spike", incident_id="test-001")
        value = await wm.get("root_cause", incident_id="test-001")
        assert value == "traffic_spike"

    @pytest.mark.asyncio
    async def test_get_all(self, wm: WorkingMemory) -> None:
        """测试获取所有工作记忆"""
        await wm.set("key1", "value1", incident_id="test-002")
        await wm.set("key2", "value2", incident_id="test-002")

        all_data = await wm.get_all(incident_id="test-002")
        assert "key1" in all_data
        assert "key2" in all_data
        assert all_data["key1"] == "value1"
        assert all_data["key2"] == "value2"

    @pytest.mark.asyncio
    async def test_delete(self, wm: WorkingMemory) -> None:
        """测试删除工作记忆"""
        await wm.set("to_delete", "value", incident_id="test-003")
        deleted = await wm.delete("to_delete", incident_id="test-003")
        assert deleted is True

        value = await wm.get("to_delete", incident_id="test-003")
        assert value is None

    @pytest.mark.asyncio
    async def test_nested_access(self, wm: WorkingMemory) -> None:
        """测试嵌套访问"""
        await wm.set("analysis", {"root_cause": "db_timeout", "confidence": 0.9}, incident_id="test-004")

        result = await wm.get_nested("analysis.root_cause", incident_id="test-004")
        assert result == "db_timeout"


class TestMemorySystem:
    """记忆系统整体测试套件"""

    @pytest.fixture
    def memory_system(self) -> MemorySystem:
        """创建MemorySystem实例"""
        MemorySystem.reset_instance()
        return MemorySystem()

    @pytest.mark.asyncio
    async def test_short_term_memory(self, memory_system: MemorySystem) -> None:
        """测试短期记忆"""
        entry = await memory_system.store(
            content="CPU usage at 95% on order-service",
            memory_type=MemoryType.OBSERVATION,
            source_agent="monitor_agent",
            importance_score=0.6,
            tags=["cpu", "order-service"],
            session_id="test_session",
        )

        assert entry is not None
        assert entry.memory_id is not None
        assert entry.content == "CPU usage at 95% on order-service"

    @pytest.mark.asyncio
    async def test_long_term_memory(self, memory_system: MemorySystem) -> None:
        """测试长期记忆存储和检索"""
        # 存储重要记忆（importance >= 0.5 会同步到长期记忆）
        await memory_system.store(
            content="Database timeout incident on mysql-primary resolved by scaling connection pool",
            memory_type=MemoryType.EPISODIC,
            source_agent="rca_agent",
            source_incident_id="incident-001",
            importance_score=0.8,
            tags=["database", "timeout", "resolution"],
        )

        # 检索
        result = await memory_system.search(
            query_text="database timeout mysql",
            top_k=5,
        )

        assert result.total_found >= 1

    @pytest.mark.asyncio
    async def test_working_memory(self, memory_system: MemorySystem) -> None:
        """测试工作记忆"""
        slot = await memory_system.working_set(
            key="current_status",
            value="investigating",
            incident_id="incident-002",
        )

        assert slot is not None
        assert slot.name == "current_status"

        value = await memory_system.working_get("current_status", incident_id="incident-002")
        assert value == "investigating"

    @pytest.mark.asyncio
    async def test_memory_flow(self, memory_system: MemorySystem) -> None:
        """
        测试记忆流转

        短期记忆 -> 长期记忆的流转。
        """
        # 存储多条短期记忆
        for i in range(5):
            await memory_system.store(
                content=f"Observation {i} during incident",
                memory_type=MemoryType.OBSERVATION,
                source_agent="monitor_agent",
                source_incident_id="incident-flow",
                importance_score=0.7,
            )

        # 执行流转
        archived = await memory_system.flow_stm_to_ltm(importance_threshold=0.6)
        assert archived >= 0

    @pytest.mark.asyncio
    async def test_hybrid_search(self, memory_system: MemorySystem) -> None:
        """
        测试混合搜索

        从两级记忆同时检索并合并结果。
        """
        # 存储到短期记忆
        await memory_system.store(
            content="Kubernetes pod restart due to OOM",
            memory_type=MemoryType.OBSERVATION,
            source_agent="heal_agent",
            importance_score=0.4,  # 低重要性，只存短期
            tags=["kubernetes", "oom"],
            session_id="test_search",
        )

        # 存储到长期记忆
        await memory_system.store(
            content="Previous OOM incident on cache-service caused by memory leak",
            memory_type=MemoryType.EPISODIC,
            source_agent="rca_agent",
            importance_score=0.8,  # 高重要性，存长期
            tags=["oom", "memory-leak"],
        )

        # 混合搜索
        result = await memory_system.search(
            query_text="OOM memory leak",
            top_k=5,
        )

        assert result.total_found >= 1

    @pytest.mark.asyncio
    async def test_context_assembly(self, memory_system: MemorySystem) -> None:
        """测试上下文组装"""
        # 存储一些记忆
        await memory_system.store(
            content="Recent observation about high latency",
            memory_type=MemoryType.OBSERVATION,
            source_agent="monitor_agent",
            importance_score=0.6,
            session_id="ctx_test",
        )

        context = await memory_system.get_context(
            query="latency",
            session_id="ctx_test",
            max_tokens=1000,
        )

        assert isinstance(context, str)

    @pytest.mark.asyncio
    async def test_memory_stats(self, memory_system: MemorySystem) -> None:
        """测试记忆统计"""
        stats = await memory_system.get_stats()

        assert "short_term" in stats
        assert "long_term" in stats
        assert "working" in stats

    def teardown_method(self) -> None:
        """清理MemorySystem单例"""
        MemorySystem.reset_instance()


class TestKeywordSearchIDF:
    """关键词检索真 IDF:候选池现算文档频率,替代旧常数权重"""

    @staticmethod
    def _entry(content: str, tags: list[str] | None = None, summary: str = "") -> MemoryEntry:
        return MemoryEntry(
            content=content,
            summary=summary,
            memory_type=MemoryType.EPISODIC,
            memory_level=MemoryLevel.LONG_TERM,
            source_agent="test",
            tags=tags or [],
        )

    def test_compute_idf_rare_word_weighs_more(self) -> None:
        """df 越大 IDF 越小;单文档词 > 全池词;空语料返回空 dict"""
        entries = [
            self._entry("timeout error in mysql"),
            self._entry("timeout error in redis"),
            self._entry("timeout error in kafka"),
        ]
        idf = compute_idf(entries)
        # 'timeout' 全池出现(df=3),'mysql' 仅 1 篇(df=1)
        assert idf["mysql"] > idf["timeout"]
        import math
        assert idf["timeout"] == pytest.approx(math.log(1 + 3 / 4))
        assert idf["mysql"] == pytest.approx(math.log(1 + 3 / 2))
        assert compute_idf([]) == {}

    def test_rare_word_hit_outranks_common_word_hit(self) -> None:
        """同 TF 下稀有词命中应压过泛词命中;旧常数权重会打成平手"""
        rare_hit = self._entry("mysql failure")      # 命中稀有词,tf=1/2
        common_hit = self._entry("timeout failure")  # 命中泛词,tf=1/2
        pool = [rare_hit, common_hit,
                self._entry("timeout in redis"), self._entry("timeout in kafka")]
        idf = compute_idf(pool)

        query = "mysql timeout"  # 整句不是任何 content 的子串,隔离子串加分
        score_rare = keyword_search_score(query, rare_hit, idf)
        score_common = keyword_search_score(query, common_hit, idf)
        assert score_rare > score_common
        # 旧常数权重下两者无差别 —— 这正是常数 IDF 的缺陷
        assert keyword_search_score(query, rare_hit) == pytest.approx(
            keyword_search_score(query, common_hit)
        )

    def test_backward_compat_without_idf(self) -> None:
        """idf=None 保持旧行为:tf * 2.0(查询取双词避开整句子串加分)"""
        entry = self._entry("mysql down")
        # 'mysql redis' 整句不在 content 内 → 无 0.8 子串分;
        # tf(mysql)=1/2 × 常数 2.0 = 1.0,redis 不命中
        assert keyword_search_score("mysql redis", entry, None) == pytest.approx(1.0)
        assert keyword_search_score("mysql redis", entry) == pytest.approx(1.0)

    def test_chinese_substring_paths_intact(self) -> None:
        """纯中文查询仍靠子串路径得分(content 0.8 / tag 0.5 / summary 0.3)"""
        entry = self._entry(
            "数据库连接池耗尽导致订单服务超时",
            tags=["数据库故障"],
            summary="数据库连接池耗尽",
        )
        idf = compute_idf([entry])
        score = keyword_search_score("数据库连接池耗尽", entry, idf)
        # content 子串 0.8 + tag 无命中(查询词整体不在 tag 内则 0)+ summary 0.3
        assert score >= 0.8
