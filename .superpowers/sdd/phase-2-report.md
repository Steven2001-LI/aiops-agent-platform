# Phase 2 Report — Orchestrator 补完 (Tasks 2.1 + 2.2 + 2.3)

## 修改 diff 摘要

仅修改文件：`backend/app/agents/orchestrator.py`

### 1. Imports（顶部）
原：
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
未 import `app.agents.monitor_agent`（避免循环依赖）。

### 2. `process_alert`（Task 2.2）
原：LangGraph 执行被 `pass` 吞掉，fallback 直接 escalate。
现：真正调用 `await self._compiled_graph.ainvoke(initial_state)`；处理 escalated/completed/未完成 三种结果分支；graph 执行失败或不可用时回落到 `_sequential_process`。

### 3. `_sequential_process`（Task 2.1）
原：4 个 TODO stub，不调 Agent。
现：完整流程 —— 创建 `AgentExecutionContext` → 分类分级 transition → 调 `RCAAgent.process` 写回 `incident.rca_event` + `context["rca_root_cause"]` → 根据 severity 决策（CRITICAL/HIGH → 走审批；其他 → 调 `HealAgent.process` 写回 `incident.heal_events`）→ 调 `ChangeAgent.process` 写回 `incident.change_events` + `context["approval_status"]` → transition 到 RESOLVED/COMPLETED。

### 4. 9 个 LangGraph 节点方法（Task 2.3）
- `_node_receive_alert` / `_node_triage`: 仅 transition ACKNOWLEDGED
- `_node_run_rca`: 调 `RCAAgent.process`，写回 `incident.rca_event` 和 `state["agent_results"]["rca"]`
- `_node_decide_action`: 占位（由 `_edge_decide_action` 决定路由）
- `_node_execute_heal`: 调 `HealAgent.process`，append 到 `incident.heal_events`
- `_node_request_approval`: 调 `ChangeAgent.process`，append 到 `incident.change_events`
- `_node_verify_fix`: transition RESOLVED，set `state["completed"] = True`
- `_node_escalate` / `_node_complete`: 标记状态

### 5. `_edge_decide_action`
CRITICAL → "approve"，HIGH/MEDIUM/LOW → "heal"，无 incident → "escalate"，completed → "complete"。

---

## Step 6 — 图构建验证

命令：
```bash
cd backend && /usr/bin/python3 -c "from app.agents.orchestrator import Orchestrator; o=Orchestrator(); g=o.build_graph(); print('graph:', g is not None); print('OK')"
```

输出：
```
[info] LangGraph orchestrator built successfully
graph: True
OK
```

**Step 6: PASS**

---

## Step 7 — Sequential 端到端验证

输入：HIGH severity alert (order-service, cpu_usage_percent=95.0)

输出：
```
rca_event: True
rca_root_cause: traffic_spike
heal_events count: 0
change_events count: 1
```

**说明**：
- `rca_event` 已填充（TRUE），`rca_root_cause = traffic_spike`（来自贝叶斯 + RAG 综合分析）
- `heal_events count = 0`：因 severity=HIGH，按 brief 设计走 `AWAITING_APPROVAL` 分支跳过 HealAgent（与 brief 中 `if incident.severity in (SeverityLevel.CRITICAL, SeverityLevel.HIGH)` 逻辑一致）
- `change_events count = 1`：ChangeAgent 正常生成 ChangeEvent（risk_score=0.395, status=pending）

---

## 任何问题

无阻塞问题。Step 6 与 Step 7 均通过。

一处观察（非问题）：Step 7 中 `heal_events count = 0` 是 brief 中 severity 逻辑的预期行为（HIGH/CRITICAL 走审批路径），不是失败。若希望同时跑 HealAgent 验证，可在 `_sequential_process` 中调整顺序或在 `_edge_decide_action` 中让 HIGH 也走 heal。