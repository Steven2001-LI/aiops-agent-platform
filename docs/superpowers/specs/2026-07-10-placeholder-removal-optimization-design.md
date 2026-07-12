# AIOps Agent Platform — 占位实现清理与全链路优化设计

> **日期**: 2026-07-10 | **范围**: 后端 Python 实现 + 测试 + 技术讲解稿
> **方法**: User-Story Driven (Approach C)

---

## 1. 目标

清理 `aiops-agent-platform` 后端代码中所有 `TODO`、`mock data`、占位实现，让"用户从 Web UI 提交告警 → 系统端到端处理 → 用户在 UI 看到真实结果"这条主链路 100% 由真实代码驱动，且 `/ready`、`/metrics`、评测体系、测试套件全部基于真实数据。

不改动的范围（明确划界）：
- **不修改**技术规范 `aiops_agent_technical_spec.md`（它是输入而非输出）
- **不修改**前端 React 代码（仅消费 API）
- **不强制要求**外部依赖（LangGraph、LLM API Key、K8s 集群），但要保证它们可用时被使用，不可用时优雅降级而非占位

---

## 2. 用户故事与断点清单

**主用户故事**: 运维用户在 Web UI 输入告警 → 系统处理 → UI 展示结果

沿此故事反向追溯，识别出 **11 个断点**：

| # | 断点位置 | 现状 | 后果 |
|---|----------|------|------|
| 1 | `backend/app/main.py` lifespan | 6 个 TODO 注释，**无任何初始化** | ChromaDB/MemorySystem/Agents 全部按需懒加载时第一次调用慢且可能失败 |
| 2 | `backend/app/main.py:124-168` `/metrics` | 手写 Prometheus 文本 | 不统计真实请求/Agent 调用 |
| 3 | `backend/app/main.py:180-188` `/ready` | 硬编码 `"connected"`/`"available"` | 永远返回 ready，无法反映真实状态 |
| 4 | `backend/app/agents/orchestrator.py:175-189` LangGraph 执行 | `pass` 跳过实际执行 | 永远走 fallback |
| 5 | `backend/app/agents/orchestrator.py:201-256` `_sequential_process` | 4 个 TODO，**不真调 Agent** | 故障处理主链路断裂 |
| 6 | `backend/app/agents/orchestrator.py:259-322` 9 个 LangGraph 节点方法 | 空 stub，只 `return state` | 即使 LangGraph 跑起来也什么都不做 |
| 7 | `backend/app/tools/{metrics,playbook,knowledge,eval}_tools.py` | 5+ 个 TODO，未调真实 API | Tool 层是空壳 |
| 8 | `backend/app/agents/eval_agent.py:500,608` `_eval_reasoning_with_mock_data` | 返回硬编码假指标 | 评测结果不可信 |
| 9 | `backend/app/agents/business_monitor_agent.py:339-349` `mock_results` 注入 | 允许 context 注入假结果 | 业务检测可被绕过 |
| 10 | `backend/app/api/routes.py:174-270` `_seed_incidents()` | 模块加载时塞 7 个假 incident | 演示数据冒充真实数据 |
| 11 | `backend/tests/conftest.py` `sys.path.insert(0, "/mnt/agents/...")` | 硬编码 Linux 路径 | 测试在本机跑不起来 |

---

## 3. 优化方案（按阶段）

### Phase 0 — 启动底座

**目标**: 让 `uvicorn app.main:app` 启动时真正初始化所有基础设施。

#### 改动
- `main.py` lifespan：
  - 同步初始化：`get_config()` 校验（LLM Key 缺失时给 warning 而非崩溃）
  - 异步初始化：尝试连接 ChromaDB → 失败则 fallback 到 InMemoryStorage
  - 异步初始化：`MemorySystem.get_instance()` 预热
  - 异步初始化：`KnowledgeBase` 加载（同步部分先做，向量入库走后台 task）
  - 启动 `Orchestrator` 并 `build_graph()`
  - 预热 Prometheus client（如果配置了 PROMETHEUS_URL）
  - 启动时输出"启动清单"日志：哪些后端已就绪、哪些 fallback
- `main.py` `/metrics`：用 `prometheus_client.Counter/Gauge/Histogram` 真实统计
  - `http_requests_total{method,path,status}`
  - `agent_invocations_total{agent,status}`
  - `incident_processing_seconds` Histogram
- `main.py` `/ready`：实际 ping 各依赖
  - ChromaDB: `client.heartbeat()` 或 `get_collection().count() >= 0`
  - Memory: `memory_system.status()` 返回 dict
  - Prometheus: `prometheus_client.health()` (新增)
  - 任一关键依赖失败 → 503
- 新增 `backend/app/infrastructure/__init__.py` 的 facade `InfrastructureRegistry`：单例管理所有外部 client

#### 验证
```bash
python -m uvicorn app.main:app --port 8000
# 应看到 "ChromaDB connected" / "MemorySystem ready" / "Orchestrator built"
curl localhost:8000/ready  # 应返回每个依赖的 200/503
curl localhost:8000/metrics  # 应有真实指标行
```

---

### Phase 1 — 主链路: Alert → Result

**目标**: 主用户故事 100% 由真实代码驱动。

#### 断点 4: LangGraph 执行被 pass

`orchestrator.process_alert` 当前结构：
```python
if self._compiled_graph:
    try:
        # ...构造初始状态...
        # result = await self._compiled_graph.ainvoke(initial_state)  # 被 pass 吞掉
        pass
    except Exception as e:
        ...
else:
    await self._sequential_process(incident)
```

**修复**:
- 真正调用 `await self._compiled_graph.ainvoke(initial_state)`
- 把 result 写回 incident（含 RCAEvent/HealEvent/ChangeEvent）
- 失败时降级到 sequential fallback 而非直接 ESCALATED

#### 断点 5: `_sequential_process` 4 个 TODO

**修复**: 真调 Agent
```python
# Step 2: 根因分析 — 替换 "# TODO: 调用 RCA Agent"
rca_result = await rca_agent.process(
    RCAInput(alert=alert, incident_id=incident.incident_id),
    context=AgentExecutionContext(...),
)
incident.rca_event = rca_result.output_data

# Step 3: 自愈 — 替换 "# TODO: 调用 Heal Agent"
heal_result = await heal_agent.process(
    HealInput(rca_event=..., incident_id=...),
    context=...,
)
incident.heal_event = heal_result.output_data

# Step 4: 变更审批 — 替换 "# TODO: 调用 Heal Agent"
change_result = await change_agent.process(...)
incident.change_event = change_result.output_data
```

#### 断点 6: LangGraph 9 个空 stub 节点

**修复**: 每个节点真正调用对应 Agent
```python
async def _node_run_rca(self, state):
    self._state = OrchestratorState.RUNNING_RCA
    rca_agent = await get_rca_agent()  # 从 DI 拿
    result = await rca_agent.process(RCAInput(alert=state["incident"].alert, ...), context)
    state["agent_results"]["rca"] = result.output_data
    state["incident"].rca_event = result.output_data
    return state
```

并修复条件边 `_edge_decide_action` 让它读 `state["agent_results"]["rca"]` 而非只看 severity。

#### 断点 10: `_seed_incidents()` 演示数据

**修复**:
- 删除模块级别的 `_seed_incidents()` 调用
- 把 seed 改为 `POST /api/v1/incidents/seed-demo` 端点，仅在 `APP_ENV=development` 时注册
- 改 `routes.py:270` 的 `_seed_incidents()` 调用为函数定义但不自动执行

#### 验证
```bash
curl -X POST localhost:8000/api/v1/incidents/trigger \
  -H "Content-Type: application/json" \
  -d '{
    "source": "manual",
    "service": "order-service",
    "metric": "cpu_usage_percent",
    "value": 95.0,
    "threshold": 80.0,
    "operator": ">",
    "severity": "critical",
    "labels": {"tier": "critical"},
    "annotations": {}
  }'

# 预期返回 incident_id，且 incident 状态最终为 COMPLETED 或 AWAITING_APPROVAL
# incident.rca_event / heal_event / change_event 均有真实数据
```

---

### Phase 2 — Tool 层真实化

**目标**: 5 个 tool 文件中的 TODO 全部用真实 API 调用替换。

#### 断点 7

| Tool 文件 | 现状 TODO | 真实实现 |
|-----------|----------|----------|
| `metrics_tools.py:91` | "调用 Prometheus/VictoriaMetrics API" | 调用 `app/infrastructure/prometheus_client.py:query_range` |
| `metrics_tools.py:154` | "查询服务指标面板" | 拼 PromQL → 调 PrometheusClient |
| `metrics_tools.py:217` | "调用异常检测算法" | 调用 `MonitorAgent._detect_anomaly` |
| `playbook_tools.py:59` | "从 Playbook 数据库查询" | 查 `data/playbooks.py:PLAYBOOKS` (内存常量) |
| `playbook_tools.py:127-129` | "加载步骤/执行命令/记录结果" | 真调 `HealAgent._execute_playbook` |
| `knowledge_tools.py:68-69` | "调用向量数据库检索" | 调 `ChromaDBStorage.search` |
| `knowledge_tools.py:128` | "调用拓扑数据库" | 查 `data/knowledge_base.py:SERVICE_TOPOLOGY` |
| `knowledge_tools.py:187` | "调用变更管理系统 API" | 暂留 TODO (外部依赖) + 在代码注释里说明需要真实接入 |
| `eval_tools.py:67-68` | "使用 LLM 评估输出质量" | 暂留 TODO (需 LLM API) + 实现无 LLM 版本的规则评分 |
| `eval_tools.py:124` | "从评估数据库查询" | 查 SQLite `audit_logs` 表 |
| `eval_tools.py:189` | "存储反馈到数据库" | 写 SQLite |

**保留为 TODO 的 (明确文档化)**:
- 真实 LLM 评估（需 OPENAI_API_KEY）
- 真实 K8s API 命令执行（需集群 RBAC）
- 真实 ArgoCD/GitLab API 接入（需 token）

这些都不是"占位实现"而是"未来扩展点"，会在讲解稿中明确标注。

---

### Phase 3 — 评测与业务检测去 mock

#### 断点 8: `eval_agent._eval_reasoning_with_mock_data`

**修复**:
- 删除该方法
- `_eval_reasoning` 在 `ground_truth` 和 `agent_results` 都为空时，**抛出 `ValueError("No data to evaluate")`**，让上游决定如何处理
- 在 `eval_agent.process()` 入口校验，至少要有 `agent_results`

#### 断点 9: `business_monitor_agent.mock_results` 注入

**修复**:
- 删除 `context.get("mock_results", {})` 分支
- 规则检测失败时直接 `raise BusinessRuleError(rule_id=...)`
- 在 `routes.py` 的 `trigger_business_incident` 端点不再传 `simulate_failure` 字段

#### 验证
```bash
# eval 端点传入真实数据
curl -X POST localhost:8000/api/v1/evaluations/run \
  -d '{"eval_type": "reasoning", "agent_results": [...], "ground_truth": {...}}'

# 业务检测端点不再支持 mock_results 字段
curl -X POST localhost:8000/api/v1/incidents/trigger-business \
  -d '{"service_name": "payment-service", "business_domain": "financial"}'
# 应返回真实检测结果或 raise
```

---

### Phase 4 — 测试套件修复与补全

#### 断点 11: 硬编码 Linux 路径

**修复**:
```python
# 旧:
sys.path.insert(0, "/mnt/agents/output/aiops-agent-platform/backend")

# 新:
import pathlib
_BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_ROOT))
```

#### 补全策略
- 删除所有依赖 `_seed_incidents()` 的测试用例（改用临时 fixture）
- 对 Phase 0-3 中补完的实现，**每个新代码路径至少 1 个测试**：
  - `test_main_lifespan.py`: 验证 lifespan 初始化
  - `test_orchestrator_sequential.py`: 验证 sequential fallback 真调 Agent
  - `test_orchestrator_langgraph.py`: 验证 LangGraph 节点（如果 LangGraph 可用，否则 skip）
  - `test_metrics_endpoint.py`: 验证 /metrics 真实指标
  - `test_ready_endpoint.py`: 验证 /ready 真实探活
  - `test_eval_no_mock.py`: 验证 EvalAgent 在无数据时抛错
  - `test_business_no_mock_injection.py`: 验证 mock_results 被拒
- 保留所有现有测试，但加 `@pytest.mark.real` 标记需要外部依赖的（如真 LLM 调用、真 Prometheus）

#### 验证
```bash
cd backend && pytest -v
# 所有非 @pytest.mark.real 测试必须通过
pytest -v -m "not real"  # 同上但显式排除
```

---

### Phase 5 — 技术讲解稿重写

**目标**: 让讲解稿的每个代码引用、行号、流程图都对应最新代码。

#### 改写策略
1. **保留章节**（结构性内容不变）:
   - 1.1 设计理念
   - 1.2 架构总览（流程图结构不变）
   - 1.3 技术栈
   - 第二部分模块职责说明（NLU/Memory/RCA/Heal/Change 等）
   - 第四部分评测体系架构

2. **必须更新**（行号/代码引用）:
   - 所有 `(file.py:line)` 引用 — 用优化后的真实行号
   - 所有代码片段 — 优化后的实际代码
   - 第五部分"踩坑点"表格 — 移除已修复的占位问题，新增本次修复

3. **新增章节**: "本次优化过程" 放在末尾，包含：
   - 11 个断点清单
   - 每个断点的优化前/后对比（代码 diff）
   - 验证结果（curl 输出、pytest 输出片段）
   - 已知遗留（K8s API、ArgoCD API、LLM 真实评估）

4. **新增章节**: "运行验证指南"
   - 一键启动命令
   - 健康检查命令
   - 主链路端到端 curl 示例
   - 跑测试命令

---

## 4. 验证策略汇总

每完成一个 Phase 必须全部通过：

| 验证项 | 命令 | 通过标准 |
|--------|------|----------|
| 启动 | `python -m uvicorn app.main:app` | 日志显示所有依赖就绪清单，无 ERROR |
| 健康检查 | `curl localhost:8000/ready` | 所有依赖返回 200 |
| Metrics | `curl localhost:8000/metrics` | 包含真实指标行（`http_requests_total`, `agent_invocations_total`） |
| 主链路 | `curl -X POST .../api/v1/incidents/trigger -d @sample.json` | incident 走完 COMPLETED 或 AWAITING_APPROVAL，所有 event 字段非空 |
| 评测 | `curl -X POST .../api/v1/evaluations/run -d '{...真实数据...}'` | 返回真实 ReasoningMetrics，无 mock |
| 业务检测 | `curl -X POST .../api/v1/incidents/trigger-business -d '{...}'` | 返回真实规则结果，mock_results 被拒 |
| 测试 | `cd backend && pytest -v -m "not real"` | 全部通过 |

---

## 5. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| LangGraph API 不兼容 | sequential fallback 是关键路径 | sequential fallback 必须自洽完整；LangGraph 缺失时不阻塞 |
| ChromaDB 启动慢 | lifespan 卡顿 | 启动 timeout 5s；失败后 InMemoryStorage 兜底 |
| LLM Key 缺失 | 真评估跑不起来 | 注入 `MockLLMClient` 用于 `LLM_API_KEY` 为空场景，并在 `/ready` 中标注 |
| Heal 真执行危险 | 可能误操作 | 保留 `heal_dry_run=True` 默认值 |
| 测试在本机跑不动 | 验证阻塞 | Phase 4 优先修 conftest，再补测试 |
| 文档与代码漂移 | 维护成本 | 重写文档时同步 commit，且在 PR 中要求文档与代码同步更新 |

---

## 6. 任务清单概览（详细 plan 见 writing-plans 输出）

```
Phase 0 启动底座 (3 files)
  - main.py (lifespan + /metrics + /ready)
  - infrastructure/__init__.py (新增 facade)
  - config.py (校验增强)

Phase 1 主链路 (3 files)
  - orchestrator.py (LangGraph 执行 + sequential 真调 + 节点真实现)
  - routes.py (删除自动 seed，新增 demo 端点)
  - main.py (routes 注册)

Phase 2 Tool 层 (4 files)
  - metrics_tools.py / playbook_tools.py / knowledge_tools.py / eval_tools.py

Phase 3 评测去 mock (2 files)
  - eval_agent.py
  - business_monitor_agent.py
  - routes.py (trigger_business)

Phase 4 测试 (3 files)
  - tests/conftest.py (修路径)
  - 新增 7 个 test_*.py
  - 标记 @pytest.mark.real

Phase 5 文档 (1 file)
  - 技术讲解稿-Agent协作故障定位全流程.md (重写)
```

---

## 7. 不在范围内（明确排除）

- **不重写**前端代码（除非 API 契约变更）
- **不修改**技术规范文档 `aiops_agent_technical_spec.md`
- **不接入**真实 K8s 集群（保留接口但默认 dry-run）
- **不接入**真实 ArgoCD/GitLab（保留 TODO 注释，明确文档化）
- **不强制**所有 Agent 调用真实 LLM（MockLLMClient 兜底）
- **不优化**性能（先做正确性，性能是后续）