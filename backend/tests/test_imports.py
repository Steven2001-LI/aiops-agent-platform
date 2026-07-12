"""
AIOps Agent Platform - Module Import Tests

验证所有模块可以正确导入和初始化。
"""

from __future__ import annotations


# ============================================================================
# Agents Import Tests
# ============================================================================

class TestImportAgents:
    """Agent模块导入测试"""

    def test_import_base_agent(self):
        """导入BaseAgent"""
        from app.agents.base import AgentResult, BaseAgent
        assert AgentResult is not None
        assert BaseAgent is not None

    def test_import_orchestrator(self):
        """导入Orchestrator"""
        from app.agents.orchestrator import Orchestrator
        assert Orchestrator is not None

    def test_import_monitor_agent(self):
        """导入MonitorAgent"""
        from app.agents.monitor_agent import MonitorAgent, MetricInput
        assert MonitorAgent is not None
        assert MetricInput is not None

    def test_import_rca_agent(self):
        """导入RCAAgent"""
        from app.agents.rca_agent import RCAAgent, RCAInput
        assert RCAAgent is not None
        assert RCAInput is not None

    def test_import_heal_agent(self):
        """导入HealAgent"""
        from app.agents.heal_agent import HealAgent, HealInput
        assert HealAgent is not None
        assert HealInput is not None

    def test_import_change_agent(self):
        """导入ChangeAgent"""
        from app.agents.change_agent import ChangeAgent, ChangeInput
        assert ChangeAgent is not None
        assert ChangeInput is not None

    def test_import_memory_agent(self):
        """导入MemoryAgent"""
        from app.agents.memory_agent import MemoryAgent, MemoryInput
        assert MemoryAgent is not None
        assert MemoryInput is not None

    def test_import_eval_agent(self):
        """导入EvalAgent"""
        from app.agents.eval_agent import EvalAgent, EvalInput
        assert EvalAgent is not None
        assert EvalInput is not None

    def test_import_all_from_init(self):
        """从agents/__init__.py导入所有导出"""
        from app.agents import (
            BaseAgent,
            Orchestrator,
            MonitorAgent,
            RCAAgent,
            HealAgent,
            ChangeAgent,
            MemoryAgent,
            EvalAgent,
        )
        assert all(v is not None for v in [
            BaseAgent, Orchestrator, MonitorAgent, RCAAgent,
            HealAgent, ChangeAgent, MemoryAgent, EvalAgent,
        ])


# ============================================================================
# Memory Import Tests
# ============================================================================

class TestImportMemory:
    """记忆系统模块导入测试"""

    def test_import_memory_system(self):
        """导入MemorySystem"""
        from app.memory.core import MemorySystem
        assert MemorySystem is not None

    def test_import_short_term_memory(self):
        """导入ShortTermMemory"""
        from app.memory.short_term import ShortTermMemory
        assert ShortTermMemory is not None

    def test_import_long_term_memory(self):
        """导入LongTermMemory"""
        from app.memory.long_term import LongTermMemory
        assert LongTermMemory is not None

    def test_import_working_memory(self):
        """导入WorkingMemory"""
        from app.memory.working_memory import WorkingMemory
        assert WorkingMemory is not None

    def test_import_storage(self):
        """导入存储层"""
        from app.memory.storage import InMemoryStorage, ChromaDBStorage
        assert InMemoryStorage is not None
        assert ChromaDBStorage is not None

    def test_import_all_from_init(self):
        """从memory/__init__.py导入所有导出"""
        from app.memory import (
            MemorySystem,
            ShortTermMemory,
            LongTermMemory,
            WorkingMemory,
            InMemoryStorage,
        )
        assert all(v is not None for v in [
            MemorySystem, ShortTermMemory, LongTermMemory,
            WorkingMemory, InMemoryStorage,
        ])


# ============================================================================
# Evaluation Import Tests
# ============================================================================

class TestImportEvaluation:
    """评估框架模块导入测试"""

    def test_import_evaluation_framework(self):
        """导入EvaluationFramework"""
        from app.evaluation.core import EvaluationFramework
        assert EvaluationFramework is not None

    def test_import_end_to_end_evaluator(self):
        """导入EndToEndEvaluator"""
        from app.evaluation.end_to_end import EndToEndEvaluator
        assert EndToEndEvaluator is not None

    def test_import_reasoning_evaluator(self):
        """导入ReasoningEvaluator"""
        from app.evaluation.reasoning_eval import ReasoningEvaluator
        assert ReasoningEvaluator is not None

    def test_import_tool_call_evaluator(self):
        """导入ToolCallEvaluator"""
        from app.evaluation.tool_call_eval import ToolCallEvaluator
        assert ToolCallEvaluator is not None

    def test_import_rag_evaluator(self):
        """导入RAGEvaluator"""
        from app.evaluation.rag_eval import RAGEvaluator
        assert RAGEvaluator is not None

    def test_import_all_from_init(self):
        """从evaluation/__init__.py导入所有导出"""
        from app.evaluation import EvaluationFramework
        assert EvaluationFramework is not None


# ============================================================================
# Tools Import Tests
# ============================================================================

class TestImportTools:
    """工具层模块导入测试"""

    def test_import_base_tool(self):
        """导入BaseTool"""
        from app.tools.base import BaseTool, ToolResult
        assert BaseTool is not None
        assert ToolResult is not None

    def test_import_metrics_tools(self):
        """导入MetricsQueryTool"""
        from app.tools.metrics_tools import QueryMetricsTool, DetectAnomaliesTool
        assert QueryMetricsTool is not None
        assert DetectAnomaliesTool is not None

    def test_import_knowledge_tools(self):
        """导入KnowledgeGraphTool"""
        from app.tools.knowledge_tools import QueryKnowledgeBaseTool, QueryTopologyTool
        assert QueryKnowledgeBaseTool is not None
        assert QueryTopologyTool is not None

    def test_import_playbook_tools(self):
        """导入PlaybookTool"""
        from app.tools.playbook_tools import GetPlaybookTool, ListPlaybooksTool
        assert GetPlaybookTool is not None
        assert ListPlaybooksTool is not None

    def test_import_memory_tools(self):
        """导入MemoryTool"""
        from app.tools.memory_tools import StoreMemoryTool, RetrieveMemoryTool
        assert StoreMemoryTool is not None
        assert RetrieveMemoryTool is not None

    def test_import_eval_tools(self):
        """导入EvalTool"""
        from app.tools.eval_tools import EvaluateOutputTool, LogFeedbackTool
        assert EvaluateOutputTool is not None
        assert LogFeedbackTool is not None


# ============================================================================
# Models Import Tests
# ============================================================================

class TestImportModels:
    """数据模型导入测试"""

    def test_import_events(self):
        """导入事件模型"""
        from app.models.events import (
            AlertEvent, RCAEvent, HealEvent, ChangeEvent,
            SeverityLevel, EventStatus, ApprovalStatus,
        )
        assert all(v is not None for v in [
            AlertEvent, RCAEvent, HealEvent, ChangeEvent,
            SeverityLevel, EventStatus, ApprovalStatus,
        ])

    def test_import_incident(self):
        """导入故障模型"""
        from app.models.incident import Incident, IncidentState
        assert Incident is not None
        assert IncidentState is not None

    def test_import_agent_models(self):
        """导入Agent模型"""
        from app.models.agent import AgentState, AgentStatus, AgentType
        assert all(v is not None for v in [AgentState, AgentStatus, AgentType])

    def test_import_memory_models(self):
        """导入记忆模型"""
        from app.models.memory import MemoryEntry, MemoryType, MemoryLevel
        assert all(v is not None for v in [MemoryEntry, MemoryType, MemoryLevel])

    def test_import_evaluation_models(self):
        """导入评估模型"""
        from app.models.evaluation import EvaluationResult, EvaluationType, EvaluationStatus
        assert all(v is not None for v in [EvaluationResult, EvaluationType, EvaluationStatus])


# ============================================================================
# Services Import Tests
# ============================================================================

class TestImportServices:
    """服务层导入测试"""

    def test_import_incident_service(self):
        """导入IncidentService"""
        from app.services.incident_service import IncidentService
        assert IncidentService is not None

    def test_import_knowledge_service(self):
        """导入KnowledgeService"""
        from app.services.knowledge_service import KnowledgeService
        assert KnowledgeService is not None

    def test_import_langfuse_service(self):
        """导入LangfuseService"""
        from app.services.langfuse_service import LangfuseService
        assert LangfuseService is not None


# ============================================================================
# Utils Import Tests
# ============================================================================

class TestImportUtils:
    """工具模块导入测试"""

    def test_import_logging(self):
        """导入日志工具"""
        from app.utils.logging import get_logger, configure_logging
        assert get_logger is not None
        assert configure_logging is not None

    def test_import_time_series(self):
        """导入时间序列工具"""
        from app.utils.time_series import TimeSeriesAnalyzer
        assert TimeSeriesAnalyzer is not None

    def test_import_helpers(self):
        """导入辅助工具"""
        from app.utils.helpers import safe_json_loads
        assert safe_json_loads is not None


# ============================================================================
# Config Import Tests
# ============================================================================

class TestImportConfig:
    """配置模块导入测试"""

    def test_import_config(self):
        """导入配置"""
        from app.config import AppConfig, get_config, LLMConfig, DatabaseConfig
        assert AppConfig is not None
        assert get_config is not None

    def test_get_config(self):
        """测试获取配置"""
        from app.config import get_config
        config = get_config()
        assert config is not None
