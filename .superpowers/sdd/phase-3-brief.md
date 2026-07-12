# Phase 3 Brief: Tool 层真实化 (Tasks 3.1 + 3.2 + 3.3 + 3.4)

## 项目上下文
Phase 0-2 完成。Phase 3 修复 `backend/app/tools/` 下 4 个文件的 TODO 占位实现，让 tools 真正调用底层基础设施（Prometheus / Playbook / Knowledge / SQLite）而非返回空数据。

**只改文件**: `metrics_tools.py` / `playbook_tools.py` / `knowledge_tools.py` / `eval_tools.py`
**Tasks**: 3.1 + 3.2 + 3.3 + 3.4
**约束**: 4 个文件都使用 `BaseTool` + `ToolResult.ok(...)` 模式（见 `app/tools/base.py`）。execute() 必须返回 `ToolResult.ok(data={...})` 或 `ToolResult.error(error_message=...)`，不能 return None。

## 已验证 API

- `PrometheusClient` 在 `app/infrastructure/prometheus_client.py`：
  - `PrometheusClient(base_url=None)` → 默认读 env `PROMETHEUS_URL`
  - `await client.query_range(promql, start, end, step)` → `list[float]`
  - `await client.health_check()` → `dict` (含 `reachable` key)
- `ChromaDBStorage` 在 `app/memory/storage.py`：
  - `ChromaDBStorage(collection_name, persist_directory)` → 默认 `aiops_memory` / `./data/chromadb`
  - `await storage._ensure_initialized()` 私有方法（OK to call）
  - `await storage.search(query, top_k)` → list of entries (实际接口见 storage.py)
- `SERVICE_TOPOLOGY` 在 `app/data/knowledge_base.py`：模块级 dict
- `KNOWLEDGE_BASE` 在 `app/data/knowledge_base.py`：模块级 list
- `PLAYBOOKS` 在 `app/data/playbooks.py`：模块级 list
- `CHANGE_RECORDS` 在 `app/data/datasets.py`：模块级 list
- `audit_logs` 表在 SQLite `./data/aiops.db`（schema 见 design doc §7.4）

## 步骤

### Step 1: 修 `backend/app/tools/metrics_tools.py`

文件顶部 import 区新增：
```python
from app.infrastructure.prometheus_client import PrometheusClient
```

定位 line 91-93 的 `# TODO: 调用 Prometheus/VictoriaMetrics API` 等三个 TODO，把 execute 方法里 `# TODO: 调用 Prometheus/VictoriaMetrics API / 解析返回数据 / 异常处理` 整段（3 行注释 + 之后的空行）替换为：

```python
        # 真实调用 Prometheus
        try:
            client = PrometheusClient()
            from datetime import datetime, timezone, timedelta
            end = datetime.now(timezone.utc)
            start = end - timedelta(hours=1)
            values = await client.query_range(query, start=start, end=end, step=step)
            return ToolResult.ok(
                tool_name=self.name,
                data={
                    "query": query,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "step": step,
                    "values": values,
                    "count": len(values),
                    "source": "prometheus",
                },
            )
        except Exception as e:
            logger.error("Prometheus query failed", query=query, error=str(e))
            return ToolResult.error(
                tool_name=self.name,
                error_message=f"Prometheus query failed: {e}",
            )
```

定位 line 154 的 `# TODO: 查询服务指标面板`，把 `return ToolResult.ok(...)` 前的那行 TODO 替换为真实调用：

```python
        try:
            client = PrometheusClient()
            from datetime import datetime, timezone, timedelta
            end = datetime.now(timezone.utc)
            start = end - timedelta(hours=1)
            cpu = await client.query_range(
                '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)',
                start=start, end=end, step="1m",
            )
            mem = await client.query_range(
                '(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100',
                start=start, end=end, step="1m",
            )
            return ToolResult.ok(
                tool_name=self.name,
                data={
                    "service": service,
                    "environment": environment,
                    "time_range": time_range,
                    "cpu_usage": cpu,
                    "memory_usage": mem,
                    "source": "prometheus",
                },
            )
        except Exception as e:
            return ToolResult.error(
                tool_name=self.name,
                error_message=f"Service metrics fetch failed: {e}",
            )
```

定位 line 217 的 `# TODO: 调用异常检测算法`（在 `DetectAnomaliesTool.execute` 内），把 TODO 替换为：

```python
        try:
            from app.agents.monitor_agent import MonitorAgent, MetricInput
            from app.agents.base import AgentExecutionContext
            monitor = MonitorAgent()
            metric_input = MetricInput(
                metric_name="cpu_usage_percent",
                metric_value=values[-1] if values else 0,
                service_name="unknown",
                history_values=values or [],
            )
            ctx = AgentExecutionContext(incident_id="tool-detect-anomaly")
            result = await monitor.process(metric_input, ctx)
            detection = (result.output_data or {}).get("detection_result", {})
            return ToolResult.ok(
                tool_name=self.name,
                data={
                    "is_anomaly": detection.get("is_anomaly", False),
                    "score": detection.get("score", 0.0),
                    "confidence": detection.get("confidence", 0.0),
                    "source": "monitor_agent",
                },
            )
        except Exception as e:
            return ToolResult.error(
                tool_name=self.name,
                error_message=f"Anomaly detection failed: {e}",
            )
```

### Step 2: 修 `backend/app/tools/playbook_tools.py`

顶部 import 区新增：
```python
from app.data.playbooks import PLAYBOOKS
try:
    from app.data.public_playbooks import PUBLIC_PLAYBOOKS
except ImportError:
    PUBLIC_PLAYBOOKS = []
```

定位 line 59 的 `# TODO: 从 Playbook 数据库查询`，把 execute 里的 TODO 替换为：
```python
        all_pbs = list(PLAYBOOKS) + list(PUBLIC_PLAYBOOKS)
        # 按 fault_type 过滤（匹配 category 或 applicable_root_causes）
        matched = [
            pb for pb in all_pbs
            if fault_type and (fault_type in str(pb.get("category", ""))
                               or fault_type in str(pb.get("applicable_root_causes", [])))
        ]
        if not matched and all_pbs:
            matched = all_pbs[:1]  # fallback: 返回第一个
        pb = matched[0] if matched else {}
        return ToolResult.ok(
            tool_name=self.name,
            data={
                "playbook_id": pb.get("id", ""),
                "name": pb.get("name", ""),
                "description": pb.get("description", ""),
                "steps": pb.get("actions", pb.get("steps", [])),
                "fault_type": fault_type,
                "service": service,
                "source": "playbooks_db",
            },
        )
```

定位 line 127-129 的三个 TODO（在 `ExecutePlaybookStepTool.execute` 内），把三个 TODO 替换为：
```python
        pb = None
        for p in list(PLAYBOOKS) + list(PUBLIC_PLAYBOOKS):
            if p.get("id") == playbook_id:
                pb = p
                break
        if not pb:
            return ToolResult.error(
                tool_name=self.name,
                error_message=f"Playbook {playbook_id} not found",
            )
        steps = pb.get("actions", pb.get("steps", []))
        if step_index >= len(steps):
            return ToolResult.error(
                tool_name=self.name,
                error_message=f"Step index {step_index} out of range",
            )
        step = steps[step_index]
        # dry_run=True 时仅生成命令不执行
        if dry_run:
            return ToolResult.ok(
                tool_name=self.name,
                data={
                    "playbook_id": playbook_id,
                    "step_index": step_index,
                    "step": step,
                    "dry_run": True,
                    "executed": False,
                    "simulated_command": step.get("type", ""),
                },
            )
        # 真执行：当前留空，标注"需对接 K8s/Ansible"
        return ToolResult.ok(
            tool_name=self.name,
            data={
                "playbook_id": playbook_id,
                "step_index": step_index,
                "step": step,
                "dry_run": False,
                "executed": False,
                "note": "Real execution requires K8s/Ansible integration (see Phase 3 spec '已知遗留')",
            },
        )
```

定位 line 178 的 `# TODO: 从 Playbook 数据库查询`（在另一个 tool 内），把 TODO 替换为：
```python
        all_pbs = list(PLAYBOOKS) + list(PUBLIC_PLAYBOOKS)
        return ToolResult.ok(
            tool_name=self.name,
            data={
                "playbooks": [
                    {"id": pb.get("id"), "name": pb.get("name"), "category": pb.get("category", "")}
                    for pb in all_pbs
                ],
                "count": len(all_pbs),
                "source": "playbooks_db",
            },
        )
```

### Step 3: 修 `backend/app/tools/knowledge_tools.py`

顶部 import 区新增：
```python
from app.data.knowledge_base import KNOWLEDGE_BASE, SERVICE_TOPOLOGY
try:
    from app.data.datasets import CHANGE_RECORDS
except ImportError:
    CHANGE_RECORDS = []
```

定位 line 68-69 的 `# TODO: 调用向量数据库检索 / 结合关键词过滤`（在 `SearchKnowledgeTool.execute`），替换为：
```python
        query_lower = str(query).lower()
        # 先尝试 ChromaDB 向量检索
        try:
            from app.memory.storage import ChromaDBStorage
            storage = ChromaDBStorage()
            await storage._ensure_initialized()
            results = await storage.search(query, top_k=top_k)
            if results:
                return ToolResult.ok(
                    tool_name=self.name,
                    data={
                        "query": query,
                        "results": results,
                        "count": len(results),
                        "source": "chromadb",
                    },
                )
        except Exception as e:
            logger.debug("ChromaDB search unavailable, using keyword fallback", error=str(e))

        # Fallback: 在内存 KNOWLEDGE_BASE 中做关键词匹配
        matched = []
        for entry in KNOWLEDGE_BASE:
            content = str(entry.get("content", "")).lower()
            tags = " ".join(entry.get("tags", [])).lower()
            score = 0.0
            if query_lower in content:
                score += 0.5
            for word in query_lower.split():
                if word in content:
                    score += 0.1
                if word in tags:
                    score += 0.2
            if score > 0:
                e_copy = dict(entry)
                e_copy["score"] = score
                matched.append(e_copy)
        matched.sort(key=lambda x: x["score"], reverse=True)
        return ToolResult.ok(
            tool_name=self.name,
            data={
                "query": query,
                "results": matched[:top_k],
                "count": len(matched[:top_k]),
                "source": "keyword_fallback",
            },
        )
```

定位 line 128 的 `# TODO: 调用拓扑数据库`，替换为：
```python
        target = str(target_service) if target_service else None
        if target:
            svc = SERVICE_TOPOLOGY.get(target, {})
            return ToolResult.ok(
                tool_name=self.name,
                data={
                    "service": target,
                    "dependencies": svc.get("dependencies", []),
                    "tier": svc.get("tier", "standard"),
                    "team": svc.get("team", ""),
                    "source": "service_topology",
                },
            )
        return ToolResult.ok(
            tool_name=self.name,
            data={
                "services": list(SERVICE_TOPOLOGY.keys()),
                "topology": SERVICE_TOPOLOGY,
                "count": len(SERVICE_TOPOLOGY),
                "source": "service_topology",
            },
        )
```

定位 line 187 的 `# TODO: 调用变更管理系统 API`，替换为：
```python
        svc = str(service) if service else None
        if not svc:
            return ToolResult.error(
                tool_name=self.name,
                error_message="service parameter required",
            )
        try:
            cutoff = window_minutes or 60
            results = [cr for cr in CHANGE_RECORDS if cr.get("service") == svc]
            return ToolResult.ok(
                tool_name=self.name,
                data={
                    "service": svc,
                    "window_minutes": cutoff,
                    "changes": results,
                    "count": len(results),
                    "source": "datasets_internal",
                    "note": "Real ArgoCD/GitLab API integration pending (see Phase 3 '已知遗留')",
                },
            )
        except Exception as e:
            return ToolResult.error(
                tool_name=self.name,
                error_message=f"get_recent_changes failed: {e}",
            )
```

### Step 4: 修 `backend/app/tools/eval_tools.py`

顶部 import 区新增：
```python
import os
import sqlite3
```

定位 line 67-68 的两个 TODO（在第一个 tool 内），替换为：
```python
        # 无 LLM 时的规则评分；如 LLM 可用则可升级（见 Phase 3 '已知遗留'）
        score = 0.0
        if output:
            text = str(output)
            text_lower = text.lower()
            # 简单启发式：关键词匹配期望内容
            for kw in expected_keywords or []:
                if str(kw).lower() in text_lower:
                    score += 1.0 / max(len(expected_keywords), 1)
            score = min(score, 1.0)
        return ToolResult.ok(
            tool_name=self.name,
            data={
                "score": score,
                "matched_keywords": [
                    kw for kw in (expected_keywords or [])
                    if str(kw).lower() in str(output).lower()
                ],
                "method": "rule_based_fallback",
                "note": "LLM-based evaluation pending (see Phase 3 '已知遗留')",
            },
        )
```

定位 line 124 的 `# TODO: 从评估数据库查询`，替换为：
```python
        db_path = os.getenv("SQLITE_PATH", "./data/aiops.db")
        if not os.path.exists(db_path):
            return ToolResult.ok(
                tool_name=self.name,
                data={"history": [], "count": 0, "source": "no_db"},
            )
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM audit_logs WHERE action='evaluation' "
                "ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            return ToolResult.ok(
                tool_name=self.name,
                data={"history": rows, "count": len(rows), "source": "sqlite"},
            )
        except Exception as e:
            return ToolResult.error(
                tool_name=self.name,
                error_message=f"query_evaluation_history failed: {e}",
            )
```

定位 line 189 的 `# TODO: 存储反馈到数据库`，替换为：
```python
        db_path = os.getenv("SQLITE_PATH", "./data/aiops.db")
        try:
            os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS evaluation_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    eval_id VARCHAR(64) NOT NULL,
                    feedback TEXT,
                    rating FLOAT,
                    metadata TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            cur.execute(
                "INSERT INTO evaluation_feedback (eval_id, feedback, rating, metadata) "
                "VALUES (?, ?, ?, ?)",
                (eval_id, feedback, rating, str(metadata or {})),
            )
            conn.commit()
            conn.close()
            return ToolResult.ok(
                tool_name=self.name,
                data={"stored": True, "eval_id": eval_id},
            )
        except Exception as e:
            return ToolResult.error(
                tool_name=self.name,
                error_message=f"store_evaluation_feedback failed: {e}",
            )
```

### Step 5: 验证 import + 4 个 tool 文件可调用

```bash
cd /Users/apple/资料/大模型/项目/运维多智能体故障定位/aiops-agent-platform/backend
/usr/bin/python3 -c "
from app.tools.metrics_tools import QueryMetricsTool, GetServiceMetricsTool, DetectAnomaliesTool
from app.tools.playbook_tools import GetPlaybookTool, ExecutePlaybookStepTool, ListPlaybooksTool
from app.tools.knowledge_tools import SearchKnowledgeTool, GetServiceTopologyTool, GetRecentChangesTool
from app.tools.eval_tools import EvaluateOutputTool, QueryEvaluationHistoryTool, StoreEvaluationFeedbackTool
print('all tools importable')
"
```
Expected: `all tools importable`

### Step 6: 验证每个 tool 可调用且返回 ToolResult

```bash
cd /Users/apple/资料/大模型/项目/运维多智能体故障定位/aiops-agent-platform/backend
/usr/bin/python3 -c "
import asyncio
from app.tools.playbook_tools import ListPlaybooksTool
from app.tools.knowledge_tools import GetServiceTopologyTool, SearchKnowledgeTool

async def main():
    pbs = await ListPlaybooksTool().execute()
    print('playbooks:', pbs.success, 'count=', pbs.data.get('count'))
    topo = await GetServiceTopologyTool().execute()
    print('topology:', topo.success, 'services=', topo.data.get('count'))
    sk = await SearchKnowledgeTool().execute(query='cpu high')
    print('search:', sk.success, 'matches=', sk.data.get('count'))

asyncio.run(main())
"
```
Expected: 三个都 success=True，count 都是数字

### Step 7: 记录

`_plan_log.md` 追加：
```
Task 3.1-3.4 — tools 层真实化  ✅  <时间戳>
```

## 报告要求

完成后在 `.superpowers/sdd/phase-3-report.md` 写入：
1. 4 个文件 diff 摘要
2. Step 5-6 验证输出
3. 任何问题

## 约束
- 只改 4 个 tools/ 文件
- 用 `/usr/bin/python3` 验证
- 不要修改 `app/data/` 下的数据文件
- 所有 execute() 必须返回 ToolResult 实例，不能 return None