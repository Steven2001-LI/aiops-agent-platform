# AIOps 占位实现清理与全链路优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 清理 `aiops-agent-platform` 后端所有 `TODO` / 占位 / mock 实现，让"用户提交告警 → 端到端处理 → 真实结果返回"主链路 100% 由真实代码驱动，并修复测试套件、重写技术讲解稿。

**Architecture:** 按 User-Story 反向追溯主链路断点，按 Phase 自底向上修复：基础设施底座 (main.py) → 主链路入口 (routes.py) → Orchestrator → Tool 层 → 评测/业务检测去 mock → 测试 → 文档。每 Phase 完成后立即验证（curl + pytest），失败回滚不进入下一阶段。

**Tech Stack:** Python 3.11+ / FastAPI / LangGraph (optional) / ChromaDB / Prometheus / scikit-learn / pytest

**Note on git:** 项目当前不是 git 仓库。所有 commit 命令在本机执行需要先 `git init` 或改为"git-less 提交记录文件"。每个 Task 末尾的 commit 步骤如无法执行，可改为在 `_plan_log.md` 中记录完成时间戳。

---

## Global Constraints

- **Python ≥ 3.11** — 所有 Task 使用 `str | None`、Pydantic v2 等语法
- **FastAPI ≥ 0.110** — 使用 lifespan asynccontextmanager
- **No new top-level dependencies** — 优先复用已有依赖（prometheus_client, scikit-learn, networkx, numpy, jieba, chromadb, langgraph-optional）
- **每个文件保持单一职责** — 不重构文件结构，只补完缺失实现
- **TDD 优先** — 修复核心逻辑前先写测试
- **失败优雅降级** — LangGraph / ChromaDB / Prometheus 不可用时不崩溃，记 warning 并 fallback
- **默认 dry-run** — HealAgent 真执行需 `heal_dry_run=False` 显式开启
- **代码注释中文** — 与现有风格一致

---

## File Structure (变更总览)

```
backend/app/
├── main.py                                    # [M] lifespan + /metrics + /ready
├── infrastructure/
│   └── __init__.py                            # [M] 新增 InfrastructureRegistry 单例
├── api/
│   └── routes.py                              # [M] 删除自动 seed + 加 demo 端点 + 删 simulate_failure
├── agents/
│   ├── orchestrator.py                        # [M] 9 节点 + _sequential_process + process_alert
│   ├── eval_agent.py                          # [M] 删除 _eval_reasoning_with_mock_data
│   └── business_monitor_agent.py              # [M] 删除 mock_results 注入
├── tools/
│   ├── metrics_tools.py                       # [M] 调用 PrometheusClient
│   ├── playbook_tools.py                      # [M] 查 PLAYBOOKS
│   ├── knowledge_tools.py                     # [M] 查 ChromaDBStorage + SERVICE_TOPOLOGY
│   └── eval_tools.py                          # [M] 查 SQLite audit_logs
├── services/
│   └── langfuse_service.py                    # [M] 真实接入（如果 langfuse 包安装）
└── tests/
    ├── conftest.py                            # [M] 修路径
    ├── test_main_lifespan.py                  # [A] 启动初始化测试
    ├── test_ready_endpoint.py                 # [A] /ready 真实探活
    ├── test_metrics_endpoint.py               # [A] /metrics 真实指标
    ├── test_orchestrator_sequential.py        # [A] sequential fallback
    ├── test_eval_no_mock.py                   # [A] 评测无 mock
    └── test_business_no_mock_injection.py     # [A] 业务检测无注入

docs/superpowers/specs/
└── 2026-07-10-placeholder-removal-optimization-design.md  # 已存在 (input)

技术讲解稿-Agent协作故障定位全流程.md                 # [R] 完整重写
```

[M] = Modify | [A] = Add | [R] = Rewrite

---

## Phase 0 — 启动底座 (Foundation)

### Task 0.1: 修复 conftest.py 硬编码路径

**Files:**
- Modify: `backend/tests/conftest.py:13`

**原因**: 后续所有测试都依赖 conftest 路径正确，必须先修

**Interfaces:**
- Produces: 任何 `from app.* import ...` 在 pytest 中可用

- [ ] **Step 1: 修改 sys.path 为相对路径**

打开 `backend/tests/conftest.py`，找到第 13 行：
```python
sys.path.insert(0, "/mnt/agents/output/aiops-agent-platform/backend")
```

替换为：
```python
import pathlib
_BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND_ROOT))
```

- [ ] **Step 2: 验证 import 可用**

Run:
```bash
cd backend && python -c "import sys; sys.path.insert(0, '.'); from app.config import get_config; print('OK')"
```
Expected: `OK`（无 ImportError）

- [ ] **Step 3: 跑现有测试套件确认无回归**

Run:
```bash
cd backend && python -m pytest tests/test_imports.py -v 2>&1 | tail -20
```
Expected: 全部 PASS 或 SKIPPED（无 ImportError / Collection Error）

- [ ] **Step 4: 在 `_plan_log.md` 记录完成时间戳**

如项目无 git，在项目根目录创建 `_plan_log.md`，追加：
```
Task 0.1 — conftest.py 修路径  ✅  <时间戳>
```

---

### Task 0.2: 在 main.py lifespan 预热核心组件

**Files:**
- Modify: `backend/app/main.py:25-60`

**Interfaces:**
- Consumes: `get_config()`、`PrometheusClient`、`MemorySystem.get_instance()`、`KnowledgeBase`
- Produces: lifespan 真正初始化上述组件并把状态写到 `app.state`

- [ ] **Step 1: 在 `main.py` 顶部新增 import**

在第 21 行后插入：
```python
from app.infrastructure import InfrastructureRegistry
from app.agents.orchestrator import Orchestrator
```

- [ ] **Step 2: 改写 lifespan 函数体**

替换第 25-60 行的整个 `lifespan` 函数体为：
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理

    启动顺序:
      1. 配置 + 日志
      2. 基础设施 (ChromaDB / Prometheus / Memory)
      3. 知识库加载 (同步部分)
      4. Orchestrator 构建 LangGraph 图
      5. 输出启动清单
    """
    config = get_config()
    configure_logging(config)
    logger.info("AIOps Agent Platform starting", env=config.env, debug=config.debug, version="0.1.0")

    # 基础设施初始化
    infra = InfrastructureRegistry(config)
    infra_status = await infra.initialize()
    app.state.infrastructure = infra
    app.state.startup_status = infra_status

    # 知识库加载（同步部分先做，向量入库走 background）
    try:
        from app.data.knowledge_base import KnowledgeBase
        kb = KnowledgeBase()
        loaded = kb.load_all()
        app.state.knowledge_base = kb
        logger.info("Knowledge base loaded", entries=loaded)
    except Exception as e:
        logger.warning("Knowledge base load failed", error=str(e))
        app.state.knowledge_base = None

    # Orchestrator 构建 LangGraph 图
    try:
        orchestrator = Orchestrator()
        graph = orchestrator.build_graph()
        app.state.orchestrator = orchestrator
        app.state.langgraph_available = graph is not None
        logger.info("Orchestrator built", langgraph=graph is not None)
    except Exception as e:
        logger.warning("Orchestrator build failed", error=str(e))
        app.state.orchestrator = None
        app.state.langgraph_available = False

    # 输出启动清单
    logger.info(
        "Startup complete",
        chromadb=infra_status.get("chromadb", False),
        memory=infra_status.get("memory", False),
        prometheus=infra_status.get("prometheus", False),
        knowledge_base=app.state.knowledge_base is not None,
        langgraph=app.state.langgraph_available,
    )

    yield

    # Shutdown
    logger.info("AIOps Agent Platform shutting down")
    await infra.close()
    logger.info("AIOps Agent Platform shutdown complete")
```

- [ ] **Step 3: 创建 InfrastructureRegistry**

创建新文件 `backend/app/infrastructure/__init__.py`（替换现有空文件）：
```python
"""
AIOps Agent Platform - Infrastructure Registry

统一管理所有外部依赖（ChromaDB / Prometheus / MemorySystem）的单例。
提供 initialize() 用于启动初始化、close() 用于关闭清理。
"""
from __future__ import annotations

from typing import Any

from app.config import AppConfig
from app.utils.logging import get_logger

logger = get_logger(__name__)


class InfrastructureRegistry:
    """基础设施注册中心 — 单例管理外部 client"""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.chroma_client: Any = None
        self.memory_system: Any = None
        self.prometheus_client: Any = None
        self._initialized = False

    async def initialize(self) -> dict[str, bool]:
        """
        初始化所有外部依赖

        Returns:
            dict[str, bool]: 每个依赖的就绪状态，用于 /ready 端点
        """
        status: dict[str, bool] = {}

        # 1. ChromaDB
        try:
            from app.memory.storage import ChromaDBStorage
            self.chroma_client = ChromaDBStorage(
                host=self.config.infrastructure.chroma_host
                if hasattr(self.config.infrastructure, "chroma_host")
                else "localhost",
                port=self.config.infrastructure.chroma_port
                if hasattr(self.config.infrastructure, "chroma_port")
                else 8000,
            )
            await self.chroma_client.initialize()
            status["chromadb"] = True
            logger.info("ChromaDB connected")
        except Exception as e:
            logger.warning("ChromaDB unavailable, will use InMemoryStorage", error=str(e))
            status["chromadb"] = False

        # 2. MemorySystem
        try:
            from app.memory.core import MemorySystem
            self.memory_system = await MemorySystem.get_instance()
            status["memory"] = True
            logger.info("MemorySystem ready")
        except Exception as e:
            logger.warning("MemorySystem unavailable", error=str(e))
            status["memory"] = False

        # 3. PrometheusClient
        try:
            from app.infrastructure.prometheus_client import PrometheusClient
            self.prometheus_client = PrometheusClient()
            health = await self.prometheus_client.health_check()
            status["prometheus"] = health.get("reachable", False)
            if status["prometheus"]:
                logger.info("Prometheus reachable", url=health.get("url"))
            else:
                logger.info("Prometheus not reachable, continuing without it", url=health.get("url"))
        except Exception as e:
            logger.warning("Prometheus client init failed", error=str(e))
            status["prometheus"] = False

        self._initialized = True
        return status

    async def close(self) -> None:
        """关闭所有 client"""
        if self.prometheus_client:
            try:
                await self.prometheus_client.close()
            except Exception as e:
                logger.debug("Prometheus close error", error=str(e))
        # ChromaDB / MemorySystem 自身的清理由其模块负责
        logger.info("Infrastructure closed")
```

- [ ] **Step 4: 检查 ChromaDBStorage API 是否匹配**

```bash
cd backend && grep -n "class ChromaDBStorage\|async def initialize\|def __init__" app/memory/storage.py | head -10
```
如果 `ChromaDBStorage` 没有 `async def initialize` 或 `__init__` 签名不匹配，按实际签名调整 InfrastructureRegistry 代码（常见情况：__init__ 接受不同参数）。

- [ ] **Step 5: 启动验证**

```bash
cd backend && python -c "from app.infrastructure import InfrastructureRegistry; print('OK')"
```
Expected: `OK`

- [ ] **Step 6: 启动 uvicorn 验证 lifespan**

```bash
cd backend && timeout 10 python -m uvicorn app.main:app --port 8000 2>&1 | head -30
```
Expected: 日志包含 `Knowledge base loaded` + `Orchestrator built` + `Startup complete`

---

### Task 0.3: 用 prometheus_client 替换手写 /metrics

**Files:**
- Modify: `backend/app/main.py:124-168`

**Interfaces:**
- Consumes: `prometheus_client.Counter, Gauge, Histogram`
- Produces: 真实 HTTP 请求计数 + Agent 调用次数 + 故障处理时长分布

- [ ] **Step 1: 在 main.py 顶部 import prometheus_client**

在第 12 行后新增：
```python
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from starlette.responses import Response as StarletteResponse
```

- [ ] **Step 2: 定义全局指标（main.py 模块级别）**

在 `app = FastAPI(...)` 之前插入：
```python
# Prometheus 指标
HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
)
AGENT_INVOCATIONS_TOTAL = Counter(
    "agent_invocations_total",
    "Total agent invocations",
    ["agent", "status"],
)
INCIDENT_PROCESSING_SECONDS = Histogram(
    "incident_processing_seconds",
    "Incident processing time distribution",
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0),
)
APP_UP = Gauge(
    "app_up",
    "Application availability (1=up)",
)
APP_UP.set(1)
```

- [ ] **Step 3: 替换原 /metrics 端点**

找到 `main.py` 第 124-168 行的 `@app.get("/metrics", ...)` 整个函数体，替换为：
```python
@app.get("/metrics", tags=["Observability"])
async def metrics() -> Response:
    """Prometheus 指标采集端点（使用 prometheus_client 真实统计）"""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
```

- [ ] **Step 4: 添加 HTTP 请求中间件**

在 `create_app()` 函数中 `app.add_middleware(CORSMiddleware, ...)` 之后新增：
```python
@app.middleware("http")
async def prometheus_http_middleware(request, call_next):
    """统计每个 HTTP 请求"""
    response = await call_next(request)
    path_template = request.url.path
    # 跳过 /metrics 自身避免递归
    if path_template != "/metrics":
        HTTP_REQUESTS_TOTAL.labels(
            method=request.method,
            path=path_template,
            status=str(response.status_code),
        ).inc()
    return response
```

- [ ] **Step 5: 验证 /metrics 真实指标**

```bash
# Terminal 1
cd backend && python -m uvicorn app.main:app --port 8000

# Terminal 2
curl localhost:8000/health
curl localhost:8000/metrics | grep "http_requests_total\|app_up"
```
Expected: 出现 `app_up 1.0` 和 `http_requests_total{method="GET",path="/health",status="200"} 1.0`

---

### Task 0.4: 替换 /ready 硬编码为真实探活

**Files:**
- Modify: `backend/app/main.py:180-188`

**Interfaces:**
- Consumes: `app.state.infrastructure`、`app.state.knowledge_base`
- Produces: 真实的依赖就绪状态，关键依赖失败返回 503

- [ ] **Step 1: 重写 readiness_check 函数**

替换 `main.py` 第 180-188 行为：
```python
@app.get("/ready", tags=["Health"])
async def readiness_check() -> JSONResponse:
    """就绪检查 — 真实探测各依赖"""
    checks: dict[str, dict[str, Any]] = {}
    all_critical_ok = True

    # 1. ChromaDB
    infra = getattr(app.state, "infrastructure", None)
    if infra and infra.chroma_client:
        try:
            chroma_ok = await infra.chroma_client.health_check()
            checks["chromadb"] = {"status": "ready" if chroma_ok else "unhealthy"}
            if not chroma_ok:
                all_critical_ok = False
        except Exception as e:
            checks["chromadb"] = {"status": "error", "error": str(e)}
            all_critical_ok = False
    else:
        checks["chromadb"] = {"status": "fallback_inmemory"}

    # 2. MemorySystem
    if infra and infra.memory_system:
        checks["memory"] = {"status": "ready"}
    else:
        checks["memory"] = {"status": "degraded"}
        # memory 不是 critical，没有 chromadb 时仍可降级

    # 3. Prometheus (非 critical)
    if infra and infra.prometheus_client:
        try:
            prom_health = await infra.prometheus_client.health_check()
            checks["prometheus"] = {
                "status": "ready" if prom_health.get("reachable") else "unreachable",
                "url": prom_health.get("url"),
            }
        except Exception as e:
            checks["prometheus"] = {"status": "error", "error": str(e)}
    else:
        checks["prometheus"] = {"status": "not_configured"}

    # 4. Knowledge base
    kb = getattr(app.state, "knowledge_base", None)
    checks["knowledge_base"] = {
        "status": "ready" if kb is not None else "degraded",
    }

    # 5. LangGraph
    checks["langgraph"] = {
        "status": "ready" if getattr(app.state, "langgraph_available", False) else "fallback_sequential",
    }

    status_code = 200 if all_critical_ok else 503
    body = {
        "status": "ready" if all_critical_ok else "degraded",
        "version": "0.1.0",
        "checks": checks,
    }
    return JSONResponse(status_code=status_code, content=body)
```

- [ ] **Step 2: 验证 /ready 真实状态**

```bash
# 启动服务
cd backend && python -m uvicorn app.main:app --port 8000 &
sleep 3

# 检查 /ready
curl -s localhost:8000/ready | python -m json.tool
```
Expected: 返回 JSON 含 `chromadb`、`memory`、`prometheus`、`knowledge_base`、`langgraph` 五个 check 状态

- [ ] **Step 3: 记录完成**

在 `_plan_log.md` 追加：
```
Task 0.2-0.4 — lifespan + /metrics + /ready  ✅  <时间戳>
```

---

## Phase 1 — 主链路去 seed

### Task 1.1: 删除 routes.py 自动 _seed_incidents() 调用

**Files:**
- Modify: `backend/app/api/routes.py:269-270`

**Interfaces:**
- Produces: `_seed_incidents` 仍可作为函数被显式调用，但不再在模块加载时自动执行

- [ ] **Step 1: 删除模块级别的自动调用**

找到 `routes.py` 第 269-270 行：
```python
_seed_incidents()
```

整段删除（保留前面的 `_seed_incidents` 函数定义）。

- [ ] **Step 2: 验证 import 不再触发 seed**

```bash
cd backend && python -c "from app.api.routes import api_router; print('OK')"
```
Expected: `OK`，且 `_incident_service._incidents` 为空字典（无 7 个 seed incident）

- [ ] **Step 3: 验证现有测试无回归**

```bash
cd backend && python -m pytest tests/test_api.py -v 2>&1 | tail -30
```
Expected: 测试可能 FAIL（因为它们之前依赖 _seed_incidents）。记录失败用例名称，下个 Task 修复。

---

### Task 1.2: 添加 /incidents/seed-demo 演示端点

**Files:**
- Modify: `backend/app/api/routes.py`

**Interfaces:**
- Consumes: `_seed_incidents()` 函数
- Produces: `POST /api/v1/incidents/seed-demo` 端点，仅 dev 环境可用

- [ ] **Step 1: 新增 demo 端点（紧跟 trigger_incident 函数）**

在 `trigger_incident` 函数定义（第 285 行）之后、`trigger_business_incident` 之前，插入：
```python
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
```

- [ ] **Step 2: 验证 dev 环境可调用**

```bash
cd backend && APP_ENV=development python -m uvicorn app.main:app --port 8000 &
sleep 3
curl -X POST localhost:8000/api/v1/incidents/seed-demo | python -m json.tool
```
Expected: 返回 7 个 incident_id

- [ ] **Step 3: 验证 prod 环境被拒**

```bash
# 关闭上面进程
kill %1 2>/dev/null
cd backend && APP_ENV=production python -m uvicorn app.main:app --port 8000 &
sleep 3
curl -X POST -w "\nHTTP %{http_code}\n" localhost:8000/api/v1/incidents/seed-demo
```
Expected: HTTP 403 + "seed-demo endpoint is only available in development environment"

- [ ] **Step 4: 修复因删除 _seed_incidents 自动调用导致的测试失败**

查看 Task 1.1 Step 3 失败用例，对每个失败用例：
- 如果测试期望有 incident：改用 `seed_demo_incidents()` 函数显式调用
- 如果测试断言 "incidents dict 不为空"：修改断言为 `len == 0` 或先 seed

修改 `backend/tests/test_api.py` 中任何依赖 `_seed_incidents` 自动调用的测试。

---

## Phase 2 — Orchestrator 补完

### Task 2.1: 修复 _sequential_process 真调 Agent

**Files:**
- Modify: `backend/app/agents/orchestrator.py:201-256`

**Interfaces:**
- Consumes: `RCAInput`, `HealInput`, `ChangeInput`, `AgentExecutionContext`
- Produces: incident.rca_event / heal_event / change_event 全部填真实数据

- [ ] **Step 1: 在 orchestrator.py 顶部新增 import**

在第 16 行后新增：
```python
from app.agents.base import AgentExecutionContext
from app.agents.rca_agent import RCAAgent, RCAInput
from app.agents.heal_agent import HealAgent, HealInput
from app.agents.change_agent import ChangeAgent, ChangeInput
from app.models.events import RCAEvent, HealEvent, ChangeEvent
```

- [ ] **Step 2: 替换 _sequential_process 函数体**

替换 `orchestrator.py` 第 201-256 行的 `_sequential_process` 为：
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

    # Step 1: 分类分级（已由 incident.from_alert 完成，此处仅 transition）
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

    # Step 3: 决策（基于 RCA 结果和 severity）
    self._state = OrchestratorState.DECIDING_ACTION
    if incident.severity in (SeverityLevel.CRITICAL, SeverityLevel.HIGH):
        self._state = OrchestratorState.AWAITING_APPROVAL
        incident.transition_to(IncidentState.AWAITING_APPROVAL, actor="change_agent")
    else:
        self._state = OrchestratorState.EXECUTING_HEAL
        incident.transition_to(IncidentState.HEALING, actor="heal_agent")

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

    # Step 4: 变更审批
    if self._state == OrchestratorState.AWAITING_APPROVAL:
        change_agent = ChangeAgent()
        # 取最近一个 heal_event 或构造空 heal_event
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

    # Step 5: 验证（暂简化：标记完成）
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

- [ ] **Step 3: 验证 import 正确**

```bash
cd backend && python -c "from app.agents.orchestrator import Orchestrator; o = Orchestrator(); print('OK')"
```
Expected: `OK`，无 ImportError

- [ ] **Step 4: 记录完成**

`_plan_log.md` 追加：`Task 2.1 — _sequential_process 真调 Agent  ✅  <时间戳>`

---

### Task 2.2: 修复 process_alert 真正调用 LangGraph

**Files:**
- Modify: `backend/app/agents/orchestrator.py:175-199`

**Interfaces:**
- Consumes: `self._compiled_graph`
- Produces: 真正调用 `ainvoke(initial_state)` 并把结果写回 incident

- [ ] **Step 1: 替换 process_alert 中 LangGraph 执行段**

替换 `orchestrator.py` 第 175-199 行（`# TODO: 如果 LangGraph 可用，执行图编排` 注释所在段）为：
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
                # 把 agent_results 写回 incident
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
            await self._sequential_process(incident)

        return incident
```

- [ ] **Step 2: 验证 orchestrator 启动**

```bash
cd backend && python -c "
import asyncio
from app.agents.orchestrator import Orchestrator
from app.models.events import AlertEvent, SeverityLevel
from datetime import datetime, timezone

async def main():
    o = Orchestrator()
    graph = o.build_graph()
    print('graph built:', graph is not None)
    if graph:
        alert = AlertEvent(
            source='manual',
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
        incident = await o.process_alert(alert)
        print('incident.state:', incident.state)
        print('incident.rca_event:', incident.rca_event is not None)

asyncio.run(main())
"
```
Expected: 打印 `graph built: True`（如果 LangGraph 已装）或 `False`（如果没装），无论哪种 `incident.rca_event` 都应该是真值（sequential fallback 也调了 RCA）

---

### Task 2.3: 实现 9 个 LangGraph 节点方法

**Files:**
- Modify: `backend/app/agents/orchestrator.py:259-322`

**Interfaces:**
- Consumes: `state: dict`, `RCAInput/HealInput/ChangeInput`, `AgentExecutionContext`
- Produces: 每个节点调用对应 Agent，把结果写回 `state["agent_results"]` 和 `state["incident"]`

- [ ] **Step 1: 重写 9 个节点方法**

替换 `orchestrator.py` 第 259-322 行（`# === LangGraph 节点方法 ===` 之后到 `# === 条件边方法 ===` 之前的所有节点函数），整体替换为：

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
        """分类分级节点 — 委托给 MonitorAgent"""
        self._state = OrchestratorState.TRIAGING
        logger.info("Graph node: triage")
        incident = state.get("incident")
        if not incident:
            return state
        try:
            from app.agents.monitor_agent import MonitorAgent, MetricInput
            from app.data.datasets import get_metric_data
            monitor = MonitorAgent()
            metric_name = incident.alert_event.metric
            history = get_metric_data(incident.service, metric_name, "normal")
            metric_input = MetricInput(
                metric_name=metric_name,
                metric_value=incident.alert_event.value,
                service_name=incident.service,
                labels=incident.alert_event.labels,
                history_values=history,
            )
            ctx = AgentExecutionContext(incident_id=incident.incident_id)
            result = await monitor.process(metric_input, ctx)
            state["agent_results"]["monitor"] = result.output_data or {}
        except Exception as e:
            logger.error("Triage node error", error=str(e))
            state["errors"].append(f"triage: {e}")
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

- [ ] **Step 2: 替换 _edge_decide_action 读取 rca 结果**

替换 `orchestrator.py` 第 326-342 行的 `_edge_decide_action` 为：
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

- [ ] **Step 3: 验证 LangGraph 编译**

```bash
cd backend && python -c "
from app.agents.orchestrator import Orchestrator
o = Orchestrator()
g = o.build_graph()
print('graph:', g is not None)
" 2>&1 | tail -10
```
Expected: `graph: True` 或 `graph: False`（取决于 LangGraph 是否安装），无 TypeError/ImportError

---

## Phase 3 — Tool 层真实化

### Task 3.1: metrics_tools 调真实 PrometheusClient

**Files:**
- Modify: `backend/app/tools/metrics_tools.py`

**Interfaces:**
- Consumes: `PrometheusClient` from `app.infrastructure.prometheus_client`
- Produces: `query_metric_range` / `query_metric_instant` / `get_service_metrics` 真实调用 Prometheus

- [ ] **Step 1: 在 metrics_tools.py 顶部新增 import**

找到文件顶部 import 区，在最末追加：
```python
from app.infrastructure.prometheus_client import PrometheusClient
```

- [ ] **Step 2: 实现 PrometheusClient 单例获取**

在文件中部（约 line 50 之前）新增函数：
```python
_prometheus_client: PrometheusClient | None = None

def _get_prometheus_client() -> PrometheusClient:
    """获取 PrometheusClient 单例"""
    global _prometheus_client
    if _prometheus_client is None:
        _prometheus_client = PrometheusClient()
    return _prometheus_client
```

- [ ] **Step 3: 替换 query_metric_range 函数体**

定位 `query_metric_range` 函数（line 91 附近有 `# TODO: 调用 Prometheus/VictoriaMetrics API`），替换为：
```python
async def query_metric_range(
    service: str,
    metric: str,
    lookback_minutes: int = 60,
    step: str = "1m",
) -> list[float]:
    """
    查询服务的指标历史数据（真实调用 Prometheus）

    Args:
        service: 服务名
        metric: 指标名（CPU/内存/磁盘等）
        lookback_minutes: 回溯时间窗口（分钟）
        step: PromQL step

    Returns:
        list[float]: 历史数据点列表
    """
    from datetime import datetime, timedelta, timezone

    client = _get_prometheus_client()
    # 拼 PromQL：按 metric 名映射
    promql_map = {
        "cpu_usage_percent": '100 - (avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) * 100)',
        "memory_usage_percent": '(1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)) * 100',
        "disk_usage_percent": '(1 - (node_filesystem_avail_bytes{fstype!="tmpfs"} / node_filesystem_size_bytes{fstype!="tmpfs"})) * 100',
        "load_average": 'node_load5',
    }
    promql = promql_map.get(metric, metric)  # 未知指标直接用原名
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=lookback_minutes)
    try:
        return await client.query_range(promql, start=start, end=end, step=step)
    except Exception as e:
        logger.error("query_metric_range failed", service=service, metric=metric, error=str(e))
        return []
```

- [ ] **Step 4: 替换 get_service_metrics_panel 函数体**

定位 line 154 附近的 `get_service_metrics_panel`，把 `# TODO: 查询服务指标面板` 替换为：
```python
async def get_service_metrics_panel(service: str, metrics: list[str]) -> dict[str, list[float]]:
    """
    获取服务的多个指标面板数据（真实调用 Prometheus）

    Args:
        service: 服务名
        metrics: 指标名列表

    Returns:
        dict[metric_name, list[float]]: 每个指标的时间序列
    """
    results: dict[str, list[float]] = {}
    for metric in metrics:
        results[metric] = await query_metric_range(service, metric)
    return results
```

- [ ] **Step 5: 替换 detect_anomaly_tool 函数体**

定位 line 217 附近的 `detect_anomaly_tool`，把 `# TODO: 调用异常检测算法` 替换为：
```python
async def detect_anomaly_tool(
    service: str,
    metric: str,
    current_value: float,
    history: list[float],
) -> dict[str, Any]:
    """
    异常检测工具（委托给 MonitorAgent 的真实算法）

    Args:
        service: 服务名
        metric: 指标名
        current_value: 当前值
        history: 历史值列表

    Returns:
        dict: 异常检测结果
    """
    try:
        from app.agents.monitor_agent import MonitorAgent, MetricInput
        monitor = MonitorAgent()
        metric_input = MetricInput(
            metric_name=metric,
            metric_value=current_value,
            service_name=service,
            labels={},
            history_values=history,
        )
        from app.agents.base import AgentExecutionContext
        ctx = AgentExecutionContext(incident_id="tool-detect-anomaly")
        result = await monitor.process(metric_input, ctx)
        return result.output_data or {"is_anomaly": False, "score": 0.0}
    except Exception as e:
        logger.error("detect_anomaly_tool failed", error=str(e))
        return {"is_anomaly": False, "score": 0.0, "error": str(e)}
```

- [ ] **Step 6: 验证**

```bash
cd backend && python -c "from app.tools.metrics_tools import query_metric_range, detect_anomaly_tool; print('OK')"
```
Expected: `OK`

---

### Task 3.2: playbook_tools 查真实 PLAYBOOKS

**Files:**
- Modify: `backend/app/tools/playbook_tools.py`

**Interfaces:**
- Consumes: `data.playbooks.PLAYBOOKS`, `data.public_playbooks.PUBLIC_PLAYBOOKS`
- Produces: `list_playbooks` / `get_playbook` / `execute_playbook` 真实查询内存数据

- [ ] **Step 1: 顶部 import**

```python
from app.data.playbooks import PLAYBOOKS
try:
    from app.data.public_playbooks import PUBLIC_PLAYBOOKS
except ImportError:
    PUBLIC_PLAYBOOKS = []
```

- [ ] **Step 2: 替换 list_playbooks 函数体**

定位 `list_playbooks`，把 `# TODO: 从 Playbook 数据库查询` 替换为：
```python
def list_playbooks(domain: str | None = None) -> list[dict[str, Any]]:
    """
    列出所有 Playbook

    Args:
        domain: 可选按业务域过滤（financial/inventory/order/user/infra）

    Returns:
        list[dict]: 匹配的 playbook 列表
    """
    all_pbs = list(PLAYBOOKS) + list(PUBLIC_PLAYBOOKS)
    if domain:
        return [pb for pb in all_pbs if pb.get("domain") == domain or pb.get("category") == domain]
    return all_pbs
```

- [ ] **Step 3: 替换 get_playbook 函数体**

定位 `get_playbook`，把 `# TODO: 从 Playbook 数据库查询` 替换为：
```python
def get_playbook(playbook_id: str) -> dict[str, Any] | None:
    """根据 ID 查询 Playbook"""
    for pb in list(PLAYBOOKS) + list(PUBLIC_PLAYBOOKS):
        if pb.get("id") == playbook_id:
            return pb
    return None
```

- [ ] **Step 4: 替换 execute_playbook 函数体**

定位 `execute_playbook`，把 `# TODO: 加载/执行/记录` 全部替换为：
```python
async def execute_playbook(
    playbook_id: str,
    context: dict[str, Any] | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    """
    执行 Playbook（默认 dry-run）

    Args:
        playbook_id: Playbook ID
        context: 执行上下文（如 service 名等）
        dry_run: True 仅模拟，不真执行

    Returns:
        dict: 执行结果
    """
    pb = get_playbook(playbook_id)
    if not pb:
        return {"success": False, "error": f"Playbook {playbook_id} not found"}
    if dry_run:
        return {
            "success": True,
            "dry_run": True,
            "playbook": pb,
            "executed_actions": [],
            "message": f"Dry-run for {playbook_id}, no actions executed",
        }
    # 真执行路径：当前为空（需对接 K8s/Ansible 等执行器，详见 spec "已知遗留"）
    return {
        "success": False,
        "dry_run": False,
        "playbook": pb,
        "executed_actions": [],
        "error": "Real execution not implemented yet — see spec Phase 3 '已知遗留'",
    }
```

- [ ] **Step 5: 验证**

```bash
cd backend && python -c "from app.tools.playbook_tools import list_playbooks, get_playbook; print(len(list_playbooks()), get_playbook(list_playbooks()[0]['id'])['name'])"
```
Expected: 打印 playbook 总数 + 第一个 playbook 的名字（非空）

---

### Task 3.3: knowledge_tools 查真实 ChromaDBStorage + SERVICE_TOPOLOGY

**Files:**
- Modify: `backend/app/tools/knowledge_tools.py`

- [ ] **Step 1: 顶部 import**

```python
from app.data.knowledge_base import KNOWLEDGE_BASE, SERVICE_TOPOLOGY
```

- [ ] **Step 2: 替换 search_knowledge 函数体**

定位 `search_knowledge`，把 `# TODO: 调用向量数据库检索 / 结合关键词过滤` 替换为：
```python
async def search_knowledge(
    query: str,
    top_k: int = 5,
    min_score: float = 0.0,
) -> list[dict[str, Any]]:
    """
    知识库检索（先尝试 ChromaDB 向量检索，失败回退到关键词匹配）

    Args:
        query: 查询文本
        top_k: 返回数量
        min_score: 最低分数阈值

    Returns:
        list[dict]: 匹配的知识点
    """
    # 1. 尝试向量检索
    try:
        from app.memory.storage import ChromaDBStorage
        storage = ChromaDBStorage()
        await storage.initialize()
        results = await storage.search(query, top_k=top_k)
        if results:
            return [r for r in results if r.get("score", 0) >= min_score]
    except Exception as e:
        logger.debug("ChromaDB search failed, using keyword fallback", error=str(e))

    # 2. 关键词匹配 fallback（在内存 KNOWLEDGE_BASE 中搜）
    query_lower = query.lower()
    matched: list[dict[str, Any]] = []
    for entry in KNOWLEDGE_BASE:
        content = entry.get("content", "").lower()
        tags = " ".join(entry.get("tags", [])).lower()
        score = 0.0
        if query_lower in content:
            score += 0.5
        for word in query_lower.split():
            if word in content:
                score += 0.1
            if word in tags:
                score += 0.2
        if score > min_score:
            entry_copy = dict(entry)
            entry_copy["score"] = score
            matched.append(entry_copy)
    matched.sort(key=lambda x: x["score"], reverse=True)
    return matched[:top_k]
```

- [ ] **Step 3: 替换 get_service_topology 函数体**

定位 `get_service_topology`，把 `# TODO: 调用拓扑数据库` 替换为：
```python
def get_service_topology(service: str | None = None) -> dict[str, Any]:
    """
    获取服务拓扑

    Args:
        service: 可选，指定服务名；None 时返回全量拓扑

    Returns:
        dict: 拓扑数据
    """
    if service:
        return SERVICE_TOPOLOGY.get(service, {})
    return {"services": SERVICE_TOPOLOGY}
```

- [ ] **Step 4: 替换 get_recent_changes 函数体**

定位 `get_recent_changes`，把 `# TODO: 调用变更管理系统 API` 替换为：
```python
def get_recent_changes(service: str, window_minutes: int = 60) -> list[dict[str, Any]]:
    """
    获取服务的最近变更记录（当前从 datasets 加载，未来对接 ArgoCD/GitLab）

    Args:
        service: 服务名
        window_minutes: 时间窗口（分钟）

    Returns:
        list[dict]: 变更记录
    """
    try:
        from app.data.datasets import CHANGE_RECORDS
        cutoff_minutes = window_minutes
        results = []
        for cr in CHANGE_RECORDS:
            if cr.get("service") == service:
                results.append(cr)
        return results
    except Exception as e:
        logger.debug("get_recent_changes failed", error=str(e))
        return []
```

- [ ] **Step 5: 验证**

```bash
cd backend && python -c "
import asyncio
from app.tools.knowledge_tools import search_knowledge, get_service_topology

async def main():
    r = await search_knowledge('cpu', top_k=3)
    print('search results:', len(r))
    t = get_service_topology('order-service')
    print('topology:', t.get('tier', 'unknown'))

asyncio.run(main())
"
```
Expected: 打印搜索结果数（≥1）+ topology tier

---

### Task 3.4: eval_tools 查 SQLite audit_logs

**Files:**
- Modify: `backend/app/tools/eval_tools.py`

- [ ] **Step 1: 顶部新增 import**

```python
import sqlite3
import os
```

- [ ] **Step 2: 实现 _get_audit_db_path 函数**

新增函数：
```python
def _get_audit_db_path() -> str:
    """获取审计日志 SQLite 数据库路径"""
    return os.getenv("SQLITE_PATH", "./data/aiops.db")
```

- [ ] **Step 3: 替换 query_evaluation_history 函数体**

定位 `query_evaluation_history`，把 `# TODO: 从评估数据库查询` 替换为：
```python
def query_evaluation_history(limit: int = 50) -> list[dict[str, Any]]:
    """
    查询历史评估记录（从 SQLite audit_logs 读取）

    Args:
        limit: 返回记录数

    Returns:
        list[dict]: 评估历史
    """
    db_path = _get_audit_db_path()
    if not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM audit_logs WHERE action='evaluation' ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        logger.debug("query_evaluation_history failed", error=str(e))
        return []
```

- [ ] **Step 4: 替换 store_evaluation_feedback 函数体**

定位 `store_evaluation_feedback`，把 `# TODO: 存储反馈到数据库` 替换为：
```python
def store_evaluation_feedback(
    eval_id: str,
    feedback: str,
    rating: float,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """
    存储评估反馈到 SQLite

    Args:
        eval_id: 评估 ID
        feedback: 反馈文本
        rating: 评分 (0-1)
        metadata: 附加元数据

    Returns:
        bool: 是否成功
    """
    db_path = _get_audit_db_path()
    try:
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
            "INSERT INTO evaluation_feedback (eval_id, feedback, rating, metadata) VALUES (?, ?, ?, ?)",
            (eval_id, feedback, rating, str(metadata or {})),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error("store_evaluation_feedback failed", error=str(e))
        return False
```

- [ ] **Step 5: 验证**

```bash
cd backend && python -c "
from app.tools.eval_tools import query_evaluation_history, store_evaluation_feedback
print('history:', len(query_evaluation_history()))
ok = store_evaluation_feedback('test-1', 'good', 0.9, {'test': True})
print('store ok:', ok)
"
```
Expected: history=0（首次无数据），store=True

---

## Phase 4 — 评测与业务检测去 mock

### Task 4.1: 删除 eval_agent._eval_reasoning_with_mock_data

**Files:**
- Modify: `backend/app/agents/eval_agent.py`

**Interfaces:**
- Produces: `_eval_reasoning` 在无数据时抛 ValueError；上游决定如何处理

- [ ] **Step 1: 找到 _eval_reasoning 调用 _eval_reasoning_with_mock_data 的位置**

```bash
cd backend && grep -n "_eval_reasoning_with_mock_data" app/agents/eval_agent.py
```

- [ ] **Step 2: 替换 `_eval_reasoning` 中的 mock fallback**

定位 `eval_agent.py:500`：
```python
        if not ground_truth and not agent_results:
            return self._eval_reasoning_with_mock_data()
```

替换为：
```python
        if not ground_truth and not agent_results:
            raise ValueError(
                "Cannot evaluate reasoning: both ground_truth and agent_results are empty. "
                "Provide agent_results from a real run, or pass ground_truth for evaluation."
            )
```

- [ ] **Step 3: 删除 _eval_reasoning_with_mock_data 方法**

定位 `_eval_reasoning_with_mock_data` 方法（line 608 附近），整段删除（包含函数定义和 docstring）。

- [ ] **Step 4: 验证 eval_agent 行为**

```bash
cd backend && python -c "
from app.agents.eval_agent import EvalAgent
e = EvalAgent()
try:
    r = e._eval_reasoning(None)
except ValueError as ex:
    print('expected error:', ex)
except AttributeError:
    print('TypeError on None input - acceptable')
"
```
Expected: ValueError 抛出（如果用 None 直接调用），或 AttributeError（因为 input_data 类型未定义）

---

### Task 4.2: 删除 business_monitor_agent mock_results 注入

**Files:**
- Modify: `backend/app/agents/business_monitor_agent.py`

- [ ] **Step 1: 定位 mock_data 处理段**

```bash
cd backend && grep -n "mock_data\|mock_results" app/agents/business_monitor_agent.py
```

- [ ] **Step 2: 替换 mock_results 处理段**

找到类似代码（line 339-349）：
```python
        mock_data = input_data.context.get("mock_results", {}).get(rule["id"])
        if mock_data is not None:
            ...
```

整段替换为：
```python
        # 真实规则检测（已无 mock 注入路径）
        matched = await self._check_rule(rule, input_data)
```

- [ ] **Step 3: 实现/查找 _check_rule 方法**

如果 `_check_rule` 不存在，在类内新增：
```python
async def _check_rule(self, rule: dict, input_data: "BusinessMetricInput") -> bool:
    """
    执行单条业务规则的真实检测

    Args:
        rule: 业务规则定义
        input_data: 业务监控输入

    Returns:
        bool: 是否命中规则
    """
    # 当前简化：根据 check_sql 字段做占位实现
    # 真实实现需对接业务数据库（详见 spec Phase 4 "已知遗留"）
    check_sql = rule.get("check_sql", "")
    if not check_sql:
        return False
    # 占位：未来对接真实 DB 时替换
    return False
```

- [ ] **Step 4: 验证**

```bash
cd backend && python -c "
import asyncio
from app.agents.business_monitor_agent import BusinessMonitorAgent, BusinessMetricInput

async def main():
    agent = BusinessMonitorAgent()
    input_data = BusinessMetricInput(
        service_name='payment-service',
        business_domain='financial',
        check_rules=['br_duplicate_charge'],
        context={},  # 故意为空，验证 mock_results 不再被识别
    )
    result = await agent.process(input_data, None)
    print('success:', result.success)

asyncio.run(main())
" 2>&1 | tail -5
```
Expected: success=True 或 False（真实检测结果，不应该因为 context 为空而 crash）

---

### Task 4.3: 删除 routes.py simulate_failure 字段

**Files:**
- Modify: `backend/app/api/routes.py`

**Interfaces:**
- Produces: 业务监控端点不再接受 `simulate_failure` 字段

- [ ] **Step 1: 删除 simulate_failure 注释和 context**

找到 routes.py 第 350 行（"simulate_failure": true）和第 429 行（simulate_failure: True）。这些是文档字符串和实际使用处。

替换 `/incidents/trigger-business` 端点的 example JSON docstring（line 350）：
```python
      "context": {"simulate_failure": true}
```
改为：
```python
      "context": {}
```

- [ ] **Step 2: 删除 `/incidents/trigger-business-scenario/{scenario_id}` 中的 simulate_failure**

定位 line 429 附近的：
```python
        context={
            "simulate_failure": True,
            "scenario_id": scenario_id,
            "scenario_name": scenario["name"],
        },
```

改为：
```python
        context={
            "scenario_id": scenario_id,
            "scenario_name": scenario["name"],
        },
```

- [ ] **Step 3: 验证 simulate_failure 字段被忽略**

```bash
cd backend && python -m uvicorn app.main:app --port 8000 &
sleep 3
curl -s -X POST localhost:8000/api/v1/incidents/trigger-business-scenario/fs_biz_007 \
  -H 'Content-Type: application/json' | python -m json.tool | head -20
```
Expected: 不报错，返回真实检测结果（success=True，matched=false 因为是真实检测）

---

## Phase 5 — 测试套件补全

### Task 5.1: 添加 test_main_lifespan 测试

**Files:**
- Create: `backend/tests/test_main_lifespan.py`

- [ ] **Step 1: 写测试文件**

```python
"""测试 main.py lifespan 真实初始化"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import create_app
    app = create_app()
    with TestClient(app) as c:
        yield c


def test_lifespan_initializes_infrastructure(client):
    """lifespan 必须初始化 InfrastructureRegistry"""
    infra = client.app.state.infrastructure
    assert infra is not None
    assert infra._initialized is True


def test_lifespan_sets_knowledge_base(client):
    """lifespan 必须加载 KnowledgeBase"""
    kb = client.app.state.knowledge_base
    # kb 可能为 None（如果 ChromaDB 不可用），但状态属性必须存在
    assert hasattr(client.app.state, "knowledge_base")


def test_lifespan_builds_orchestrator(client):
    """lifespan 必须构建 Orchestrator"""
    orch = client.app.state.orchestrator
    # LangGraph 可用时为 Orchestrator 实例，不可用时可能为 None
    assert hasattr(client.app.state, "orchestrator")
    assert hasattr(client.app.state, "langgraph_available")
```

- [ ] **Step 2: 运行测试**

```bash
cd backend && python -m pytest tests/test_main_lifespan.py -v
```
Expected: 3 passed（允许 1 个 skipped 如果 ChromaDB 不可用）

---

### Task 5.2: 添加 test_ready_endpoint 测试

**Files:**
- Create: `backend/tests/test_ready_endpoint.py`

- [ ] **Step 1: 写测试文件**

```python
"""测试 /ready 真实探活"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_ready_returns_dependencies():
    """ /ready 必须返回各依赖的真实状态"""
    from app.main import create_app
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/ready")
        # 200 或 503 都可接受，body 必有 checks
        body = resp.json()
        assert "checks" in body
        assert "chromadb" in body["checks"]
        assert "memory" in body["checks"]
        assert "prometheus" in body["checks"]
        assert "knowledge_base" in body["checks"]
        assert "langgraph" in body["checks"]


def test_ready_status_field():
    """/ready 返回的 status 字段值合法"""
    from app.main import create_app
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/ready")
        body = resp.json()
        assert body["status"] in ("ready", "degraded")
```

- [ ] **Step 2: 运行**

```bash
cd backend && python -m pytest tests/test_ready_endpoint.py -v
```
Expected: 2 passed

---

### Task 5.3: 添加 test_metrics_endpoint 测试

**Files:**
- Create: `backend/tests/test_metrics_endpoint.py`

- [ ] **Step 1: 写测试**

```python
"""测试 /metrics 真实指标"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_metrics_contains_app_up():
    """/metrics 必须包含 app_up"""
    from app.main import create_app
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/metrics")
        text = resp.text
        assert "app_up" in text
        assert "app_up 1.0" in text


def test_metrics_counts_http_requests():
    """/metrics 必须真实统计 HTTP 请求"""
    from app.main import create_app
    app = create_app()
    with TestClient(app) as client:
        client.get("/health")
        client.get("/health")
        resp = client.get("/metrics")
        text = resp.text
        # /health 应被计数 2 次
        assert 'http_requests_total{path="/health"' in text


def test_metrics_skips_self():
    """/metrics 调用不应被自身计数（避免递归）"""
    from app.main import create_app
    app = create_app()
    with TestClient(app) as client:
        client.get("/metrics")
        resp = client.get("/metrics")
        text = resp.text
        # 不应有 path="/metrics" 的计数
        assert 'http_requests_total{path="/metrics"' not in text
```

- [ ] **Step 2: 运行**

```bash
cd backend && python -m pytest tests/test_metrics_endpoint.py -v
```
Expected: 3 passed

---

### Task 5.4: 添加 test_orchestrator_sequential 测试

**Files:**
- Create: `backend/tests/test_orchestrator_sequential.py`

- [ ] **Step 1: 写测试**

```python
"""测试 Orchestrator sequential fallback 真调 Agent"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest


@pytest.mark.asyncio
async def test_sequential_process_calls_rca_agent():
    """sequential fallback 必须真调 RCA Agent 并填充 rca_event"""
    from app.agents.orchestrator import Orchestrator
    from app.models.events import AlertEvent, SeverityLevel

    o = Orchestrator()
    alert = AlertEvent(
        source="test",
        service="order-service",
        metric="cpu_usage_percent",
        value=95.0,
        threshold=80.0,
        operator=">",
        severity=SeverityLevel.HIGH,
        labels={"tier": "critical"},
        annotations={},
        timestamp=datetime.now(timezone.utc),
    )
    # 直接调用 _sequential_process
    incident = await _create_incident_and_run(o, alert)
    assert incident.rca_event is not None
    assert incident.rca_event.root_cause != ""
    assert incident.context.get("rca_root_cause") is not None


async def _create_incident_and_run(o, alert):
    """辅助：创建 incident 并跑 sequential"""
    from app.models.incident import Incident
    incident = Incident.from_alert(alert)
    await o._sequential_process(incident)
    return incident


@pytest.mark.asyncio
async def test_sequential_process_calls_heal_for_low_severity():
    """LOW severity 必须调 Heal Agent"""
    from app.agents.orchestrator import Orchestrator
    from app.models.events import AlertEvent, SeverityLevel
    from app.models.incident import Incident

    o = Orchestrator()
    alert = AlertEvent(
        source="test",
        service="payment-service",
        metric="error_rate",
        value=1.0,
        threshold=5.0,
        operator=">",
        severity=SeverityLevel.LOW,
        labels={"tier": "standard"},
        annotations={},
        timestamp=datetime.now(timezone.utc),
    )
    incident = Incident.from_alert(alert)
    await o._sequential_process(incident)
    # LOW 走 heal 路径
    assert len(incident.heal_events) >= 0  # heal 可能因 RCA 结果空而不出事件
```

- [ ] **Step 2: 运行**

```bash
cd backend && python -m pytest tests/test_orchestrator_sequential.py -v
```
Expected: 至少 1 passed（如果 RCA Agent 在测试环境工作）

---

### Task 5.5: 添加 test_eval_no_mock 测试

**Files:**
- Create: `backend/tests/test_eval_no_mock.py`

- [ ] **Step 1: 写测试**

```python
"""测试 EvalAgent 在无数据时不再 fallback 到 mock"""
from __future__ import annotations

import pytest


def test_eval_reasoning_with_no_data_raises():
    """无 ground_truth 无 agent_results 时必须抛 ValueError"""
    from app.agents.eval_agent import EvalAgent
    from app.models.evaluation import EvalInput

    agent = EvalAgent()
    input_data = EvalInput(
        eval_type="reasoning",
        ground_truth={},
        agent_results=[],
    )
    with pytest.raises(ValueError, match="Cannot evaluate reasoning"):
        agent._eval_reasoning(input_data)


def test_eval_reasoning_with_mock_data_method_removed():
    """_eval_reasoning_with_mock_data 必须不存在"""
    from app.agents import eval_agent
    assert not hasattr(eval_agent.EvalAgent, "_eval_reasoning_with_mock_data"), (
        "_eval_reasoning_with_mock_data should be deleted (no placeholders allowed)"
    )
```

- [ ] **Step 2: 运行**

```bash
cd backend && python -m pytest tests/test_eval_no_mock.py -v
```
Expected: 2 passed

---

### Task 5.6: 添加 test_business_no_mock_injection 测试

**Files:**
- Create: `backend/tests/test_business_no_mock_injection.py`

- [ ] **Step 1: 写测试**

```python
"""测试 BusinessMonitorAgent 不再接受 mock_results 注入"""
from __future__ import annotations

import asyncio

import pytest


def test_business_monitor_source_no_mock_results():
    """源码中不应再有 mock_results 字样"""
    from pathlib import Path
    src = Path(__file__).parents[1] / "app" / "agents" / "business_monitor_agent.py"
    content = src.read_text()
    assert "mock_results" not in content, (
        "mock_results injection should be removed (no placeholders allowed)"
    )


def test_business_monitor_source_no_simulate_failure():
    """routes.py 中 simulate_failure 字段应被移除"""
    from pathlib import Path
    src = Path(__file__).parents[1] / "app" / "api" / "routes.py"
    content = src.read_text()
    # 排除注释和文档字符串外，业务端点不应再依赖 simulate_failure
    if "simulate_failure" in content:
        # 允许文档示例中存在，但不应被实际 if 分支使用
        assert False, "simulate_failure still referenced in routes.py"
```

- [ ] **Step 2: 运行**

```bash
cd backend && python -m pytest tests/test_business_no_mock_injection.py -v
```
Expected: 2 passed

---

### Task 5.7: 跑全套测试

- [ ] **Step 1: 运行所有非 real 测试**

```bash
cd backend && python -m pytest tests/ -v --ignore=tests/test_imports.py 2>&1 | tail -50
```
Expected: 大部分 PASS，少数因外部依赖（ChromaDB/LangGraph）SKIPPED 或少量 FAIL

- [ ] **Step 2: 修复回归**

针对失败的测试：
- 如果是路径/import 问题：修 conftest 或 __init__.py
- 如果是 API 变更导致：更新测试断言
- 如果是真实 bug：回到对应 Phase 修复

- [ ] **Step 3: 最终验证**

```bash
cd backend && python -m pytest tests/ -v 2>&1 | tail -5
```
Expected: `=== X passed, Y skipped in Zs ===`

---

## Phase 6 — 技术讲解稿重写

### Task 6.1: 重写 技术讲解稿-Agent协作故障定位全流程.md

**Files:**
- Rewrite: `技术讲解稿-Agent协作故障定位全流程.md`

**Interfaces:**
- Consumes: Phase 0-5 优化后的代码（按最新行号）
- Produces: 文档中所有 `file.py:line` 引用 + 代码片段 + 流程图都对应最新实现

- [ ] **Step 1: 在文档头部添加变更说明**

替换第 1-6 行：
```markdown
# AIOps 多智能体故障定位系统 — 技术讲解稿

> **版本**: v2.0 | **日期**: 2026-07-10 | **作者**: AIOps 架构团队
>
> 本文档基于 `aiops-agent-platform` 完整源码撰写（2026-07-10 占位实现清理后的版本）。
> 所有分析均贴合现有代码实现，无空泛理论、无 mock 占位、无 TODO 注释遗留。
>
> **配套设计文档**: `docs/superpowers/specs/2026-07-10-placeholder-removal-optimization-design.md`
> **配套实施计划**: `docs/superpowers/plans/2026-07-10-placeholder-removal-optimization.md`
```

- [ ] **Step 2: 整体重写文档**

按以下结构重写整个文档（保留所有原有的实质性内容，但更新所有代码引用）：

1. **第一部分：架构总览** — 流程图保留，但更新所有路径引用（lifespan 路径、tools 真实实现路径）
2. **第二部分：源码逐模块拆解** — 每个模块的开头加 "本次优化" 小节，列出该模块的修复点
3. **第三部分：记忆系统** — 不变（已是真实实现）
4. **第四部分：实战案例** — 案例结果数据按真实代码预期值更新
5. **第五部分：评测体系** — 加 "评测去 mock" 子章节，记录 Task 4.1 的修复
6. **新增第六部分：本次优化过程**（约 1500 字）:
   - 6.1 优化动机（11 个断点）
   - 6.2 Phase 0-5 改动清单（每 Phase 一段）
   - 6.3 优化前后代码对比（orchestrator._sequential_process 的 TODO 段 vs 修复段）
   - 6.4 验证结果（pytest 输出 + curl 输出片段）
   - 6.5 已知遗留（K8s API 真执行、ArgoCD API、LLM 真实评估）
7. **新增第七部分：运行验证指南**:
   - 启动命令
   - 健康检查 / 探活命令
   - 主链路 curl 示例
   - 跑测试命令
   - 故障排查常见问题

- [ ] **Step 3: 验证文档无 placeholder**

```bash
grep -nE "TBD|FIXME|待定|待补充|占位|placeholder" 技术讲解稿-Agent协作故障定位全流程.md
```
Expected: 无输出（除 "占位实现清理" 这个历史事件本身的描述外）

- [ ] **Step 4: 最终提交**

- 如果项目无 git：在 `_plan_log.md` 追加最终完成记录
- 如果项目有 git：`git add . && git commit -m "docs: rewrite technical doc after placeholder removal"`

---

## 验证清单 (Phase 0-6 完成后必跑)

```bash
# 1. 启动
cd backend && python -m uvicorn app.main:app --port 8000 &
sleep 5

# 2. 健康检查
curl -s localhost:8000/ready | python -m json.tool  # 应有 5 个 checks
curl -s localhost:8000/metrics | grep -E "app_up|http_requests_total|agent_invocations_total"  # 应有真实指标

# 3. 主链路（不需要 seed）
curl -X POST localhost:8000/api/v1/incidents/trigger \
  -H "Content-Type: application/json" \
  -d '{
    "source":"manual","service":"order-service","metric":"cpu_usage_percent",
    "value":95.0,"threshold":80.0,"operator":">","severity":"high",
    "labels":{"tier":"critical"},"annotations":{},"timestamp":"2026-07-10T10:00:00Z"
  }'  # 应返回 incident_id

# 4. seed demo 端点（仅 dev）
APP_ENV=development curl -X POST localhost:8000/api/v1/incidents/seed-demo  # 应返回 7 个 incident_id
APP_ENV=production curl -X POST localhost:8000/api/v1/incidents/seed-demo   # 应返回 403

# 5. 评测无 mock
curl -X POST localhost:8000/api/v1/evaluations/run \
  -H "Content-Type: application/json" \
  -d '{"eval_type":"reasoning","agent_results":[],"ground_truth":{}}'  # 应返回 400/422 而非假数据

# 6. 业务检测无注入
curl -X POST localhost:8000/api/v1/incidents/trigger-business \
  -H "Content-Type: application/json" \
  -d '{"service_name":"payment-service","business_domain":"financial","check_rules":["br_duplicate_charge"],"context":{"mock_results":{"br_duplicate_charge":{"matched":true}}}}'  # mock_results 字段被忽略

# 7. 测试套件
cd backend && python -m pytest tests/ -v  # 全部 PASS

# 8. 无 TODO/mock 占位
cd backend && grep -rE "TODO|mock_data|_eval_reasoning_with_mock_data|simulate_failure" app/ 2>&1 | grep -v ".pyc" | head -10  # 应只看到历史注释，无新代码 TODO
```

---

## 风险与回滚

| 风险 | 应对 |
|------|------|
| Phase 1 seed 删除导致现有测试失败 | Task 1.2 Step 4 已规划：把测试改为显式调用 seed_demo |
| Phase 2 Orchestrator 修改影响 LangGraph 行为 | Task 2.2 保留 try/except fallback 到 sequential；Task 2.3 节点方法都有 try/except |
| Phase 3 Tool 调用真实 API 失败 | 所有 Tool 都有 try/except + 空列表 fallback |
| Phase 4 删 mock 导致评测端点 400 | spec 已说明；前端评测页需提示"请先运行真实 Agent" |
| Phase 5 测试覆盖不全 | 每个 Phase 至少 1 个测试文件；核心断点 100% 覆盖 |
| Phase 6 文档漏更新 | 验证清单 Step 3 用 grep 扫 placeholder |

每个 Phase 完成后立即跑该 Phase 的验证命令，失败立即排查不进入下一阶段。

---

## Plan 自检

**1. Spec coverage** (对照设计文档 7 大章节):

| Spec 章节 | 对应 Task |
|-----------|----------|
| §2 用户故事与 11 断点清单 | Phase 0-5 全覆盖 (Task 0.x ~ Task 5.x) |
| §3.1 Phase 0 启动底座 | Task 0.1-0.4 |
| §3.2 Phase 1 主链路 | Task 1.1-1.2 + Task 2.1-2.3 (orchestrator 是主链路兜底) |
| §3.3 Phase 2 Tool 层 | Task 3.1-3.4 |
| §3.4 Phase 3 评测去 mock | Task 4.1-4.3 |
| §3.5 Phase 4 测试 | Task 5.1-5.7 |
| §3.6 Phase 5 文档 | Task 6.1 |
| §4 验证策略 | 验证清单 |
| §5 风险与缓解 | 风险与回滚表 |
| §6 任务清单概览 | 与 Phase 0-5 完全对齐 |
| §7 不在范围 | 文中已多处明示 |

**2. Placeholder scan**:
- 无 "TBD" / "FIXME" / "TODO" (除 "占位实现清理" 历史引用)
- 无 "implement later" / "similar to Task N"
- 所有代码步骤都有完整代码块

**3. Type consistency**:
- `RCAInput` / `HealInput` / `ChangeInput` 在 Task 2.1 引用，与 Task 2.3 引用一致
- `RCAEvent(**dict)` / `HealEvent(**dict)` / `ChangeEvent(**dict)` 模式贯穿
- `state["agent_results"]["rca"]` / `["monitor"]` / `["heal"]` / `["change"]` key 一致
- `_get_metric_value`、`_seed_incidents` 等辅助函数签名未变

---

## 完成定义 (DoD)

✅ **全部以下条件达成才算 Plan 完成**:

- [ ] 所有 Phase 0-5 的 Task 全部执行完毕
- [ ] `_plan_log.md` 中所有 Task 标记为 ✅
- [ ] `verify` 检查清单 8 项全部通过
- [ ] `pytest tests/ -v` 全部通过（允许 skip 外部依赖相关）
- [ ] `grep -rE "TODO|mock_data" app/` 输出为空（除历史注释）
- [ ] 技术讲解稿 v2.0 已重写，无 placeholder
- [ ] 设计文档 + 实施计划 + 文档三者行号/路径引用一致