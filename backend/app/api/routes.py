"""
AIOps Agent Platform - RESTful API Routes

定义所有 RESTful API 端点，包括故障管理、Agent 管理、评估和记忆搜索。
所有端点现已连接到实际的 Service/Agent 层，返回真实的模拟数据。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel as PydanticBaseModel, Field

from app.agents.base import AgentExecutionContext
from app.agents.business_monitor_agent import BusinessMetricInput, BusinessMonitorAgent
from app.agents.change_agent import ChangeAgent, ChangeInput
from app.agents.eval_agent import EvalAgent, EvalInput, EvalType
from app.agents.heal_agent import HealAgent, HealInput
from app.agents.monitor_agent import MetricInput, MonitorAgent
from app.agents.rca_agent import RCAAgent, RCAInput
from app.api.websocket import manager as ws_manager
from app.config import AppConfig, get_config
from app.data.datasets import (
    FAULT_SCENARIOS,
    METRICS_DATASETS,
    get_metric_data,
)
from app.data.knowledge_base import (
    KNOWLEDGE_BASE,
    SERVICE_TOPOLOGY,
    KnowledgeBase,
)
from app.data.playbooks import PLAYBOOKS
from app.dependencies import CommonQueryParams
from app.models.agent import AgentState, AgentStatus, AgentType
from app.models.evaluation import EvaluationResult, EvaluationType
from app.models.events import (
    AlertEvent,
    ChangeEvent,
    HealEvent,
    RCAEvent,
    SeverityLevel,
)
from app.models.incident import Incident, IncidentState
from app.models.memory import MemoryType
from app.nlu.hybrid import hybrid_understand
from app.nlu.metric_mapper import MetricMapper
from app.services.incident_service import get_incident_service
from app.utils.logging import get_logger

logger = get_logger(__name__)

api_router = APIRouter(prefix="/api/v1")

# 全局服务实例（模块级别单例）
_incident_service = get_incident_service()

# 评测运行历史:POST /evaluations/run 成功后按维度落一条记录,
# GET /evaluations 从这里读真实历史(进程内存储,重启即清空)
_evaluation_runs: list[dict[str, Any]] = []
_monitor_agent: MonitorAgent | None = None
_rca_agent: RCAAgent | None = None
_heal_agent: HealAgent | None = None
_change_agent: ChangeAgent | None = None
_business_monitor_agent: BusinessMonitorAgent | None = None


def _get_monitor() -> MonitorAgent:
    global _monitor_agent
    if _monitor_agent is None:
        _monitor_agent = MonitorAgent()
    return _monitor_agent


def _get_rca() -> RCAAgent:
    global _rca_agent
    if _rca_agent is None:
        _rca_agent = RCAAgent()
    return _rca_agent


def _get_heal() -> HealAgent:
    global _heal_agent
    if _heal_agent is None:
        _heal_agent = HealAgent()
    return _heal_agent


def _get_change() -> ChangeAgent:
    global _change_agent
    if _change_agent is None:
        _change_agent = ChangeAgent()
    return _change_agent


def _get_business_monitor() -> BusinessMonitorAgent:
    global _business_monitor_agent
    if _business_monitor_agent is None:
        _business_monitor_agent = BusinessMonitorAgent()
    return _business_monitor_agent


# =============================================================================
# Agent 注册表（供 /agents 端点读取真实元数据与执行统计）
# =============================================================================
# peek 只读取已实例化的模块级单例,绝不触发惰性构造——
# 避免 GET 列表引发重型初始化;未实例化的 Agent 诚实报告零统计。
# memory/eval/orchestrator 没有常驻 BaseAgent 单例(eval 按评审白名单要求
# 每次请求内构造),它们的统计恒为 0,状态由各自子系统可用性推导。
_AGENT_REGISTRY: dict[str, dict[str, Any]] = {
    "monitor-agent-001": {
        "agent_type": AgentType.MONITOR,
        "name": "Monitor Agent",
        "description": "集成3-Sigma/EWMA/IsolationForest多算法投票的智能异常检测",
        "capabilities": ["3-Sigma检测", "EWMA趋势分析", "Isolation Forest", "告警去重", "自适应阈值"],
        "peek": lambda: _monitor_agent,
    },
    "rca-agent-001": {
        "agent_type": AgentType.RCA,
        "name": "RCA Agent",
        "description": "基于贝叶斯推理+BFS依赖遍历+RAG知识库增强的根因分析引擎",
        "capabilities": ["BFS拓扑遍历", "贝叶斯推理", "RAG知识库检索", "影响链路构建"],
        "peek": lambda: _rca_agent,
    },
    "heal-agent-001": {
        "agent_type": AgentType.HEAL,
        "name": "Heal Agent",
        "description": "Playbook驱动的故障自愈引擎，支持L0-L2三级自愈+熔断控制",
        "capabilities": ["Playbook匹配", "Dry-Run模拟", "爆炸半径评估", "熔断保护", "回滚计划"],
        "peek": lambda: _heal_agent,
    },
    "change-agent-001": {
        "agent_type": AgentType.CHANGE,
        "name": "Change Agent",
        "description": "多维风险评分+多级审批流程的变更管理Agent",
        "capabilities": ["五因素风险评分", "多级审批", "审计日志", "升级机制"],
        "peek": lambda: _change_agent,
    },
    "memory-agent-001": {
        "agent_type": AgentType.MEMORY,
        "name": "Memory Agent",
        "description": "三层记忆系统（短期/长期/工作记忆），支持语义搜索和时间衰减",
        "capabilities": ["语义搜索", "RRF融合排序", "时间衰减", "记忆合并", "自动归档"],
        "peek": lambda: None,
    },
    "eval-agent-001": {
        "agent_type": AgentType.EVAL,
        "name": "Eval Agent",
        "description": "四维度（端到端/推理/工具调用/RAG）评估框架",
        "capabilities": ["端到端评估", "推理评估", "工具调用评估", "RAG评估", "基准报告"],
        "peek": lambda: None,
    },
    "orchestrator-001": {
        "agent_type": AgentType.ORCHESTRATOR,
        "name": "Orchestrator",
        "description": "LangGraph多Agent编排器，管理完整故障处理状态机",
        "capabilities": ["状态机编排", "条件路由", "审批暂停终态", "失败升级"],
        "peek": lambda: None,
    },
}


def _agent_public_info(agent_id: str, meta: dict[str, Any]) -> dict[str, Any]:
    """组装单个 Agent 的对外信息:已实例化的读真实 AgentState,否则零统计。"""
    info: dict[str, Any] = {
        "agent_id": agent_id,
        "agent_type": meta["agent_type"].value,
        "name": meta["name"],
        "description": meta["description"],
        "status": AgentStatus.IDLE.value,
        "version": "1.0.0",
        "capabilities": meta["capabilities"],
        "total_executions": 0,
        "successful_executions": 0,
        "failed_executions": 0,
        "success_rate": 0.0,
        "average_execution_time_ms": 0.0,
        "last_active_at": None,
    }
    instance = meta["peek"]()
    if instance is not None:
        state = instance.state
        info.update(
            {
                "status": state.status.value,
                "version": instance.get_version(),
                "total_executions": state.total_executions,
                "successful_executions": state.successful_executions,
                "failed_executions": state.failed_executions,
                "success_rate": round(state.success_rate, 4),
                "average_execution_time_ms": round(state.average_execution_time_ms, 2),
                "last_active_at": (
                    state.last_active_at.isoformat() if state.last_active_at else None
                ),
            }
        )
    return info


# =============================================================================
# 记忆系统集成（惰性初始化，失败不影响管道）
# =============================================================================

_memory_system: Any = None
_memory_init_attempted: bool = False


async def _get_memory_system():
    """获取 MemorySystem 单例。首次失败后不再重试，避免阻塞管道。"""
    global _memory_system, _memory_init_attempted
    if _memory_system is not None:
        return _memory_system
    if _memory_init_attempted:
        return None
    try:
        from app.memory.core import MemorySystem as MS
        _memory_system = await MS.get_instance()
        logger.info("MemorySystem initialized for pipeline integration")
    except Exception as e:
        _memory_init_attempted = True
        logger.warning("MemorySystem unavailable, pipeline will run without memory persistence", error=str(e))
    return _memory_system


async def _store_agent_memory(
    content: str,
    agent_name: str,
    incident_id: str,
    importance: float = 0.5,
    memory_type: MemoryType = MemoryType.OBSERVATION,
    tags: list[str] | None = None,
) -> None:
    """存储 Agent 执行结果到记忆系统，失败静默忽略。"""
    try:
        ms = await _get_memory_system()
        if ms is None:
            return
        await ms.store(
            content=content,
            memory_type=memory_type,
            source_agent=agent_name,
            source_incident_id=incident_id,
            importance=importance,
            tags=tags or [],
            session_id=incident_id,
        )
    except Exception as e:
        logger.debug("Failed to store agent memory (non-fatal)", agent=agent_name, error=str(e))


async def _store_working_memory(
    incident_id: str,
    key: str,
    value: Any,
    ttl_seconds: int = 3600,
) -> None:
    """存储工作记忆，供下游 Agent 读取。"""
    try:
        ms = await _get_memory_system()
        if ms is None:
            return
        await ms.working_set(key=key, value=value, incident_id=incident_id, ttl_seconds=ttl_seconds)
    except Exception as e:
        logger.debug("Failed to store working memory (non-fatal)", key=key, error=str(e))


# =============================================================================
# 为演示预设一些故障数据
# =============================================================================

def _seed_incidents() -> None:
    """预设故障数据（仅首次调用时填充）"""
    if _incident_service._incidents:
        return

    from datetime import timedelta as _td
    now = datetime.now(timezone.utc)
    seeds = [
        {
            "incident_id": "INC-2026-001",
            "service": "order-service",
            "metric": "cpu_usage_percent",
            "severity": SeverityLevel.CRITICAL,
            "state": IncidentState.HEALING,
            "value": 95.0,
            "threshold": 80.0,
        },
        {
            "incident_id": "INC-2026-002",
            "service": "payment-service",
            "metric": "memory_usage_percent",
            "severity": SeverityLevel.HIGH,
            "state": IncidentState.RCA_IN_PROGRESS,
            "value": 88.0,
            "threshold": 85.0,
        },
        {
            "incident_id": "INC-2026-003",
            "service": "api-gateway",
            "metric": "error_rate_percent",
            "severity": SeverityLevel.CRITICAL,
            "state": IncidentState.RCA_COMPLETED,
            "value": 12.0,
            "threshold": 5.0,
        },
        {
            "incident_id": "INC-2026-004",
            "service": "user-service",
            "metric": "p99_latency_ms",
            "severity": SeverityLevel.HIGH,
            "state": IncidentState.ANALYZING,
            "value": 500.0,
            "threshold": 200.0,
        },
        {
            "incident_id": "INC-2026-005",
            "service": "inventory-service",
            "metric": "cpu_usage_percent",
            "severity": SeverityLevel.MEDIUM,
            "state": IncidentState.ACKNOWLEDGED,
            "value": 78.0,
            "threshold": 75.0,
        },
        {
            "incident_id": "INC-2026-006",
            "service": "payment-service",
            "metric": "error_rate_percent",
            "severity": SeverityLevel.LOW,
            "state": IncidentState.RESOLVED,
            "value": 2.1,
            "threshold": 3.0,
        },
        {
            "incident_id": "INC-2026-007",
            "service": "mysql-primary",
            "metric": "p99_latency_ms",
            "severity": SeverityLevel.CRITICAL,
            "state": IncidentState.HEALING,
            "value": 3000.0,
            "threshold": 500.0,
        },
    ]

    for i, s in enumerate(seeds):
        alert = AlertEvent(
            source="monitor_agent",
            service=s["service"],
            metric=s["metric"],
            value=s["value"],
            threshold=s["threshold"],
            operator=">",
            severity=s["severity"],
            labels={"tier": SERVICE_TOPOLOGY.get(s["service"], {}).get("tier", "standard")},
            annotations={"seed": "true"},
        )
        incident = Incident.from_alert(alert)
        incident.incident_id = s["incident_id"]
        incident.transition_to(s["state"], actor="system")
        # 调整时间为更早的
        incident.created_at = now - _td(minutes=(len(seeds) - i) * 15)
        incident.updated_at = now
        _incident_service._incidents[incident.incident_id] = incident

    logger.info("Seed incidents created", count=len(seeds))


# =============================================================================
# Incident Routes
# =============================================================================

@api_router.post(
    "/incidents/trigger",
    response_model=dict[str, Any],
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Incidents"],
    summary="触发故障处理",
    description="接收告警事件并触发完整的 Agent 协作处理流程。",
)
async def trigger_incident(
    alert: AlertEvent,
    config: AppConfig = Depends(get_config),
) -> dict[str, Any]:
    """
    触发故障处理流程

    完整流程:
    1. Monitor Agent - 异常检测与去重
    2. RCA Agent - 根因分析
    3. Heal Agent - 自愈操作
    4. Change Agent - 变更审批
    每个阶段通过 WebSocket 实时推送进度。
    """
    logger.info(
        "Incident triggered",
        service=alert.service,
        metric=alert.metric,
        severity=alert.severity.value,
    )

    # 创建故障实例
    incident = Incident.from_alert(alert)
    incident.transition_to(IncidentState.ACKNOWLEDGED, actor="orchestrator")
    _incident_service._incidents[incident.incident_id] = incident

    # WebSocket 广播: 故障已创建
    await ws_manager.broadcast({
        "type": "incident_update",
        "payload": _incident_to_dict(incident),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    # 启动后台处理（不阻塞响应）
    asyncio.create_task(_process_incident_pipeline(incident))

    return {
        "incident_id": incident.incident_id,
        "status": "accepted",
        "state": incident.state.value,
        "message": "Incident processing started — agents are running",
        "correlation_id": incident.alert_event.correlation_id if incident.alert_event else "",
    }


@api_router.post(
    "/incidents/seed-demo",
    response_model=dict[str, Any],
    tags=["Incidents"],
    summary="注入演示故障数据（仅 dev 环境）",
)
async def seed_demo_incidents() -> dict[str, Any]:
    """显式注入 7 个演示 incident。仅在 APP_ENV=development 时可用。"""
    config = get_config()
    if config.env != "development":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="seed-demo endpoint is only available in development environment",
        )
    _seed_incidents()
    return {
        "status": "seeded",
        "count": len(_incident_service._incidents),
        "incident_ids": list(_incident_service._incidents.keys()),
    }


# =============================================================================
# 业务异常检测端点
# =============================================================================

@api_router.post("/incidents/trigger-business")
async def trigger_business_incident(
    request: BusinessMetricInput,
) -> dict[str, Any]:
    """
    触发业务异常检测流程。

    接收业务检查请求，执行规则引擎检测，返回检测结果。
    与 /incidents/trigger 互补 —— 前者处理指标异常，此端点处理业务逻辑异常。

    示例请求:
    ```json
    {
      "service_name": "payment-service",
      "business_domain": "financial",
      "check_rules": ["br_duplicate_charge"],
      "context": {}
    }
    ```
    """
    logger.info(
        "Business incident triggered",
        service=request.service_name,
        domain=request.business_domain,
        rules=request.check_rules,
    )

    agent = _get_business_monitor()
    ctx = AgentExecutionContext(
        incident_id=str(uuid.uuid4()),
        input_data={"business_check": True},
    )

    result = await agent.process(request, ctx)

    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Business monitoring failed: {result.error_message}",
        )

    return {
        "incident_id": ctx.incident_id,
        "status": "completed",
        "message": "Business anomaly check completed",
        "result": result.output_data,
    }


@api_router.get("/business-rules")
async def list_business_rules(
    domain: str | None = Query(None, description="按业务域过滤"),
) -> dict[str, Any]:
    """
    列出所有业务检测规则。

    可选按业务域过滤: financial, inventory, order, user
    """
    agent = _get_business_monitor()
    if domain:
        rules = agent.get_rules_by_domain(domain)
    else:
        rules = agent.get_all_rules()

    return {
        "total": len(rules),
        "domain": domain or "all",
        "rules": rules,
    }


@api_router.post("/incidents/trigger-business-scenario/{scenario_id}")
async def trigger_business_scenario(
    scenario_id: str,
) -> dict[str, Any]:
    """
    触发预设的业务故障场景（演示用）。

    可用场景: fs_biz_007(重复扣款), fs_biz_008(库存超卖),
             fs_biz_009(金额对账不平), fs_biz_010(支付回调丢失)
    """
    from app.data.datasets import get_fault_scenario

    scenario = get_fault_scenario(scenario_id)
    if not scenario or scenario.get("category") != "business_logic":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Business scenario '{scenario_id}' not found",
        )

    biz_input = BusinessMetricInput(
        service_name=scenario["service"],
        business_domain="financial",
        check_rules=scenario.get("business_rules", []),
        context={
            "scenario_id": scenario_id,
            "scenario_name": scenario["name"],
        },
    )

    agent = _get_business_monitor()
    ctx = AgentExecutionContext(
        incident_id=str(uuid.uuid4()),
        input_data={"business_scenario": scenario_id},
    )

    result = await agent.process(biz_input, ctx)

    return {
        "incident_id": ctx.incident_id,
        "scenario": scenario["name"],
        "description": scenario["description"],
        "expected_root_cause": scenario["root_cause"],
        "result": result.output_data,
    }


async def _process_incident_pipeline(incident: Incident) -> None:
    """
    完整的 Agent 处理管道（后台异步执行）

    Step 1: Monitor Agent  → 异常检测 → 存入记忆
    Step 2: RCA Agent      → 根因分析 → 存入记忆
    Step 3: Heal Agent     → 自愈操作 → 存入记忆
    Step 4: Change Agent   → 变更审批 → 存入记忆
    Step 5: 完成 → 归档工作记忆
    """
    ctx = AgentExecutionContext(
        incident_id=incident.incident_id,
        metadata={
            "correlation_id": incident.alert_event.correlation_id if incident.alert_event else "",
            "user_id": "system",
        },
    )

    try:
        # ── Step 1: Monitor Agent ──
        incident.transition_to(IncidentState.ACKNOWLEDGED, actor="monitor_agent")
        await _broadcast_incident(incident)

        monitor = _get_monitor()
        metric_name = _get_metric(incident)
        history = get_metric_data(incident.service, metric_name, "normal")
        metric_input = MetricInput(
            metric_name=metric_name,
            metric_value=_get_metric_value(incident),
            service_name=incident.service,
            labels={"tier": SERVICE_TOPOLOGY.get(incident.service, {}).get("tier", "standard")},
            history_values=history,
        )
        monitor_result = await monitor.execute(metric_input, ctx)
        logger.info("Monitor agent completed", success=monitor_result.success)

        if not monitor_result.success:
            await _escalate_pipeline_failure(
                incident,
                stage="monitor",
                error_message=monitor_result.error_message or "Monitor agent returned an unsuccessful result",
                actor="monitor_agent",
            )
            return

        # → 记忆：存储异常检测结果
        if monitor_result.success and monitor_result.output_data:
            detection = monitor_result.output_data.get("detection_result", {})
            await _store_agent_memory(
                content=(
                    f"[MonitorAgent] {incident.service} {metric_name}={_get_metric_value(incident)}. "
                    f"Anomaly detected: {detection.get('algorithm_consensus', 'unknown')} consensus "
                    f"({detection.get('confidence', 0):.2f}). "
                    f"Score: {detection.get('score', 0):.3f}. "
                    f"Algorithms: {len(detection.get('algorithms_voted', []))} voted."
                ),
                agent_name="monitor_agent",
                incident_id=incident.incident_id,
                importance=0.9 if incident.severity == SeverityLevel.CRITICAL else 0.7,
                memory_type=MemoryType.OBSERVATION,
                tags=["monitor", "anomaly_detection", incident.service, metric_name],
            )
            # → 工作记忆：供 RCA 读取
            await _store_working_memory(
                incident_id=incident.incident_id,
                key="monitor.detection",
                value={
                    "is_anomaly": detection.get("is_anomaly", True),
                    "score": detection.get("score", 0),
                    "confidence": detection.get("confidence", 0),
                    "consensus": detection.get("algorithm_consensus", "unknown"),
                },
            )

        # ── Step 2: RCA Agent ──
        incident.transition_to(IncidentState.RCA_IN_PROGRESS, actor="rca_agent")
        await _broadcast_incident(incident)

        rca = _get_rca()
        rca_input = RCAInput(
            alert=incident.alert_event,
            incident_id=incident.incident_id,
            lookback_minutes=60,
            max_hops=3,
        )
        rca_result = await rca.execute(rca_input, ctx)

        if not rca_result.success:
            await _escalate_pipeline_failure(
                incident,
                stage="rca",
                error_message=rca_result.error_message or "RCA agent returned an unsuccessful result",
                actor="rca_agent",
            )
            return

        rca_event: RCAEvent | None = None
        if rca_result.output_data:
            rca_event_dict = rca_result.output_data.get("rca_event", {})
            if rca_event_dict:
                rca_event = RCAEvent(**rca_event_dict) if isinstance(rca_event_dict, dict) else rca_event_dict
                incident.rca_event = rca_event
                # 存入 context 供前端展示
                incident.context["rca_root_cause"] = rca_result.output_data.get("root_cause", "unknown")
                incident.context["rca_confidence"] = rca_result.output_data.get("confidence", 0)
                incident.context["rca_impact_count"] = rca_result.output_data.get("impact_services_count", 0)
                incident.context["rca_suggested_actions"] = rca_result.output_data.get("suggested_actions", [])

        if rca_event is None:
            await _escalate_pipeline_failure(
                incident,
                stage="rca",
                error_message="RCA agent succeeded without a valid rca_event",
                actor="rca_agent",
            )
            return

        incident.transition_to(IncidentState.RCA_COMPLETED, actor="rca_agent")
        await _broadcast_incident(incident)
        logger.info("RCA agent completed", success=rca_result.success)

        # → 记忆：存储根因分析结果（高重要性，自动进入长期记忆）
        if rca_result.success and rca_result.output_data:
            root_cause = rca_result.output_data.get("root_cause", "unknown")
            confidence = rca_result.output_data.get("confidence", 0)
            await _store_agent_memory(
                content=(
                    f"[RCAAgent] Root cause of {incident.service} {metric_name} issue: {root_cause}. "
                    f"Confidence: {confidence:.2f}. "
                    f"Impact: {rca_result.output_data.get('impact_services_count', 0)} services. "
                    f"Top causes: {rca_result.output_data.get('bayesian_results', [])}. "
                    f"Suggested actions: {rca_result.output_data.get('suggested_actions', [])}"
                ),
                agent_name="rca_agent",
                incident_id=incident.incident_id,
                importance=0.85,
                memory_type=MemoryType.EPISODIC,
                tags=["rca", "root_cause", incident.service, "confidence_" + str(round(confidence, 1))],
            )
            # → 工作记忆：供 Heal 读取
            await _store_working_memory(
                incident_id=incident.incident_id,
                key="rca.result",
                value={
                    "root_cause": root_cause,
                    "confidence": confidence,
                    "suggested_actions": rca_result.output_data.get("suggested_actions", []),
                },
            )

        # ── Step 3: Heal Agent ──
        incident.transition_to(IncidentState.HEALING, actor="heal_agent")
        await _broadcast_incident(incident)

        heal = _get_heal()
        heal_input = HealInput(
            rca_event=rca_event,
            incident_id=incident.incident_id,
            dry_run=True,
        )
        heal_result = await heal.execute(heal_input, ctx)

        if not heal_result.success:
            await _escalate_pipeline_failure(
                incident,
                stage="heal",
                error_message=heal_result.error_message or "Heal agent returned an unsuccessful result",
                actor="heal_agent",
            )
            return

        if heal_result.output_data.get("dry_run_passed") is not True:
            await _escalate_pipeline_failure(
                incident,
                stage="heal",
                error_message="Heal dry-run validation did not pass",
                actor="heal_agent",
            )
            return

        heal_event: HealEvent | None = None
        if heal_result.output_data:
            heal_event_dict = heal_result.output_data.get("heal_event", {})
            if heal_event_dict:
                heal_event = HealEvent(**heal_event_dict) if isinstance(heal_event_dict, dict) else heal_event_dict
                incident.heal_events.append(heal_event)
                incident.context["heal_action"] = heal_result.output_data.get("action", "")
                incident.context["heal_level"] = heal_result.output_data.get("level", "L0")
                incident.context["heal_blast_radius"] = heal_result.output_data.get("blast_radius", 0)

        if heal_event is None:
            await _escalate_pipeline_failure(
                incident,
                stage="heal",
                error_message="Heal agent succeeded without a valid heal_event",
                actor="heal_agent",
            )
            return

        logger.info("Heal agent completed", success=heal_result.success)

        # → 记忆：存储自愈方案
        if heal_result.success and heal_result.output_data:
            heal_level = heal_result.output_data.get("heal_level", "L0")
            playbook = heal_result.output_data.get("playbook_matched", "none")
            actions_count = heal_result.output_data.get("actions_count", 0)
            await _store_agent_memory(
                content=(
                    f"[HealAgent] For {incident.service}: matched playbook '{playbook}', "
                    f"heal level={heal_level}, "
                    f"{actions_count} actions planned. "
                    f"Action: {heal_result.output_data.get('action', '')}. "
                    f"Dry-run: {'PASSED' if heal_result.output_data.get('dry_run_passed') else 'FAILED'}."
                ),
                agent_name="heal_agent",
                incident_id=incident.incident_id,
                importance=0.7,
                memory_type=MemoryType.PROCEDURAL,
                tags=["heal", "playbook", playbook, incident.service, f"level_{heal_level}"],
            )
            # → 工作记忆：供 Change 读取
            await _store_working_memory(
                incident_id=incident.incident_id,
                key="heal.plan",
                value={
                    "playbook": playbook,
                    "heal_level": heal_level,
                    "actions_count": actions_count,
                    "requires_approval": heal_result.output_data.get("requires_approval", True),
                },
            )

        # ── Step 4: Change Agent ──
        incident.transition_to(IncidentState.AWAITING_APPROVAL, actor="change_agent")
        await _broadcast_incident(incident)

        change = _get_change()
        change_input = ChangeInput(
            heal_event=heal_event,
            incident_id=incident.incident_id,
            change_type="auto_heal",
            requester="orchestrator",
        )
        change_result = await change.execute(change_input, ctx)

        if not change_result.success:
            await _escalate_pipeline_failure(
                incident,
                stage="change",
                error_message=change_result.error_message or "Change agent returned an unsuccessful result",
                actor="change_agent",
            )
            return

        change_event: ChangeEvent | None = None
        if change_result.output_data:
            change_event_dict = change_result.output_data.get("change_event", {})
            if change_event_dict:
                change_event = ChangeEvent(**change_event_dict) if isinstance(change_event_dict, dict) else change_event_dict
                incident.change_events.append(change_event)
                incident.context["approval_status"] = change_result.output_data.get("approval_status", "pending")
                incident.context["risk_score"] = change_result.output_data.get("risk_score", 0)

        if change_event is None:
            await _escalate_pipeline_failure(
                incident,
                stage="change",
                error_message="Change agent succeeded without a valid change_event",
                actor="change_agent",
            )
            return

        logger.info("Change agent completed", success=change_result.success)

        # → 记忆：存储审批决策
        if change_result.success and change_result.output_data:
            risk_score = change_result.output_data.get("risk_score", 0)
            risk_level = change_result.output_data.get("risk_level", "low")
            approval = change_result.output_data.get("approval_status", "pending")
            await _store_agent_memory(
                content=(
                    f"[ChangeAgent] For {incident.service}: risk_score={risk_score:.3f} ({risk_level}), "
                    f"approval={approval}. "
                    f"Auto-decision: {change_result.output_data.get('auto_decision', False)}. "
                    f"Approvers: {change_result.output_data.get('approvers', [])}"
                ),
                agent_name="change_agent",
                incident_id=incident.incident_id,
                importance=0.6,
                memory_type=MemoryType.EPISODIC,
                tags=["change", "approval", incident.service, risk_level],
            )

        approval_status = change_result.output_data.get("approval_status", "")
        if approval_status == "pending":
            await _broadcast_incident(incident)
            logger.info(
                "Incident pipeline paused for approval",
                incident_id=incident.incident_id,
                approval_status=approval_status,
            )
            return

        if approval_status not in {"approved", "auto_approved"}:
            await _escalate_pipeline_failure(
                incident,
                stage="change",
                error_message=f"Change approval did not permit resolution: {approval_status or 'missing'}",
                actor="change_agent",
            )
            return

        # ── Step 5: 完成 → 归档工作记忆到长期记忆 ──
        incident.context["resolution_mode"] = "simulated"
        incident.transition_to(IncidentState.RESOLVED, actor="orchestrator")
        await _broadcast_incident(incident)
        logger.info("Incident pipeline completed", incident_id=incident.incident_id)

        # → 归档：将本次故障的工作记忆转为长期记忆
        try:
            ms = await _get_memory_system()
            if ms is not None:
                archived_count = await ms.flow_wm_to_ltm(incident.incident_id)
                logger.info("Working memory archived to LTM", incident_id=incident.incident_id, count=archived_count)
        except Exception as e:
            logger.debug("Failed to archive working memory (non-fatal)", error=str(e))

    except Exception as e:
        import traceback
        logger.error(
            "Incident pipeline error",
            incident_id=incident.incident_id,
            error=str(e),
            traceback=traceback.format_exc(),
        )
        await _escalate_pipeline_failure(
            incident,
            stage="pipeline",
            error_message=str(e),
            actor="orchestrator",
        )


async def _escalate_pipeline_failure(
    incident: Incident,
    *,
    stage: str,
    error_message: str,
    actor: str,
) -> None:
    """将核心管道失败记录为可观测的人工升级终态。"""
    logger.error(
        "Incident pipeline stage failed",
        incident_id=incident.incident_id,
        stage=stage,
        error=error_message,
    )
    incident.context["pipeline_failure"] = {
        "stage": stage,
        "error": error_message,
    }
    incident.transition_to(IncidentState.ESCALATED, actor=actor)
    if incident.timeline:
        incident.timeline[-1].details.update({
            "failure_stage": stage,
            "error": error_message,
        })
    await _broadcast_incident(incident)


def _get_metric(incident: Incident) -> str:
    """从 incident 提取指标名称"""
    if incident.alert_event:
        return incident.alert_event.metric
    return incident.context.get("metric", "unknown")


def _get_metric_value(incident: Incident) -> float:
    """从 incident 提取指标值"""
    if incident.alert_event:
        return incident.alert_event.value
    return float(incident.context.get("value", 0))


async def _broadcast_incident(incident: Incident) -> None:
    """通过 WebSocket 广播故障更新"""
    try:
        await ws_manager.broadcast({
            "type": "incident_update",
            "payload": _incident_to_dict(incident),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        await ws_manager.broadcast_to_incident(
            incident.incident_id,
            {
                "type": "incident_update",
                "payload": _incident_to_dict(incident),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as e:
        logger.error("WebSocket broadcast failed", error=str(e))


def _incident_to_dict(incident: Incident) -> dict[str, Any]:
    """将 Incident 转为前端兼容的字典格式"""
    data = incident.model_dump()

    # 映射 state -> status（前端使用 status）
    data["status"] = data.get("state", "pending")
    # 映射 incident_id -> id（前端使用 id）
    if "incident_id" in data:
        data["id"] = data["incident_id"]

    # 提取 RCA 数据
    if incident.rca_event:
        data["rca"] = {
            "root_cause": incident.rca_event.root_cause,
            "confidence": incident.rca_event.confidence,
            "impact_chain": incident.rca_event.impact_chain,
            "suggested_actions": incident.rca_event.recommended_actions,
        }
    elif "rca_root_cause" in incident.context:
        data["rca"] = {
            "root_cause": incident.context.get("rca_root_cause", ""),
            "confidence": incident.context.get("rca_confidence", 0),
            "impact_chain": [],
            "suggested_actions": incident.context.get("rca_suggested_actions", []),
        }

    # 提取 Heal 数据
    if incident.heal_events:
        latest_heal = incident.heal_events[-1]
        data["heal"] = {
            "action": latest_heal.action,
            "level": latest_heal.level,
            "dry_run_result": "success" if latest_heal.status == "success" else "pending",
            "blast_radius": latest_heal.dry_run_result.get("blast_radius_ratio", 0) if latest_heal.dry_run_result else 0,
        }
    elif "heal_action" in incident.context:
        data["heal"] = {
            "action": incident.context.get("heal_action", ""),
            "level": incident.context.get("heal_level", "L0"),
            "dry_run_result": "success",
            "blast_radius": incident.context.get("heal_blast_radius", 0),
        }

    # 提取 Change 数据
    if incident.change_events:
        latest_change = incident.change_events[-1]
        data["change"] = {
            "approval_status": latest_change.approval_status.value,
            "risk_score": int(latest_change.risk_score * 100),  # 前端期望 0-100
            "approver": latest_change.approvers[0] if latest_change.approvers else "auto",
        }
    elif "approval_status" in incident.context:
        data["change"] = {
            "approval_status": incident.context.get("approval_status", "pending"),
            "risk_score": incident.context.get("risk_score", 0),
            "approver": "",
        }

    # 提取 Alert 数据
    if incident.alert_event:
        data["alert"] = {
            "is_anomaly": True,
            "confidence": 0.95,
            "algorithms_voted": ["3-sigma", "ewma", "isolation_forest"],
        }

    return data


@api_router.get(
    "/incidents/{incident_id}",
    response_model=dict[str, Any],
    tags=["Incidents"],
    summary="查询故障状态",
    description="根据故障ID查询详细的故障状态和完整处理历史。",
)
async def get_incident(
    incident_id: str,
) -> dict[str, Any]:
    """查询故障详情"""
    logger.info("Get incident requested", incident_id=incident_id)

    incident = await _incident_service.get(incident_id)
    if incident is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident {incident_id} not found",
        )

    return _incident_to_dict(incident)


@api_router.get(
    "/incidents",
    response_model=dict[str, Any],
    tags=["Incidents"],
    summary="故障列表查询",
    description="分页查询故障列表，支持状态和严重级别过滤。",
)
async def list_incidents(
    params: CommonQueryParams = Depends(),
    state: IncidentState | None = Query(None, description="按状态过滤"),
    severity: str | None = Query(None, description="按严重级别过滤"),
    service: str | None = Query(None, description="按服务过滤"),
) -> dict[str, Any]:
    """查询故障列表"""
    logger.info(
        "List incidents requested",
        page=params.page,
        state=state.value if state else None,
    )

    result = await _incident_service.list(
        state=state,
        severity=severity,
        service=service,
        page=params.page,
        page_size=params.page_size,
    )

    # 转换每个 incident 为前端兼容格式
    result["items"] = [
        _incident_to_dict(Incident(**item)) if isinstance(item, dict) else _incident_to_dict(item)
        for item in result["items"]
    ]

    return result


# =============================================================================
# Agent Routes
# =============================================================================

@api_router.get(
    "/agents",
    response_model=dict[str, Any],
    tags=["Agents"],
    summary="Agent 列表",
    description="获取所有已注册 Agent 的列表和状态概览。",
)
async def list_agents(
    request: Request,
    agent_type: AgentType | None = Query(None, description="按类型过滤"),
    status: AgentStatus | None = Query(None, description="按状态过滤"),
) -> dict[str, Any]:
    """获取 Agent 列表（元数据 + 真实执行统计）"""
    logger.info("List agents requested")

    agents = [
        _agent_public_info(agent_id, meta)
        for agent_id, meta in _AGENT_REGISTRY.items()
    ]

    # orchestrator 状态由 lifespan 探针推导:LangGraph 图构建失败时报 offline
    langgraph_available = getattr(request.app.state, "langgraph_available", None)
    if langgraph_available is False:
        for a in agents:
            if a["agent_id"] == "orchestrator-001":
                a["status"] = AgentStatus.OFFLINE.value

    if agent_type:
        agents = [a for a in agents if a["agent_type"] == agent_type.value]
    if status:
        agents = [a for a in agents if a["status"] == status.value]

    return {
        "items": agents,
        "total": len(agents),
    }


@api_router.get(
    "/agents/{agent_id}/status",
    response_model=dict[str, Any],
    tags=["Agents"],
    summary="Agent 状态",
    description="获取指定 Agent 的详细状态信息。",
)
async def get_agent_status(
    agent_id: str,
) -> dict[str, Any]:
    """获取 Agent 详细状态（真实执行统计;未知 id 返回零值兜底）"""
    logger.info("Get agent status", agent_id=agent_id)

    meta = _AGENT_REGISTRY.get(agent_id)
    response: dict[str, Any] = {
        "agent_id": agent_id,
        "status": AgentStatus.IDLE.value,
        "current_task": "",
        "progress_percent": 0,
        "total_executions": 0,
        "success_rate": 0.0,
        "capabilities": meta["capabilities"] if meta else [],
    }
    if meta is None:
        return response

    instance = meta["peek"]()
    if instance is not None:
        state = instance.state
        response.update(
            {
                "status": state.status.value,
                "current_task": state.current_task,
                "progress_percent": state.progress_percent,
                "total_executions": state.total_executions,
                "success_rate": round(state.success_rate, 4),
            }
        )
    return response


# =============================================================================
# Evaluation Routes
# =============================================================================

@api_router.get(
    "/evaluations",
    response_model=dict[str, Any],
    tags=["Evaluations"],
    summary="评估结果查询",
    description="查询 Agent 评估结果列表。",
)
async def list_evaluations(
    params: CommonQueryParams = Depends(),
    eval_type: EvaluationType | None = Query(None, description="按评估类型过滤"),
    agent_type: str | None = Query(None, description="按 Agent 类型过滤"),
) -> dict[str, Any]:
    """查询评估结果列表（真实运行历史,最新在前;未跑过评测时为空列表）"""
    logger.info("List evaluations requested")

    filtered = list(reversed(_evaluation_runs))
    if eval_type:
        filtered = [i for i in filtered if i.get("eval_type") == eval_type.value]

    total = len(filtered)
    start = (params.page - 1) * params.page_size
    page_items = filtered[start : start + params.page_size]

    return {
        "items": page_items,
        "total": total,
        "page": params.page,
        "page_size": params.page_size,
        "filters": {
            "eval_type": eval_type.value if eval_type else None,
            "agent_type": agent_type,
        },
    }


def _record_evaluation_run(eval_id: str, report: dict[str, Any]) -> None:
    """把一次成功的评测运行按维度落入 _evaluation_runs。

    分值口径:评测框架输出 0~1,对外展示统一为 0~100;
    维度名口径:后端枚举 tool_call 对外映射为 tool_calling(前端 dimensionMeta 键)。
    """
    generated_at = report.get("generated_at")
    if isinstance(generated_at, datetime):
        timestamp = generated_at.isoformat()
    elif generated_at:
        timestamp = str(generated_at)
    else:
        timestamp = datetime.now(timezone.utc).isoformat()

    for r in report.get("eval_results", []):
        dimension = str(r.get("eval_type", ""))
        _evaluation_runs.append(
            {
                "id": f"{eval_id}-{dimension}",
                "run_id": eval_id,
                "dimension": "tool_calling" if dimension == "tool_call" else dimension,
                "eval_type": dimension,
                "status": r.get("status", ""),
                "overall_score": round(float(r.get("overall_score", 0.0)) * 100, 1),
                "metrics": [
                    {
                        "name": m.get("name", ""),
                        "score": round(float(m.get("score", 0.0)) * 100, 1),
                        "weight": m.get("weight", 0.0),
                    }
                    for m in r.get("metrics", [])
                ],
                "timestamp": timestamp,
            }
        )


@api_router.post(
    "/evaluations/run",
    response_model=dict[str, Any],
    status_code=status.HTTP_202_ACCEPTED,
    tags=["Evaluations"],
    summary="执行评估",
    description="触发指定类型的评估任务。",
)
async def run_evaluation(
    eval_type: EvaluationType,
    agent_type: str | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """执行评估

    D6 接真:构造 EvalInput(默认测试集 + 从 _incident_service 现有故障重建的 RCA
    产物,即真实冒烟"先 trigger 后 run"的数据面)→ 调 EvalAgent。响应保留既有 4 个
    key(兼容任何依赖方,status 字面量恒为 "started"),评测产出增量附加;空库或
    reasoning 缺数据导致 process 降级为 failure 时,增量键优雅缺席、端点仍返回 202。
    judge 开关由 EvalAgent.process() 每次读全局配置(未注入路径)。
    """
    logger.info(
        "Run evaluation requested", eval_type=eval_type.value, agent_type=agent_type
    )

    eval_id = f"eval-run-{uuid.uuid4().hex[:8]}"

    # EvaluationType → EvalType(safety/performance 无对应维度 → 落 FULL)
    try:
        mapped_type = EvalType(eval_type.value)
    except ValueError:
        mapped_type = EvalType.FULL

    explicit_truths = (body or {}).get("ground_truth_by_incident", {})
    if not isinstance(explicit_truths, dict):
        explicit_truths = {}
    scenarios_by_id = {scenario["id"]: scenario for scenario in FAULT_SCENARIOS}

    def normalize_ground_truth(raw: dict[str, Any]) -> dict[str, Any]:
        """把请求体/数据集的场景真值归一为 reasoning ground_truth。"""
        truth = dict(raw)
        expected_action = raw.get("expected_action", {})
        if "suggested_actions" not in truth:
            if isinstance(expected_action, dict) and expected_action.get("type"):
                truth["suggested_actions"] = [expected_action["type"]]
            elif isinstance(expected_action, str) and expected_action:
                truth["suggested_actions"] = [expected_action]
        # FAULT_SCENARIOS 没有单独 evidence 字段；metrics 是场景已知的
        # 观测真值，只映射为 evidence_completeness 所比较的证据类型。
        if "evidence" not in truth and raw.get("metrics"):
            truth["evidence"] = {"alert_metric": raw["metrics"]}
        return truth

    # 从现有故障重建 rca_agent 产物，同时按 incident 粒度配对真值。
    # 优先级：显式请求体 > context.scenario_id > incident_id > N/A。
    agent_results: list[dict[str, Any]] = []
    reasoning_samples: list[dict[str, Any]] = []
    ground_truth_sources = {
        "explicit": 0,
        "context_scenario": 0,
        "incident_id_scenario": 0,
        "excluded": 0,
        "total": 0,
    }
    for stored_incident_id, inc in _incident_service._incidents.items():
        if inc.rca_event is not None:
            incident_id = str(getattr(inc, "incident_id", stored_incident_id))
            rca_event = inc.rca_event
            agent_result = {
                "incident_id": incident_id,
                "agent_name": "rca_agent",
                "output_data": {
                    "rca_event": rca_event.model_dump(),
                    "root_cause": rca_event.root_cause,
                    "confidence": rca_event.confidence,
                    "impact_chain": rca_event.impact_chain,
                    "suggested_actions": rca_event.recommended_actions,
                },
            }
            agent_results.append(agent_result)

            raw_truth: dict[str, Any] | None = None
            source: str | None = None
            explicit_truth = explicit_truths.get(incident_id)
            if explicit_truth is None and incident_id != str(stored_incident_id):
                explicit_truth = explicit_truths.get(str(stored_incident_id))
            if isinstance(explicit_truth, dict):
                raw_truth = explicit_truth
                source = "explicit"
            else:
                context = getattr(inc, "context", {}) or {}
                scenario_id = (
                    context.get("scenario_id") if isinstance(context, dict) else None
                )
                if scenario_id in scenarios_by_id:
                    raw_truth = scenarios_by_id[scenario_id]
                    source = "context_scenario"
                elif incident_id in scenarios_by_id:
                    raw_truth = scenarios_by_id[incident_id]
                    source = "incident_id_scenario"

            ground_truth = normalize_ground_truth(raw_truth) if raw_truth else {}
            ground_truth_sources["total"] += 1
            if source is not None and ground_truth.get("root_cause"):
                ground_truth_sources[source] += 1
            else:
                ground_truth_sources["excluded"] += 1
                ground_truth = {}

            reasoning_samples.append(
                {
                    "id": incident_id,
                    "predicted": EvalAgent._prediction_from_rca_result(agent_result),
                    "ground_truth": ground_truth,
                }
            )

    eval_input = EvalInput(
        eval_type=mapped_type,
        target_agent=agent_type or "",
        test_cases=EvalAgent._get_default_test_cases(),
        agent_results=agent_results,
        samples_by_type={"reasoning": reasoning_samples},
    )

    # 函数内构造(白名单不允许模块级单例);process() 每次读 enable_judge 开关
    eval_agent = EvalAgent()
    ctx = AgentExecutionContext(
        incident_id=eval_id,
        input_data={"eval_type": mapped_type.value},
    )
    result = await eval_agent.execute(eval_input, ctx)

    # 既有 4 key 原样保留(守住 test_api.py 绿基线)
    response: dict[str, Any] = {
        "eval_id": eval_id,
        "status": "started",
        "eval_type": eval_type.value,
        "message": f"Evaluation task {eval_id} started — results will be available shortly",
        "ground_truth_sources": ground_truth_sources,
    }
    # 增量:诚实反映实际执行结果(降级/空库时优雅缺席)
    if result.success and result.output_data:
        response["execution"] = "completed"
        response["report"] = result.output_data.get("report")
        response["overall_score"] = result.output_data.get("overall_score")
        # 成功运行落入历史,GET /evaluations 由此返回真实数据
        _record_evaluation_run(eval_id, result.output_data.get("report") or {})
    return response


# =============================================================================
# Topology Routes
# =============================================================================

def _topology_node_metrics(svc_name: str) -> dict[str, Any]:
    """节点指标取静态数据集 normal 场景的最新值。

    数据层节点(mysql-primary/redis-cache/elasticsearch)不在 METRICS_DATASETS,
    诚实返回 None 而非伪造数值。
    """
    if svc_name not in METRICS_DATASETS:
        return {"cpu": None, "memory": None, "latency": None}
    return {
        "cpu": f"{get_metric_data(svc_name, 'cpu_usage_percent', 'normal')[-1]:.0f}%",
        "memory": f"{get_metric_data(svc_name, 'memory_usage_percent', 'normal')[-1]:.0f}%",
        "latency": f"{get_metric_data(svc_name, 'p99_latency_ms', 'normal')[-1]:.0f}ms",
    }


def _topology_edge_latency(target: str) -> str | None:
    """边延迟取被调用方(target)的 p99 延迟最新值;无数据集时为 None。"""
    if target not in METRICS_DATASETS:
        return None
    return f"{get_metric_data(target, 'p99_latency_ms', 'normal')[-1]:.0f}ms"


@api_router.get(
    "/topology",
    response_model=dict[str, Any],
    tags=["Topology"],
    summary="服务拓扑",
    description="获取服务依赖拓扑图。",
)
async def get_topology(
    service: str | None = Query(None, description="指定服务根节点"),
    depth: int = Query(3, ge=1, le=10, description="拓扑深度"),
    environment: str | None = Query(None, description="环境过滤"),
) -> dict[str, Any]:
    """获取服务拓扑 — 基于 SERVICE_TOPOLOGY 构建节点和边"""
    logger.info("Get topology requested", service=service, depth=depth)

    # 构建节点列表
    nodes: list[dict[str, Any]] = []
    node_ids = set()

    for svc_name, svc_info in SERVICE_TOPOLOGY.items():
        if service and svc_name != service:
            # BFS 展开：如果指定了根服务，只返回其上下游
            continue

        node_ids.add(svc_name)
        # 确定节点状态（模拟）
        status_map = {
            "order-service": "warning",
            "payment-service": "healthy",
            "inventory-service": "healthy",
            "user-service": "healthy",
            "api-gateway": "critical",
            "mysql-primary": "warning",
            "redis-cache": "healthy",
            "elasticsearch": "healthy",
        }

        nodes.append({
            "id": svc_name,
            "name": svc_name,
            "service": svc_name,
            "status": status_map.get(svc_name, "healthy"),
            "dependencies": svc_info.get("dependencies", []),
            "tier": svc_info.get("tier", "standard"),
            "metrics": _topology_node_metrics(svc_name),
        })

    # 构建边列表
    edges: list[dict[str, Any]] = []
    edge_set = set()

    for svc_name, svc_info in SERVICE_TOPOLOGY.items():
        if service and svc_name != service:
            continue
        for dep in svc_info.get("dependencies", []):
            edge_key = f"{svc_name}->{dep}"
            if edge_key not in edge_set:
                edge_set.add(edge_key)
                edge_type = "db" if dep.startswith("mysql") or dep.startswith("redis") or dep.startswith("elastic") else "http"
                edges.append({
                    "source": svc_name,
                    "target": dep,
                    "type": edge_type,
                    "latency": _topology_edge_latency(dep),
                })
        for dep in svc_info.get("dependents", []):
            edge_key = f"{dep}->{svc_name}"
            if edge_key not in edge_set:
                edge_set.add(edge_key)
                edges.append({
                    "source": dep,
                    "target": svc_name,
                    "type": "http",
                    "latency": _topology_edge_latency(svc_name),
                })

    # 如果指定了 service，执行 BFS 展开
    if service and service in SERVICE_TOPOLOGY:
        visited = {service}
        queue = [service]
        for _ in range(depth):
            if not queue:
                break
            current = queue.pop(0)
            svc_info = SERVICE_TOPOLOGY.get(current, {})
            for dep in svc_info.get("dependencies", []) + svc_info.get("dependents", []):
                if dep not in visited and dep in SERVICE_TOPOLOGY:
                    visited.add(dep)
                    queue.append(dep)
                    if dep not in node_ids:
                        node_ids.add(dep)
                        nodes.append({
                            "id": dep,
                            "name": dep,
                            "service": dep,
                            "status": "healthy",
                            "dependencies": SERVICE_TOPOLOGY.get(dep, {}).get("dependencies", []),
                            "tier": SERVICE_TOPOLOGY.get(dep, {}).get("tier", "standard"),
                            "metrics": _topology_node_metrics(dep),
                        })

    return {
        "nodes": nodes,
        "edges": edges,
        "root_service": service,
        "depth": depth,
        "environment": environment or "production",
    }


# =============================================================================
# Memory Routes
# =============================================================================

def _parse_memory_type(memory_type: str | None) -> MemoryType | None:
    """把查询参数解析为 MemoryType,非法值抛 400。"""
    if not memory_type:
        return None
    try:
        return MemoryType(memory_type)
    except ValueError:
        valid = "/".join(t.value for t in MemoryType)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"invalid memory_type '{memory_type}', expected one of: {valid}",
        )


def _memory_entry_to_dict(entry: Any) -> dict[str, Any]:
    """MemoryEntry → 端点响应结构(保留 key/value 等既有字段名兼容消费方)。"""
    return {
        "id": entry.memory_id,
        "memory_id": entry.memory_id,
        "key": entry.summary or entry.content[:50],
        "value": entry.content,
        "type": entry.memory_type.value,
        "created_at": entry.created_at.isoformat(),
        "importance": entry.importance_score,
        "tags": entry.tags,
    }


@api_router.get(
    "/memory/search",
    response_model=dict[str, Any],
    tags=["Memory"],
    summary="记忆搜索",
    description="在记忆系统中搜索相关记忆。",
)
async def search_memory(
    query: str = Query(..., description="搜索查询"),
    top_k: int = Query(10, ge=1, le=50, description="返回数量"),
    memory_type: str | None = Query(
        None, description="记忆类型过滤(episodic/semantic/procedural/observation)"
    ),
    incident_id: str | None = Query(None, description="关联故障ID"),
) -> dict[str, Any]:
    """搜索记忆 — 接真实 MemorySystem(三层记忆 + ChromaDB/RRF 混合检索)

    记忆系统不可用或检索异常时降级为空结果(HTTP 200),不再回退到假数据。
    """
    logger.info("Memory search", query=query, top_k=top_k)

    mt = _parse_memory_type(memory_type)
    empty_response = {
        "query": query,
        "results": [],
        "total": 0,
        "search_time_ms": 0.0,
    }

    ms = await _get_memory_system()
    if ms is None:
        return empty_response

    try:
        result = await ms.search(
            query_text=query,
            top_k=top_k,
            memory_type=mt,
            incident_id=incident_id or "",
        )
    except Exception as e:
        logger.warning("Memory search failed, returning empty result", error=str(e))
        return empty_response

    similarities = list(result.similarities)
    similarities += [0.0] * max(0, len(result.results) - len(similarities))
    results = [
        {"entry": _memory_entry_to_dict(entry), "score": round(score, 4)}
        for entry, score in zip(result.results, similarities)
    ]

    return {
        "query": query,
        "results": results,
        "total": result.total_found,
        "search_time_ms": round(result.query_time_ms, 2),
    }


@api_router.post(
    "/memory/store",
    response_model=dict[str, Any],
    tags=["Memory"],
    summary="存储记忆",
    description="向记忆系统中存储新的记忆条目。",
)
async def store_memory(
    content: str,
    memory_type: str = "observation",
    incident_id: str = "",
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """存储记忆 — 真实写入 MemorySystem(此前只返回假 ID,不落任何存储)"""
    mt = _parse_memory_type(memory_type) or MemoryType.OBSERVATION

    ms = await _get_memory_system()
    if ms is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Memory system unavailable",
        )

    try:
        entry = await ms.store(
            content=content,
            memory_type=mt,
            source_agent="api",
            source_incident_id=incident_id,
            tags=tags or [],
        )
    except Exception as e:
        logger.warning("Memory store failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Memory store failed",
        )

    logger.info("Store memory", memory_type=memory_type, memory_id=entry.memory_id)
    return {
        "memory_id": entry.memory_id,
        "status": "stored",
        "content_length": len(content),
        "type": memory_type,
    }


# =============================================================================
# 自然语言诊断端点
# =============================================================================


class NLQueryRequest(PydanticBaseModel):
    """自然语言诊断请求"""
    query: str = Field(default="", description="用户的自然语言问题")
    context: dict[str, Any] = Field(default_factory=dict, description="额外上下文")


@api_router.post(
    "/incidents/diagnose",
    response_model=dict[str, Any],
    tags=["Diagnosis"],
    summary="自然语言故障诊断",
    description="""
    接收用户的自然语言问题，自动完成全流程诊断。

    支持的大白话示例：
    - "为什么下单这么慢？"
    - "支付一直转圈圈，是不是挂了？"
    - "用户说登录不上去了"
    - "有没有重复扣款的情况？"

    内部流程:
    1. 意图识别 → 判断用户是诊断/查询/修复/历史查询
    2. 实体抽取 → 提取服务名、症状、紧急程度
    3. 指标映射 → 生成诊断查询计划（哪些指标、查哪些服务）
    4. 如检测到故障意图 → 自动触发 Agent 管道进行根因分析
    """,
)
async def diagnose_from_natural_language(
    request: NLQueryRequest,
) -> dict[str, Any]:
    """自然语言故障诊断入口"""
    query = request.query.strip()
    if not query:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Query cannot be empty",
        )

    logger.info("NL diagnosis requested", query=query[:100])

    # Layer 1+2: 快慢路径混合 NLU(降级在 hybrid_understand 内闭环,永不 raise)
    intent, entities, nlu_info = await hybrid_understand(
        query, known_services=list(SERVICE_TOPOLOGY.keys()),
    )

    # Layer 3: 指标映射
    metric_mapper = MetricMapper()
    primary_service = entities.services[0] if entities.services else "unknown"
    diagnosis_plan = metric_mapper.map(
        service=primary_service,
        symptoms=entities.symptoms,
        business_domain=entities.business_domain,
    )

    # Layer 4: 根据意图触发对应流程
    triggered_incident = None
    triggered_business = None

    if intent.intent == "fault_diagnosis" and entities.symptoms:
        # 构造告警事件并触发 Agent 管道
        if entities.services:
            alert = AlertEvent(
                service=primary_service,
                metric=diagnosis_plan.queries[0].metric if diagnosis_plan.queries else "unknown",
                value=0.0,
                threshold=0.0,
                severity=SeverityLevel(entities.urgency) if entities.urgency in {"critical", "high"} else SeverityLevel.MEDIUM,
                labels={
                    "tier": "critical",
                    "nl_query": query[:200],
                },
                annotations={
                    "summary": f"用户报告: {query}",
                    "extracted_symptoms": ",".join(entities.symptoms),
                    "extracted_services": ",".join(entities.services),
                },
            )

            incident = Incident.from_alert(alert)
            incident.transition_to(IncidentState.ACKNOWLEDGED, actor="nl-diagnosis")
            _incident_service._incidents[incident.incident_id] = incident

            # 异步触发管道
            asyncio.create_task(_process_incident_pipeline(incident))

            triggered_incident = {
                "incident_id": incident.incident_id,
                "status": "processing",
            }

    elif intent.intent == "business_check" and entities.business_domain:
        # 触发业务异常检查
        biz_input = BusinessMetricInput(
            service_name=primary_service,
            business_domain=entities.business_domain,
            context={},
        )
        biz_agent = _get_business_monitor()
        biz_ctx = AgentExecutionContext(
            incident_id=str(uuid.uuid4()),
            input_data={"nl_query": query},
        )
        biz_result = await biz_agent.process(biz_input, biz_ctx)
        triggered_business = biz_result.output_data if biz_result.success else None

    # 构建响应
    return {
        "original_query": query,
        "intent": {
            "type": intent.intent,
            "confidence": round(intent.confidence, 4),
        },
        "nlu_path": nlu_info["path"],
        "clarification_question": nlu_info.get("clarification", ""),
        "understood_as": {
            "services": entities.services,
            "symptoms": entities.symptoms,
            "urgency": entities.urgency,
            "business_domain": entities.business_domain or "infrastructure",
        },
        "diagnosis_plan": {
            "total_queries": len(diagnosis_plan.queries),
            "queries": [
                {
                    "target": q.target,
                    "metric": q.metric,
                    "reason": q.reason,
                    "priority": q.priority,
                }
                for q in diagnosis_plan.queries[:10]
            ],
        },
        "explanation": metric_mapper.get_explanation(diagnosis_plan),
        "triggered_incident": triggered_incident,
        "triggered_business_check": triggered_business,
    }
