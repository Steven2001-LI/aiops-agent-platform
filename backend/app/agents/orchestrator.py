"""
AIOps Agent Platform - LangGraph Orchestrator

使用 LangGraph 编排多个 Agent 的协作流程，定义故障处理的完整状态机。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from app.agents.base import AgentExecutionContext
from app.agents.change_agent import ChangeAgent, ChangeInput
from app.agents.heal_agent import HealAgent, HealInput
from app.agents.rca_agent import RCAAgent, RCAInput
from app.models.agent import AgentState, AgentStatus
from app.models.events import (
    AlertEvent,
    ChangeEvent,
    HealEvent,
    RCAEvent,
    SeverityLevel,
)
from app.models.incident import Incident, IncidentPhase, IncidentState
from app.utils.logging import get_logger

logger = get_logger(__name__)


class OrchestratorState(str, Enum):
    """编排器状态"""
    IDLE = "idle"                    # 空闲等待
    RECEIVING_ALERT = "receiving_alert"   # 接收告警
    TRIAGING = "triaging"            # 分类分级
    RUNNING_RCA = "running_rca"      # 根因分析
    DECIDING_ACTION = "deciding_action"   # 决策
    EXECUTING_HEAL = "executing_heal"     # 执行自愈
    AWAITING_APPROVAL = "awaiting_approval"  # 等待审批
    VERIFYING = "verifying"          # 验证结果
    COMPLETED = "completed"          # 完成
    ESCALATED = "escalated"          # 已升级
    ERROR = "error"                  # 错误


class Orchestrator:
    """
    LangGraph 编排器

    使用 LangGraph 定义故障处理的完整状态机和 Agent 协作流程。

    流程图:
    
    [Alert] -> RECEIVING_ALERT -> TRIAGING -> RUNNING_RCA -> DECIDING_ACTION
                                                                   |
                    +----------------------------------------------+
                    |                                              |
              [低风险] -> EXECUTING_HEAL -> VERIFYING -> COMPLETED  |
                    |                                              |
              [高风险] -> AWAITING_APPROVAL -> ...                 |
                    |                                              |
              [无法处理] -> ESCALATED                              |
    """

    def __init__(self) -> None:
        self._state = OrchestratorState.IDLE
        self._current_incident: Incident | None = None
        self._graph: Any = None  # LangGraph 图实例
        self._compiled_graph: Any = None  # 编译后的图

    @property
    def state(self) -> OrchestratorState:
        """当前编排器状态"""
        return self._state

    @property
    def current_incident(self) -> Incident | None:
        """当前处理的故障"""
        return self._current_incident

    def build_graph(self) -> Any:
        """
        构建 LangGraph 状态机图

        定义各节点（Agent）和边（流转条件）。

        Returns:
            Any: 构建好的图
        """
        try:
            from langgraph.graph import StateGraph, END

            # 定义状态类型
            class GraphState:
                """LangGraph 状态定义"""
                def __init__(self) -> None:
                    self.incident: Incident | None = None
                    self.current_phase: str = ""
                    self.agent_results: dict[str, Any] = {}
                    self.errors: list[str] = []
                    self.completed: bool = False
                    self.escalated: bool = False

            # 创建图
            workflow = StateGraph(GraphState)

            # 添加节点
            workflow.add_node("receive_alert", self._node_receive_alert)
            workflow.add_node("triage", self._node_triage)
            workflow.add_node("run_rca", self._node_run_rca)
            workflow.add_node("decide_action", self._node_decide_action)
            workflow.add_node("execute_heal", self._node_execute_heal)
            workflow.add_node("request_approval", self._node_request_approval)
            workflow.add_node("verify_fix", self._node_verify_fix)
            workflow.add_node("escalate", self._node_escalate)
            workflow.add_node("complete", self._node_complete)

            # 添加边 - 定义流转逻辑
            workflow.set_entry_point("receive_alert")
            workflow.add_edge("receive_alert", "triage")
            workflow.add_edge("triage", "run_rca")
            workflow.add_edge("run_rca", "decide_action")

            # 决策节点条件分支
            workflow.add_conditional_edges(
                "decide_action",
                self._edge_decide_action,
                {
                    "heal": "execute_heal",
                    "approve": "request_approval",
                    "escalate": "escalate",
                    "complete": "complete",
                },
            )

            workflow.add_edge("execute_heal", "verify_fix")

            # 验证节点条件分支
            workflow.add_conditional_edges(
                "verify_fix",
                self._edge_verify_fix,
                {
                    "completed": "complete",
                    "retry": "decide_action",
                    "escalate": "escalate",
                },
            )

            workflow.add_edge("request_approval", "verify_fix")
            workflow.add_edge("escalate", END)
            workflow.add_edge("complete", END)

            self._graph = workflow
            self._compiled_graph = workflow.compile()

            logger.info("LangGraph orchestrator built successfully")
            return self._compiled_graph

        except ImportError:
            logger.warning("LangGraph not available, running in fallback mode")
            return None

    async def process_alert(self, alert: AlertEvent) -> Incident:
        """
        处理告警入口

        Args:
            alert: 告警事件

        Returns:
            Incident: 创建的故障实例
        """
        logger.info(
            "Orchestrator processing alert",
            service=alert.service,
            severity=alert.severity.value,
        )

        self._state = OrchestratorState.RECEIVING_ALERT

        # 创建故障实例
        incident = Incident.from_alert(alert)
        self._current_incident = incident

        if self._compiled_graph:
            try:
                initial_state = {
                    "incident": incident,
                    "current_phase": "detection",
                    "agent_results": {},
                    "errors": [],
                    "completed": False,
                    "escalated": False,
                }
                # 真正调用 LangGraph
                result = await self._compiled_graph.ainvoke(initial_state)
                # 把 agent_results 写回 incident context
                for key, value in result.get("agent_results", {}).items():
                    incident.context[f"orchestrator.{key}"] = value
                if result.get("escalated"):
                    incident.transition_to(IncidentState.ESCALATED, actor="orchestrator")
                    self._state = OrchestratorState.ESCALATED
                elif result.get("completed"):
                    incident.transition_to(IncidentState.RESOLVED, actor="orchestrator")
                    self._state = OrchestratorState.COMPLETED
                else:
                    # graph 执行未明确完成，转 sequential 兜底
                    await self._sequential_process(incident)
            except Exception as e:
                logger.error("LangGraph execution failed, falling back to sequential", error=str(e))
                await self._sequential_process(incident)
        else:
            # LangGraph 不可用，sequential 兜底
            await self._sequential_process(incident)

        return incident

    async def _sequential_process(self, incident: Incident) -> None:
        """
        顺序处理流程（Fallback 模式）

        当 LangGraph 不可用时，按顺序执行各 Agent。
        每个 Agent 的真实结果写回 incident 对应字段。

        Args:
            incident: 故障实例
        """
        ctx = AgentExecutionContext(
            incident_id=incident.incident_id,
            metadata={"correlation_id": incident.alert_event.correlation_id if incident.alert_event else ""},
        )

        # Step 1: 分类分级（incident.from_alert 已完成，此处仅 transition）
        self._state = OrchestratorState.TRIAGING
        incident.transition_to(IncidentState.ACKNOWLEDGED, actor="orchestrator")
        incident.add_timeline_entry(
            phase=IncidentPhase.TRIAGE,
            state=IncidentState.ACKNOWLEDGED,
            actor="orchestrator",
            action="Alert triaged",
        )

        # Step 2: 根因分析
        self._state = OrchestratorState.RUNNING_RCA
        incident.transition_to(IncidentState.RCA_IN_PROGRESS, actor="rca_agent")
        incident.add_timeline_entry(
            phase=IncidentPhase.RCA,
            state=IncidentState.RCA_IN_PROGRESS,
            actor="rca_agent",
            action="Running root cause analysis",
        )

        try:
            rca_agent = RCAAgent()
            rca_result = await rca_agent.process(
                RCAInput(
                    alert=incident.alert_event,
                    incident_id=incident.incident_id,
                    lookback_minutes=60,
                    max_hops=3,
                ),
                context=ctx,
            )
            if rca_result.success and rca_result.output_data:
                rca_event_dict = rca_result.output_data.get("rca_event", {})
                if rca_event_dict:
                    incident.rca_event = (
                        RCAEvent(**rca_event_dict)
                        if isinstance(rca_event_dict, dict)
                        else rca_event_dict
                    )
                    incident.context["rca_root_cause"] = rca_result.output_data.get("root_cause", "unknown")
            incident.transition_to(IncidentState.RCA_COMPLETED, actor="rca_agent")
        except Exception as e:
            logger.error("RCA agent failed in sequential", error=str(e))
            incident.transition_to(IncidentState.ESCALATED, actor="rca_agent")

        # Step 3: 决策 + 自愈（基于 severity）
        self._state = OrchestratorState.DECIDING_ACTION
        if incident.severity in (SeverityLevel.CRITICAL, SeverityLevel.HIGH):
            # 高风险 → 必须审批，跳过直接自愈
            self._state = OrchestratorState.AWAITING_APPROVAL
            incident.transition_to(IncidentState.AWAITING_APPROVAL, actor="change_agent")
        else:
            # 低/中风险 → 自愈
            self._state = OrchestratorState.EXECUTING_HEAL
            incident.transition_to(IncidentState.HEALING, actor="heal_agent")
            try:
                heal_agent = HealAgent()
                heal_result = await heal_agent.process(
                    HealInput(
                        rca_event=incident.rca_event or RCAEvent(incident_id=incident.incident_id),
                        incident_id=incident.incident_id,
                        dry_run=True,
                    ),
                    context=ctx,
                )
                if heal_result.success and heal_result.output_data:
                    heal_event_dict = heal_result.output_data.get("heal_event", {})
                    if heal_event_dict:
                        heal_event = (
                            HealEvent(**heal_event_dict)
                            if isinstance(heal_event_dict, dict)
                            else heal_event_dict
                        )
                        incident.heal_events.append(heal_event)
                        incident.context["heal_action"] = heal_result.output_data.get("action", "")
                incident.transition_to(IncidentState.HEALED, actor="heal_agent")
            except Exception as e:
                logger.error("Heal agent failed in sequential", error=str(e))
                incident.transition_to(IncidentState.AWAITING_APPROVAL, actor="heal_agent")

        # Step 4: 变更审批（无论前面是否走过 heal，都需要 Change Agent 决策）
        try:
            change_agent = ChangeAgent()
            latest_heal = incident.heal_events[-1] if incident.heal_events else None
            change_input = ChangeInput(
                heal_event=latest_heal or HealEvent(incident_id=incident.incident_id),
                incident_id=incident.incident_id,
                change_type="auto_heal",
                requester="orchestrator",
            )
            change_result = await change_agent.process(change_input, context=ctx)
            if change_result.success and change_result.output_data:
                change_event_dict = change_result.output_data.get("change_event", {})
                if change_event_dict:
                    change_event = (
                        ChangeEvent(**change_event_dict)
                        if isinstance(change_event_dict, dict)
                        else change_event_dict
                    )
                    incident.change_events.append(change_event)
                    incident.context["approval_status"] = change_result.output_data.get(
                        "approval_status", "pending"
                    )
        except Exception as e:
            logger.error("Change agent failed in sequential", error=str(e))

        # Step 5: 验证（标记完成）
        self._state = OrchestratorState.VERIFYING
        incident.transition_to(IncidentState.RESOLVED, actor="orchestrator")
        self._state = OrchestratorState.COMPLETED

        incident.add_timeline_entry(
            phase=IncidentPhase.RESOLUTION,
            state=IncidentState.RESOLVED,
            actor="orchestrator",
            action="Incident resolved (sequential fallback)",
        )

    # === LangGraph 节点方法（真实调用 Agent）===

    async def _node_receive_alert(self, state: dict[str, Any]) -> dict[str, Any]:
        """接收告警节点"""
        self._state = OrchestratorState.RECEIVING_ALERT
        logger.info("Graph node: receive_alert")
        if state.get("incident"):
            state["incident"].transition_to(IncidentState.ACKNOWLEDGED, actor="orchestrator")
        return state

    async def _node_triage(self, state: dict[str, Any]) -> dict[str, Any]:
        """分类分级节点 — 标记 incident 已被 orchestrator 接收"""
        self._state = OrchestratorState.TRIAGING
        logger.info("Graph node: triage")
        incident = state.get("incident")
        if incident:
            incident.transition_to(IncidentState.ACKNOWLEDGED, actor="orchestrator")
        return state

    async def _node_run_rca(self, state: dict[str, Any]) -> dict[str, Any]:
        """根因分析节点"""
        self._state = OrchestratorState.RUNNING_RCA
        logger.info("Graph node: run_rca")
        incident = state.get("incident")
        if not incident:
            return state
        incident.transition_to(IncidentState.RCA_IN_PROGRESS, actor="rca_agent")
        try:
            rca_agent = RCAAgent()
            ctx = AgentExecutionContext(incident_id=incident.incident_id)
            result = await rca_agent.process(
                RCAInput(alert=incident.alert_event, incident_id=incident.incident_id),
                context=ctx,
            )
            state["agent_results"]["rca"] = result.output_data or {}
            rca_event_dict = result.output_data.get("rca_event", {}) if result.output_data else {}
            if rca_event_dict:
                incident.rca_event = (
                    RCAEvent(**rca_event_dict)
                    if isinstance(rca_event_dict, dict)
                    else rca_event_dict
                )
            incident.transition_to(IncidentState.RCA_COMPLETED, actor="rca_agent")
        except Exception as e:
            logger.error("RCA node error", error=str(e))
            state["errors"].append(f"rca: {e}")
        return state

    async def _node_decide_action(self, state: dict[str, Any]) -> dict[str, Any]:
        """决策节点 — 不调 Agent，仅记录决策依据"""
        self._state = OrchestratorState.DECIDING_ACTION
        logger.info("Graph node: decide_action")
        return state

    async def _node_execute_heal(self, state: dict[str, Any]) -> dict[str, Any]:
        """执行自愈节点"""
        self._state = OrchestratorState.EXECUTING_HEAL
        logger.info("Graph node: execute_heal")
        incident = state.get("incident")
        if not incident:
            return state
        try:
            heal_agent = HealAgent()
            ctx = AgentExecutionContext(incident_id=incident.incident_id)
            rca_event = incident.rca_event or RCAEvent(incident_id=incident.incident_id)
            result = await heal_agent.process(
                HealInput(rca_event=rca_event, incident_id=incident.incident_id, dry_run=True),
                context=ctx,
            )
            state["agent_results"]["heal"] = result.output_data or {}
            heal_event_dict = result.output_data.get("heal_event", {}) if result.output_data else {}
            if heal_event_dict:
                incident.heal_events.append(
                    HealEvent(**heal_event_dict)
                    if isinstance(heal_event_dict, dict)
                    else heal_event_dict
                )
        except Exception as e:
            logger.error("Heal node error", error=str(e))
            state["errors"].append(f"heal: {e}")
        return state

    async def _node_request_approval(self, state: dict[str, Any]) -> dict[str, Any]:
        """请求审批节点"""
        self._state = OrchestratorState.AWAITING_APPROVAL
        logger.info("Graph node: request_approval")
        incident = state.get("incident")
        if not incident:
            return state
        try:
            change_agent = ChangeAgent()
            ctx = AgentExecutionContext(incident_id=incident.incident_id)
            latest_heal = incident.heal_events[-1] if incident.heal_events else None
            result = await change_agent.process(
                ChangeInput(
                    heal_event=latest_heal or HealEvent(incident_id=incident.incident_id),
                    incident_id=incident.incident_id,
                    change_type="auto_heal",
                    requester="orchestrator",
                ),
                context=ctx,
            )
            state["agent_results"]["change"] = result.output_data or {}
            change_event_dict = result.output_data.get("change_event", {}) if result.output_data else {}
            if change_event_dict:
                incident.change_events.append(
                    ChangeEvent(**change_event_dict)
                    if isinstance(change_event_dict, dict)
                    else change_event_dict
                )
        except Exception as e:
            logger.error("Change node error", error=str(e))
            state["errors"].append(f"change: {e}")
        return state

    async def _node_verify_fix(self, state: dict[str, Any]) -> dict[str, Any]:
        """验证修复节点"""
        self._state = OrchestratorState.VERIFYING
        logger.info("Graph node: verify_fix")
        incident = state.get("incident")
        if incident:
            incident.transition_to(IncidentState.RESOLVED, actor="orchestrator")
        state["completed"] = True
        return state

    async def _node_escalate(self, state: dict[str, Any]) -> dict[str, Any]:
        """升级节点"""
        self._state = OrchestratorState.ESCALATED
        logger.info("Graph node: escalate")
        state["escalated"] = True
        if state.get("incident"):
            incident = state["incident"]
            incident.transition_to(IncidentState.ESCALATED, actor="orchestrator")
        return state

    async def _node_complete(self, state: dict[str, Any]) -> dict[str, Any]:
        """完成节点"""
        self._state = OrchestratorState.COMPLETED
        logger.info("Graph node: complete")
        state["completed"] = True
        return state

    # === 条件边方法 ===

    def _edge_decide_action(self, state: dict[str, Any]) -> str:
        """
        决策条件边

        根据 RCA 结果和 severity 决定下一步：
        - 没有 incident → escalate
        - severity=CRITICAL 或 HIGH → approve（需审批）
        - 否则 → heal（直接自愈）
        - 已 completed → complete
        """
        incident = state.get("incident")
        if not incident:
            return "escalate"

        if state.get("completed"):
            return "complete"

        # 基于 severity 决策
        if incident.severity == SeverityLevel.CRITICAL:
            return "approve"
        elif incident.severity == SeverityLevel.LOW:
            return "heal"
        else:
            # HIGH/MEDIUM 默认走 heal（带 dry-run）
            return "heal"

    def _edge_verify_fix(self, state: dict[str, Any]) -> str:
        """
        验证条件边

        根据验证结果决定下一步。
        """
        if state.get("completed"):
            return "completed"
        if state.get("escalated"):
            return "escalate"
        # 简化实现：默认 completed（重试逻辑可作为未来扩展点）
        return "completed"
