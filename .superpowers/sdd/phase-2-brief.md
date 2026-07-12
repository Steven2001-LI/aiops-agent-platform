# Phase 2 Brief: Orchestrator 补完 (Tasks 2.1 + 2.2 + 2.3)

## 项目上下文
Phase 0+1 完成。Phase 2 修复 `orchestrator.py` 的三个核心断点：
- `_sequential_process` (line 201) 含 4 个 TODO，不真调 Agent
- `process_alert` (line 153) 中 LangGraph 执行被 `pass` 吞掉
- 9 个 LangGraph 节点方法（lines 259-322）全部空 stub

**只改文件**: `backend/app/agents/orchestrator.py`
**Tasks**: 2.1 + 2.2 + 2.3

## 关键约束

**Imports 当前已有**（orchestrator.py 顶部）：
```python
from app.models.events import AlertEvent, SeverityLevel  # 已经存在
```
你需要新增：
```python
from app.agents.base import AgentExecutionContext
from app.agents.rca_agent import RCAAgent, RCAInput
from app.agents.heal_agent import HealAgent, HealInput
from app.agents.change_agent import ChangeAgent, ChangeInput
from app.models.events import RCAEvent, HealEvent, ChangeEvent  # 扩 AlertEvent 的同 import
```
注意 `RCAEvent/HealEvent/ChangeEvent` 也在 `app.models.events`，把它们加入现有 `from app.models.events import ...` 行（避免重复 import）。

**不能 import** `app.agents.monitor_agent`（避免循环依赖 — MonitorAgent 在 sequential pipeline 里被 `routes.py` 直接调用，不走 orchestrator）。如果某个节点需要 monitor 数据，从 `state` 读。

## 步骤

### Step 1: 在 orchestrator.py 顶部 import 区追加

修改第 14 行附近的：
```python
from app.models.events import AlertEvent, SeverityLevel
```

替换为：
```python
from app.agents.base import AgentExecutionContext
from app.agents.change_agent import ChangeAgent, ChangeInput
from app.agents.heal_agent import HealAgent, HealInput
from app.agents.rca_agent import RCAAgent, RCAInput
from app.models.events import (
    AlertEvent,
    ChangeEvent,
    HealEvent,
    RCAEvent,
    SeverityLevel,
)
```

如果 `SeverityLevel` 不在原 import 中，新增它。

### Step 2: 替换 `process_alert` 中 LangGraph 执行段（Task 2.2）

定位 line 153 起的 `async def process_alert(self, alert: AlertEvent) -> Incident:` 函数，找到里面 `# TODO: 如果 LangGraph 可用，执行图编排` 注释（line 175），整段（从该注释到函数末尾 line 199 的 `return incident`）替换为：

```python
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
```

### Step 3: 替换 `_sequential_process` 整段（Task 2.1）

定位 line 201-256 的 `async def _sequential_process(self, incident: Incident) -> None:` 整段（从 def 开始到最后一个 add_timeline_entry），替换为：

```python
    async def _sequential_process(self, incident: Incident) -> None:
        """
        顺序处理流程（Fallback 模式）

        当 LangGraph 不可用时，按顺序执行各 Agent。
        每个 Agent 的真实结果写回 incident 对应字段。
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
```

### Step 4: 替换 9 个 LangGraph 节点方法（Task 2.3）

定位 line 259-322（`# === LangGraph 节点方法 ===` 之后到 `# === 条件边方法 ===` 之前的所有节点函数），整段替换为：

```python
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
```

### Step 5: 替换 `_edge_decide_action`（Task 2.3 一部分）

定位 line 326-342 的 `def _edge_decide_action`，替换为：

```python
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
```

### Step 6: 验证 import 与图构建

```bash
cd /Users/apple/资料/大模型/项目/运维多智能体故障定位/aiops-agent-platform/backend
/usr/bin/python3 -c "
from app.agents.orchestrator import Orchestrator
o = Orchestrator()
g = o.build_graph()
print('graph:', g is not None)
print('OK')
"
```
Expected: `graph: True` 或 `False`（取决于 LangGraph 是否安装），无 TypeError/ImportError，最终打印 OK

### Step 7: 验证 sequential fallback 端到端

```bash
cd /Users/apple/资料/大模型/项目/运维多智能体故障定位/aiops-agent-platform/backend
/usr/bin/python3 -c "
import asyncio
from datetime import datetime, timezone
from app.agents.orchestrator import Orchestrator
from app.models.events import AlertEvent, SeverityLevel
from app.models.incident import Incident

async def main():
    o = Orchestrator()
    alert = AlertEvent(
        source='test',
        service='order-service',
        metric='cpu_usage_percent',
        value=95.0,
        threshold=80.0,
        operator='>',
        severity=SeverityLevel.HIGH,
        labels={'tier': 'critical'},
        annotations={},
        timestamp=datetime.now(timezone.utc),
    )
    incident = Incident.from_alert(alert)
    await o._sequential_process(incident)
    print('rca_event:', incident.rca_event is not None)
    print('rca_root_cause:', incident.context.get('rca_root_cause', 'NONE'))
    print('heal_events count:', len(incident.heal_events))
    print('change_events count:', len(incident.change_events))

asyncio.run(main())
"
```
Expected: 全部非 NONE/0（除非 RCA 真实失败）

### Step 8: 记录

`_plan_log.md` 追加：
```
Task 2.1-2.3 — orchestrator.py 补完 sequential + LangGraph  ✅  <时间戳>
```

## 报告要求

完成后在 `.superpowers/sdd/phase-2-report.md` 写入：
1. 修改 diff 摘要（4 处替换：imports / process_alert / _sequential_process / 节点方法 + _edge_decide_action）
2. Step 6 输出
3. Step 7 端到端输出
4. 任何问题

## 约束
- 只改 `orchestrator.py`
- 不要修改 routes.py（routes.py 已有 `_process_incident_pipeline`，与 orchestrator 独立）
- 用 `/usr/bin/python3` 跑验证
- 不能 `import app.agents.monitor_agent`（避免循环依赖）