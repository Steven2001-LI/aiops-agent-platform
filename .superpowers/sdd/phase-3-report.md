# Phase 3 Report: Tool 层真实化 (Tasks 3.1–3.4)

状态: **DONE_WITH_CONCERNS**

12 个 TODO 占位实现全部替换为真实基础设施调用（Prometheus / Playbook / Knowledge / SQLite），4 个文件均可导入，所有 `execute()` 均返回 `ToolResult` 实例。主要问题：brief 的替换代码与验证脚本引用了一批与当前代码库不符的类名/变量名（见下方 Concerns），已按实际方法签名做最小适配。

## 1. 4 个文件 diff 摘要

只修改了以下 4 个文件（未触碰 `app/data/` 或其他任何文件）。

### `app/tools/metrics_tools.py`
- 顶部新增 `from app.infrastructure.prometheus_client import PrometheusClient`。
- `QueryMetricsTool.execute`：3 行 TODO + 空返回 → `PrometheusClient().query_range(query, start, end, step)`，带 try/except，返回 `values/count/source`。
- `GetServiceMetricsTool.execute`：TODO + 空返回 → 两次 `query_range`（CPU、内存 PromQL），返回 `cpu_usage/memory_usage/source`。
- `DetectAnomaliesTool.execute`：TODO + 空返回 → 调用 `MonitorAgent.process(MetricInput, ctx)`，返回 `is_anomaly/score/confidence`。**适配**：brief 用 `values`，本方法参数为 `metric_data`，故在 try 内从 `metric_data` 派生 `values`（支持 `[ts,value]` 或标量）。

### `app/tools/playbook_tools.py`
- 顶部新增 `from app.data.playbooks import PLAYBOOKS` + `try/except ImportError` 导入 `PUBLIC_PLAYBOOKS`（按 brief 模式）。
- `GetPlaybookTool.execute`：TODO → 合并 `PLAYBOOKS+PUBLIC_PLAYBOOKS`，按 `fault_type` 过滤 `category/applicable_root_causes`，fallback 取第一个。
- `ExecutePlaybookStepTool.execute`：3 个 TODO → 按 `playbook_id` 查找、越界校验、`dry_run` 分支（模拟 vs 标注需 K8s/Ansible）。
- `ListPlaybooksTool.execute`：TODO → 返回全部 playbook 的 `id/name/category` 与 `count`。

### `app/tools/knowledge_tools.py`
- 顶部新增 `from app.data.knowledge_base import KNOWLEDGE_BASE, SERVICE_TOPOLOGY` + `try/except ImportError` 导入 `CHANGE_RECORDS`。
- `QueryKnowledgeBaseTool.execute`（brief 称 `SearchKnowledgeTool`）：2 个 TODO → 先 ChromaDB 向量检索，失败/空则回退内存 `KNOWLEDGE_BASE` 关键词打分。
- `QueryTopologyTool.execute`（brief 称 `GetServiceTopologyTool`）：TODO → 查 `SERVICE_TOPOLOGY`。**适配**：brief 用 `target_service`，本方法参数为 `service`，已改为 `service`。
- `QueryChangeHistoryTool.execute`（brief 称 `GetRecentChangesTool`）：TODO → 从 `CHANGE_RECORDS` 过滤 `service`。**适配**：brief 用 `window_minutes`（本方法无此参数），改为 `kwargs.get("window_minutes") or 60`。

### `app/tools/eval_tools.py`
- 顶部新增 `import os` / `import sqlite3`。
- `EvaluateOutputTool.execute`：2 个 TODO → 规则打分。**适配**：brief 用 `output`/`expected_keywords`，本方法参数为 `agent_output`/`criteria`，已相应映射。
- `GetBenchmarkResultsTool.execute`（brief 称 `QueryEvaluationHistoryTool`）：TODO → 查 SQLite `audit_logs`（`action='evaluation'`）。**适配**：brief 用 `limit`（本方法无此参数），改为 `kwargs.get("limit", 100)`。
- `LogFeedbackTool.execute`（brief 称 `StoreEvaluationFeedbackTool`）：TODO → 建表 `evaluation_feedback` 并 INSERT。**适配**：brief 用 `eval_id/feedback/metadata`，本方法参数为 `incident_id/comment/feedback_type`，映射为 `eval_id=incident_id`、`feedback=comment`、`metadata={"feedback_type": feedback_type}`。

## 2. Step 5 / Step 6 验证输出

### Step 5 (import)
- brief 字面脚本（`SearchKnowledgeTool` / `GetServiceTopologyTool` / `GetRecentChangesTool` / `QueryEvaluationHistoryTool` / `StoreEvaluationFeedbackTool`）→ **ImportError**，因为这些类名在代码库中不存在（brief 写错）。
- 用实际类名（`QueryKnowledgeBaseTool` / `QueryTopologyTool` / `QueryChangeHistoryTool` / `GetBenchmarkResultsTool` / `LogFeedbackTool`）→ 输出 `all tools importable`。

**结论：模块导入 PASS**（4 文件均干净导入，全部真实工具类可导入）；brief 的字面 import 片段因类名错误而失败。

### Step 6 (tool calls)
用实际类名调用（`GetServiceTopologyTool`→`QueryTopologyTool`，`SearchKnowledgeTool`→`QueryKnowledgeBaseTool`）：
```
playbooks: success=True  count=28
topology:  success=True  services=8
search:    success=True  matches=5   (命中 ChromaDB，非关键词回退)
```
三者均 `success=True` 且 count 为数字。

额外冒烟测试：其余 9 个被修改的 `execute()` 全部返回 `ToolResult` 实例（Prometheus 不可达时 `query_range` 降级为 `[]`，工具仍 `success=True`；`ExecutePlaybookStep` 对不存在的 playbook 返回 `success=False` 的错误 ToolResult —— 符合预期）。

## 3. 问题 / Concerns

1. **brief 类名/变量名与代码库不符**（最重要）。brief 的替换代码与 Step 5/6 验证脚本按一套已重命名的类/变量编写：
   - 类名：`SearchKnowledgeTool`/`GetServiceTopologyTool`/`GetRecentChangesTool`/`QueryEvaluationHistoryTool`/`StoreEvaluationFeedbackTool` 在库中实为 `QueryKnowledgeBaseTool`/`QueryTopologyTool`/`QueryChangeHistoryTool`/`GetBenchmarkResultsTool`/`LogFeedbackTool`。
   - 变量名：`values`(实为`metric_data`)、`target_service`(实为`service`)、`window_minutes`(无此参数)、`output`/`expected_keywords`(实为`agent_output`/`criteria`)、`limit`(无此参数)、`eval_id`/`feedback`/`metadata`(实为`incident_id`/`comment`/`feedback_type`)。
   - 未按字面粘贴，否则 `QueryTopologyTool` / `EvaluateOutputTool` 会因 `NameError` 抛异常而不返回 ToolResult，违反硬约束。已做最小适配（见 diff 中标注的“适配”）。**未重命名任何类**（会破坏 `register_*_tools()`）。
2. **PrometheusClient 会话未关闭**。brief 提供的模式 `client = PrometheusClient()` 后未 close，测试中出现 `Unclosed client session` 警告。按 brief 原样保留，未额外改动；后续可加 `async with` 或 `await client.close()`。
3. **Step 7（`_plan_log.md` 追加）已跳过**。用户级 Critical 约束为“ONLY modify the 4 tool files”，与 brief Step 7 冲突；以更严格的用户约束为准，未写 `_plan_log.md`。
4. `LogFeedbackTool`（brief 目标 `StoreEvaluationFeedbackTool`）存表 `evaluation_feedback`；语义为“记录反馈”，与 brief 意图一致。
