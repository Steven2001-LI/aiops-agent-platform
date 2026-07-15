"""
AIOps Agent Platform - LangGraph Orchestrator

使用 LangGraph 编排多个 Agent 的协作流程，定义故障处理的完整状态机。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any, TypedDict

from app.agents.base import AgentExecutionContext
from app.agents.change_agent import ChangeAgent, ChangeInput
from app.agents.heal_agent import HealAgent, HealInput
from app.agents.rca_agent import RCAAgent, RCAInput
from app.models.events import AlertEvent, ChangeEvent, HealEvent, RCAEvent
from app.models.incident import Incident, IncidentState
from app.utils.logging import get_logger

logger = get_logger(__name__)


class GraphState(TypedDict):
    """LangGraph 状态 schema。"""

    # incident 是穿过整张图的共享可变对象；节点直接记录其状态和时间线。
    incident: Incident
    current_phase: str
    agent_results: dict[str, Any]
    errors: list[str]
    completed: bool
    escalated: bool
    approval_status: str


class OrchestratorState(str, Enum):
    """编排器状态。"""

    IDLE = "idle"
    RECEIVING_ALERT = "receiving_alert"
    TRIAGING = "triaging"
    RUNNING_RCA = "running_rca"
    DECIDING_ACTION = "deciding_action"
    EXECUTING_HEAL = "executing_heal"
    AWAITING_APPROVAL = "awaiting_approval"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    ESCALATED = "escalated"
    ERROR = "error"


class Orchestrator:
    """独立的 LangGraph 备用/演示编排路径。

    流程与 HTTP 主管道的核心语义保持一致：RCA 、dry-run Heal、Change
    审批都通过后才能将故障标记为 RESOLVED。任何 Agent 失败都转人工升级，
    审批 pending 则作为合法的暂停终态保留。
    """

    def __init__(
        self,
        on_incident_update: Callable[[Incident], Awaitable[None]] | None = None,
    ) -> None:
        self._state = OrchestratorState.IDLE
        self._current_incident: Incident | None = None
        self._graph: Any = None
        self._compiled_graph: Any = None
        # 状态更新回调(HTTP 接线时注入 WebSocket 广播);None 时静默跳过,
        # orchestrator 自身不依赖任何 HTTP/WS 模块
        self._on_incident_update = on_incident_update

    async def _notify(self, incident: Incident) -> None:
        """通知调用方 incident 状态更新;回调失败不影响图执行。"""
        if self._on_incident_update is None:
            return
        try:
            await self._on_incident_update(incident)
        except Exception as exc:
            logger.debug("Incident update callback failed", error=str(exc))

    @property
    def state(self) -> OrchestratorState:
        """当前编排器状态。"""
        return self._state

    @property
    def current_incident(self) -> Incident | None:
        """当前处理的故障。"""
        return self._current_incident

    def build_graph(self) -> Any:
        """构建并编译 LangGraph 状态机。"""
        try:
            from langgraph.graph import END, StateGraph

            workflow = StateGraph(GraphState)
            workflow.add_node("receive_alert", self._node_receive_alert)
            workflow.add_node("triage", self._node_triage)
            workflow.add_node("run_rca", self._node_run_rca)
            workflow.add_node("decide_action", self._node_decide_action)
            workflow.add_node("execute_heal", self._node_execute_heal)
            workflow.add_node("request_approval", self._node_request_approval)
            workflow.add_node("verify_fix", self._node_verify_fix)
            workflow.add_node("escalate", self._node_escalate)
            workflow.add_node("complete", self._node_complete)

            workflow.set_entry_point("receive_alert")
            workflow.add_edge("receive_alert", "triage")
            workflow.add_edge("triage", "run_rca")
            workflow.add_edge("run_rca", "decide_action")
            workflow.add_conditional_edges(
                "decide_action",
                self._edge_decide_action,
                {"heal": "execute_heal", "escalate": "escalate"},
            )
            workflow.add_conditional_edges(
                "execute_heal",
                self._edge_after_heal,
                {"approve": "request_approval", "escalate": "escalate"},
            )
            workflow.add_conditional_edges(
                "request_approval",
                self._edge_after_approval,
                {
                    "verify": "verify_fix",
                    "pending": END,
                    "escalate": "escalate",
                },
            )
            workflow.add_conditional_edges(
                "verify_fix",
                self._edge_verify_fix,
                {"completed": "complete", "escalate": "escalate"},
            )
            workflow.add_edge("escalate", END)
            workflow.add_edge("complete", END)

            self._graph = workflow
            self._compiled_graph = workflow.compile()
            logger.info("LangGraph orchestrator built successfully")
            return self._compiled_graph
        except ImportError:
            logger.warning("LangGraph not available; orchestrator cannot execute")
            return None

    async def process_alert(
        self,
        alert: AlertEvent,
        incident: Incident | None = None,
    ) -> Incident:
        """通过已编译的 LangGraph 处理告警。

        Args:
            alert: 告警事件
            incident: 复用已注册的 Incident(HTTP 接线时传入,保证图内处理的
                对象与故障存储/前端查询到的是同一个);None 时自行创建
        """
        logger.info(
            "Orchestrator processing alert",
            service=alert.service,
            severity=alert.severity.value,
        )
        self._state = OrchestratorState.RECEIVING_ALERT
        incident = incident or Incident.from_alert(alert)
        self._current_incident = incident

        if self._compiled_graph is None:
            self._escalate_incident(
                incident,
                stage="graph",
                error_message="LangGraph is unavailable or has not been built",
            )
            return incident

        initial_state: GraphState = {
            "incident": incident,
            "current_phase": "detection",
            "agent_results": {},
            "errors": [],
            "completed": False,
            "escalated": False,
            "approval_status": "",
        }

        try:
            result = await self._compiled_graph.ainvoke(initial_state)
        except Exception as exc:
            logger.error("LangGraph execution failed", error=str(exc))
            self._escalate_incident(
                incident,
                stage="graph",
                error_message=str(exc) or type(exc).__name__,
            )
            return incident

        if not isinstance(result, dict):
            self._escalate_incident(
                incident,
                stage="graph",
                error_message="LangGraph returned an invalid terminal state",
            )
            return incident

        for key, value in result.get("agent_results", {}).items():
            incident.context[f"orchestrator.{key}"] = value
        if result.get("errors"):
            incident.context["orchestrator.errors"] = list(result["errors"])

        # incident 是终态事实源；图字段只是路由信号。pending 是合法终态，
        # 必须在“不明确终态”之前显式识别。
        if incident.state == IncidentState.ESCALATED:
            self._state = OrchestratorState.ESCALATED
        elif (
            result.get("approval_status") == "pending"
            and incident.state == IncidentState.AWAITING_APPROVAL
        ):
            self._state = OrchestratorState.AWAITING_APPROVAL
        elif result.get("completed") and incident.state == IncidentState.RESOLVED:
            self._state = OrchestratorState.COMPLETED
        else:
            self._escalate_incident(
                incident,
                stage="graph",
                error_message=(
                    "LangGraph terminal signals disagree with incident state: "
                    f"incident={incident.state.value}, "
                    f"completed={bool(result.get('completed'))}, "
                    f"escalated={bool(result.get('escalated'))}, "
                    f"approval_status={result.get('approval_status', '')}"
                ),
            )

        return incident

    # === LangGraph 节点 ===

    async def _node_receive_alert(self, state: GraphState) -> dict[str, Any]:
        """接收告警并确认 incident(对已 ACK 的注入 incident 幂等)。"""
        self._state = OrchestratorState.RECEIVING_ALERT
        logger.info("Graph node: receive_alert")
        incident = state["incident"]
        if incident.state != IncidentState.ACKNOWLEDGED:
            incident.transition_to(IncidentState.ACKNOWLEDGED, actor="orchestrator")
        await self._notify(incident)
        return {"incident": incident, "current_phase": "triage"}

    async def _node_triage(self, state: GraphState) -> dict[str, Any]:
        """记录分级阶段，避免重复 ACKNOWLEDGED 时间线。"""
        self._state = OrchestratorState.TRIAGING
        logger.info("Graph node: triage")
        return {"current_phase": "rca"}

    async def _node_run_rca(self, state: GraphState) -> dict[str, Any]:
        """执行 RCA，只有成功且返回有效事件才通过门禁。"""
        self._state = OrchestratorState.RUNNING_RCA
        logger.info("Graph node: run_rca")
        incident = state["incident"]
        incident.transition_to(IncidentState.RCA_IN_PROGRESS, actor="rca_agent")
        results = dict(state["agent_results"])

        try:
            ctx = self._agent_context(incident)
            result = await RCAAgent().execute(
                RCAInput(
                    alert=incident.alert_event,
                    incident_id=incident.incident_id,
                    lookback_minutes=60,
                    max_hops=3,
                ),
                context=ctx,
            )
            results["rca"] = result.output_data or {}
            if not result.success:
                return self._failure_update(
                    state,
                    stage="rca",
                    error_message=result.error_message or "RCA agent returned an unsuccessful result",
                    agent_results=results,
                )

            rca_event_data = result.output_data.get("rca_event")
            if not rca_event_data:
                return self._failure_update(
                    state,
                    stage="rca",
                    error_message="RCA agent succeeded without a valid rca_event",
                    agent_results=results,
                )
            incident.rca_event = (
                RCAEvent(**rca_event_data)
                if isinstance(rca_event_data, dict)
                else rca_event_data
            )
            incident.context["rca_root_cause"] = result.output_data.get("root_cause", "unknown")
            incident.context["rca_confidence"] = result.output_data.get("confidence", 0)
            incident.transition_to(IncidentState.RCA_COMPLETED, actor="rca_agent")
            await self._notify(incident)
            return {
                "incident": incident,
                "current_phase": "decision",
                "agent_results": results,
            }
        except Exception as exc:
            logger.error("RCA node error", error=str(exc))
            return self._failure_update(
                state,
                stage="rca",
                error_message=str(exc) or type(exc).__name__,
                agent_results=results,
            )

    async def _node_decide_action(self, state: GraphState) -> dict[str, Any]:
        """将成功 RCA 导向 dry-run Heal。"""
        self._state = OrchestratorState.DECIDING_ACTION
        logger.info("Graph node: decide_action")
        return {"current_phase": "heal"}

    async def _node_execute_heal(self, state: GraphState) -> dict[str, Any]:
        """执行 dry-run Heal 并校验结果。"""
        self._state = OrchestratorState.EXECUTING_HEAL
        logger.info("Graph node: execute_heal")
        incident = state["incident"]
        incident.transition_to(IncidentState.HEALING, actor="heal_agent")
        results = dict(state["agent_results"])

        try:
            if incident.rca_event is None:
                return self._failure_update(
                    state,
                    stage="heal",
                    error_message="Heal cannot run without an rca_event",
                    agent_results=results,
                )
            result = await HealAgent().execute(
                HealInput(
                    rca_event=incident.rca_event,
                    incident_id=incident.incident_id,
                    dry_run=True,
                ),
                context=self._agent_context(incident),
            )
            results["heal"] = result.output_data or {}
            if not result.success:
                return self._failure_update(
                    state,
                    stage="heal",
                    error_message=result.error_message or "Heal agent returned an unsuccessful result",
                    agent_results=results,
                )
            if result.output_data.get("dry_run_passed") is not True:
                return self._failure_update(
                    state,
                    stage="heal",
                    error_message="Heal dry-run validation did not pass",
                    agent_results=results,
                )

            heal_event_data = result.output_data.get("heal_event")
            if not heal_event_data:
                return self._failure_update(
                    state,
                    stage="heal",
                    error_message="Heal agent succeeded without a valid heal_event",
                    agent_results=results,
                )
            heal_event = (
                HealEvent(**heal_event_data)
                if isinstance(heal_event_data, dict)
                else heal_event_data
            )
            incident.heal_events.append(heal_event)
            incident.context["heal_action"] = result.output_data.get("action", heal_event.action)
            incident.context["heal_level"] = result.output_data.get("heal_level", heal_event.level)
            await self._notify(incident)
            return {
                "incident": incident,
                "current_phase": "approval",
                "agent_results": results,
            }
        except Exception as exc:
            logger.error("Heal node error", error=str(exc))
            return self._failure_update(
                state,
                stage="heal",
                error_message=str(exc) or type(exc).__name__,
                agent_results=results,
            )

    async def _node_request_approval(self, state: GraphState) -> dict[str, Any]:
        """请求变更审批；pending 是合法暂停终态。"""
        self._state = OrchestratorState.AWAITING_APPROVAL
        logger.info("Graph node: request_approval")
        incident = state["incident"]
        incident.transition_to(IncidentState.AWAITING_APPROVAL, actor="change_agent")
        results = dict(state["agent_results"])

        try:
            if not incident.heal_events:
                return self._failure_update(
                    state,
                    stage="change",
                    error_message="Change approval cannot run without a heal_event",
                    agent_results=results,
                )
            result = await ChangeAgent().execute(
                ChangeInput(
                    heal_event=incident.heal_events[-1],
                    incident_id=incident.incident_id,
                    change_type="auto_heal",
                    requester="orchestrator",
                ),
                context=self._agent_context(incident),
            )
            results["change"] = result.output_data or {}
            if not result.success:
                return self._failure_update(
                    state,
                    stage="change",
                    error_message=(
                        result.error_message or "Change agent returned an unsuccessful result"
                    ),
                    agent_results=results,
                )

            change_event_data = result.output_data.get("change_event")
            if not change_event_data:
                return self._failure_update(
                    state,
                    stage="change",
                    error_message="Change agent succeeded without a valid change_event",
                    agent_results=results,
                )
            change_event = (
                ChangeEvent(**change_event_data)
                if isinstance(change_event_data, dict)
                else change_event_data
            )
            incident.change_events.append(change_event)
            approval_status = result.output_data.get("approval_status", "")
            incident.context["approval_status"] = approval_status
            incident.context["risk_score"] = result.output_data.get("risk_score", 0)

            if approval_status not in {"pending", "approved", "auto_approved"}:
                return self._failure_update(
                    state,
                    stage="change",
                    error_message=(
                        "Change approval did not permit resolution: "
                        f"{approval_status or 'missing'}"
                    ),
                    agent_results=results,
                    approval_status=approval_status,
                )
            await self._notify(incident)
            return {
                "incident": incident,
                "current_phase": (
                    "awaiting_approval" if approval_status == "pending" else "verification"
                ),
                "agent_results": results,
                "approval_status": approval_status,
                "completed": False,
            }
        except Exception as exc:
            logger.error("Change node error", error=str(exc))
            return self._failure_update(
                state,
                stage="change",
                error_message=str(exc) or type(exc).__name__,
                agent_results=results,
            )

    async def _node_verify_fix(self, state: GraphState) -> dict[str, Any]:
        """在审批通过且没有失败时标记图完成。"""
        self._state = OrchestratorState.VERIFYING
        logger.info("Graph node: verify_fix")
        if (
            state["escalated"]
            or state["errors"]
            or state["approval_status"] not in {"approved", "auto_approved"}
        ):
            return self._failure_update(
                state,
                stage="verification",
                error_message="Verification reached without an approved change",
            )
        return {"current_phase": "completion", "completed": True}

    async def _node_escalate(self, state: GraphState) -> dict[str, Any]:
        """将失败的图路径转为可观测的人工升级终态。"""
        self._state = OrchestratorState.ESCALATED
        logger.info("Graph node: escalate")
        incident = state["incident"]
        if "pipeline_failure" not in incident.context:
            error_message = state["errors"][-1] if state["errors"] else "Unknown graph failure"
            incident.context["pipeline_failure"] = {
                "stage": state["current_phase"] or "graph",
                "error": error_message,
            }
        if incident.state != IncidentState.ESCALATED:
            incident.transition_to(IncidentState.ESCALATED, actor="orchestrator")
        await self._notify(incident)
        return {
            "incident": incident,
            "current_phase": "escalated",
            "escalated": True,
            "completed": False,
        }

    async def _node_complete(self, state: GraphState) -> dict[str, Any]:
        """将经审批的 dry-run 结果标记为模拟解决。"""
        self._state = OrchestratorState.COMPLETED
        logger.info("Graph node: complete")
        incident = state["incident"]
        incident.context["resolution_mode"] = "simulated"
        if incident.state != IncidentState.RESOLVED:
            incident.transition_to(IncidentState.RESOLVED, actor="orchestrator")
        await self._notify(incident)
        return {
            "incident": incident,
            "current_phase": "completed",
            "completed": True,
            "escalated": False,
        }

    # === 条件边 ===

    def _edge_decide_action(self, state: GraphState) -> str:
        """RCA 失败升级，否则所有 severity 均进入 dry-run Heal。"""
        if state["escalated"] or state["errors"]:
            return "escalate"
        return "heal"

    def _edge_after_heal(self, state: GraphState) -> str:
        """Heal 失败升级，否则请求 Change 审批。"""
        if state["escalated"] or state["errors"]:
            return "escalate"
        return "approve"

    def _edge_after_approval(self, state: GraphState) -> str:
        """区分审批通过、pending 暂停和失败。"""
        if state["escalated"] or state["errors"]:
            return "escalate"
        if state["approval_status"] == "pending":
            return "pending"
        if state["approval_status"] in {"approved", "auto_approved"}:
            return "verify"
        return "escalate"

    def _edge_verify_fix(self, state: GraphState) -> str:
        """验证失败升级，只有显式 completed 才进入完成节点。"""
        if state["escalated"] or state["errors"] or not state["completed"]:
            return "escalate"
        return "completed"

    # === 内部辅助 ===

    def _agent_context(self, incident: Incident) -> AgentExecutionContext:
        """为 Agent execute() 构造共享执行上下文。"""
        return AgentExecutionContext(
            incident_id=incident.incident_id,
            metadata={
                "correlation_id": (
                    incident.alert_event.correlation_id if incident.alert_event else ""
                ),
            },
        )

    def _failure_update(
        self,
        state: GraphState,
        *,
        stage: str,
        error_message: str,
        agent_results: dict[str, Any] | None = None,
        approval_status: str | None = None,
    ) -> dict[str, Any]:
        """构造节点失败更新，同时写入图状态和 incident context。"""
        message = error_message or "Unknown error"
        errors = [*state["errors"], f"{stage}: {message}"]
        incident = state["incident"]
        incident.context["pipeline_failure"] = {
            "stage": stage,
            "error": message,
        }
        update: dict[str, Any] = {
            "incident": incident,
            "current_phase": stage,
            "errors": errors,
            "escalated": True,
            "completed": False,
        }
        if agent_results is not None:
            update["agent_results"] = agent_results
        if approval_status is not None:
            update["approval_status"] = approval_status
        return update

    def _escalate_incident(
        self,
        incident: Incident,
        *,
        stage: str,
        error_message: str,
    ) -> None:
        """处理图边界失败，不切换到其他隐式管道。"""
        message = error_message or "Unknown error"
        incident.context["pipeline_failure"] = {
            "stage": stage,
            "error": message,
        }
        incident.context["orchestrator.errors"] = [f"{stage}: {message}"]
        if incident.state != IncidentState.ESCALATED:
            incident.transition_to(IncidentState.ESCALATED, actor="orchestrator")
        self._state = OrchestratorState.ESCALATED
