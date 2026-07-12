"""
AIOps Agent Platform - pytest Configuration and Shared Fixtures

提供所有测试共享的fixtures和配置。
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from typing import Any

import pytest

# Ensure the app module is importable
import pathlib
_BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_ROOT))


# =============================================================================
# Event Loop Policy
# =============================================================================

@pytest.fixture(scope="session")
def event_loop_policy():
    """设置事件循环策略"""
    return asyncio.DefaultEventLoopPolicy()


# =============================================================================
# FastAPI Test Client
# =============================================================================

@pytest.fixture
def client():
    """FastAPI TestClient fixture"""
    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app)


# =============================================================================
# Agent Fixtures
# =============================================================================

@pytest.fixture
def agent_context():
    """创建测试用的 AgentExecutionContext"""
    from app.models.agent import AgentExecutionContext

    return AgentExecutionContext(
        incident_id="test-incident-001",
        input_data={"test": True},
    )


# =============================================================================
# Event Fixtures
# =============================================================================

@pytest.fixture
def sample_alert():
    """创建测试用的 AlertEvent"""
    from app.models.events import AlertEvent, SeverityLevel

    return AlertEvent(
        service="order-service",
        metric="cpu_usage_percent",
        value=95.0,
        threshold=80.0,
        severity=SeverityLevel.HIGH,
        labels={"environment": "production", "tier": "critical"},
        annotations={"description": "CPU usage is very high"},
    )


@pytest.fixture
def sample_alert_normal():
    """创建正常指标的 AlertEvent"""
    from app.models.events import AlertEvent, SeverityLevel

    return AlertEvent(
        service="order-service",
        metric="cpu_usage_percent",
        value=50.0,
        threshold=80.0,
        severity=SeverityLevel.INFO,
        labels={"environment": "production", "tier": "standard"},
    )


@pytest.fixture
def sample_rca_event():
    """创建测试用的 RCAEvent"""
    from app.models.events import RCAEvent

    return RCAEvent(
        incident_id="test-incident-001",
        root_cause="traffic_spike",
        confidence=0.85,
        impact_chain=["order-service", "api-gateway"],
        evidence={
            "alert_metric": "cpu_usage_percent",
            "alert_value": 95.0,
            "affected_services": ["order-service"],
            "critical_services_affected": ["order-service"],
        },
        recommended_actions=["scale_up_resources", "enable_rate_limiting"],
    )


@pytest.fixture
def sample_heal_event():
    """创建测试用的 HealEvent"""
    from app.models.events import HealEvent

    return HealEvent(
        incident_id="test-incident-001",
        action="scale_up",
        action_category="pb_high_cpu",
        level="L0",
        target_resource="order-service",
        dry_run=True,
        dry_run_result={
            "all_executable": True,
            "results": [],
            "playbook_matched": "pb_high_cpu",
            "blast_radius_info": {
                "blast_radius_ratio": 0.05,
                "affected_service_count": 1,
                "total_service_count": 8,
            },
        },
        requires_approval=False,
    )


# =============================================================================
# Memory System Fixtures
# =============================================================================

@pytest.fixture
async def memory_system():
    """记忆系统fixture（自动清理）"""
    from app.memory.core import MemorySystem

    # Reset singleton before creating new instance
    MemorySystem.reset_instance()
    ms = MemorySystem()
    yield ms
    await ms.short_term.clear()
    MemorySystem.reset_instance()


@pytest.fixture
def mock_incident():
    """模拟故障数据"""
    return {
        "service": "order-service",
        "metric": "cpu_usage_percent",
        "metric_value": 95.0,
        "severity": "high",
    }


# =============================================================================
# MetricInput Fixtures for MonitorAgent
# =============================================================================

@pytest.fixture
def normal_metric():
    """正常指标数据"""
    from app.agents.monitor_agent import MetricInput

    return MetricInput(
        metric_name="cpu_usage_percent",
        metric_value=45.0,
        service_name="order-service",
        history_values=[40.0, 42.0, 43.0, 41.0, 44.0, 42.0, 45.0, 43.0, 41.0, 42.0],
    )


@pytest.fixture
def spike_metric():
    """突变指标数据"""
    from app.agents.monitor_agent import MetricInput

    return MetricInput(
        metric_name="cpu_usage_percent",
        metric_value=95.0,
        service_name="order-service",
        history_values=[40.0, 42.0, 43.0, 41.0, 44.0, 42.0, 45.0, 43.0, 41.0, 42.0],
        labels={"environment": "production", "tier": "critical"},
    )


@pytest.fixture
def trend_metric():
    """趋势异常指标数据"""
    from app.agents.monitor_agent import MetricInput

    return MetricInput(
        metric_name="memory_usage_percent",
        metric_value=92.0,
        service_name="payment-service",
        history_values=[55.0, 58.0, 62.0, 65.0, 68.0, 72.0, 76.0, 80.0, 85.0, 88.0],
        labels={"environment": "production", "tier": "critical"},
    )
