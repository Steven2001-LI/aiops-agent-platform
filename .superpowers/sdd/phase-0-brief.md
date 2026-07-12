# Phase 0 Brief: 启动底座 (Foundation)

## 项目上下文
aiops-agent-platform 后端的占位实现清理优化。Phase 0 让 main.py 启动时真正初始化所有基础设施 (Chromadb / Memory / Knowledge / Orchestrator)，替换手写 `/metrics` 和硬编码 `/ready`。

**项目根目录**: `/Users/apple/资料/大模型/项目/运维多智能体故障定位/aiops-agent-platform/`
**Tasks**: Task 0.2 (lifespan + InfrastructureRegistry), Task 0.3 (prometheus metrics), Task 0.4 (real /ready)
**Approach**: 单 subagent 一次性完成 Task 0.2-0.4（都改 main.py）

## 关键约束

**API 校正（与原 plan 不同，已验证）**:
- `ChromaDBStorage.__init__(collection_name="aiops_memory", persist_directory="./data/chromadb")` — 无 host/port 参数
- `ChromaDBStorage` 没有 `initialize()` 方法，使用内置 lazy init (`_ensure_initialized` 是 async 私有)
- `ChromaDBStorage` 没有 `health_check()` 方法 — InfrastructureRegistry 需要自己用 `await storage._ensure_initialized()` 加 `storage._initialized` 判断
- `MemorySystem.get_instance()` 是 async — 已有，先用着
- `PrometheusClient` 已有，`health_check()` 已存在可用

**config.py 没有 infrastructure 子配置** — 不能引用 `config.infrastructure.chroma_host`。直接读环境变量或 hardcode default。

## 步骤

### Step 1: 创建 InfrastructureRegistry

修改 `backend/app/infrastructure/__init__.py`（如果不存在就创建；如果存在但内容不同，覆盖）。完整文件内容：

```python
"""
AIOps Agent Platform - Infrastructure Registry

统一管理所有外部依赖（ChromaDB / Prometheus / MemorySystem）的单例。
提供 initialize() 用于启动初始化、close() 用于关闭清理。
"""
from __future__ import annotations

import os
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
            dict[str, bool]: 每个依赖的就绪状态
        """
        status: dict[str, bool] = {}

        # 1. ChromaDB
        try:
            from app.memory.storage import ChromaDBStorage
            self.chroma_client = ChromaDBStorage(
                collection_name="aiops_memory",
                persist_directory="./data/chromadb",
            )
            await self.chroma_client._ensure_initialized()
            status["chromadb"] = self.chroma_client._initialized
            logger.info("ChromaDB connected", initialized=self.chroma_client._initialized)
        except Exception as e:
            logger.warning("ChromaDB unavailable", error=str(e))
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
            self.prometheus_client = PrometheusClient(
                base_url=os.getenv("PROMETHEUS_URL", "http://localhost:9090")
            )
            health = await self.prometheus_client.health_check()
            status["prometheus"] = health.get("reachable", False)
            logger.info("Prometheus health", **health)
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
        logger.info("Infrastructure closed")
```

### Step 2: 在 main.py 顶部加 import

找到 `backend/app/main.py` 第 12 行 `from fastapi import FastAPI, status` 后面，加入：

```python
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST
from fastapi.responses import Response
```

把 `from fastapi.responses import JSONResponse` 改成跟 `Response` 一行（如果原来分开）— 实际保留两者都导入。

### Step 3: 在 main.py `app = FastAPI(...)` 之前定义 Prometheus 指标

找到第 63 行附近的 `def create_app() -> FastAPI:`，在它之前插入：

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

### Step 4: 改写 lifespan 函数

找到 main.py 第 25-60 行的 `@asynccontextmanager async def lifespan(app: FastAPI):` 整段（包括 yield 和之后），替换为：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理

    启动顺序:
      1. 配置 + 日志
      2. 基础设施 (ChromaDB / Prometheus / Memory)
      3. 知识库加载
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

    # 知识库加载
    try:
        from app.data.knowledge_base import KnowledgeBase
        kb = KnowledgeBase()
        loaded = kb.load_all()
        app.state.knowledge_base = kb
        logger.info("Knowledge base loaded", entries=loaded)
    except Exception as e:
        logger.warning("Knowledge base load failed", error=str(e))
        app.state.knowledge_base = None

    # Orchestrator 构建
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

并在 main.py 顶部 import 区加入 `from app.infrastructure import InfrastructureRegistry` 和 `from app.agents.orchestrator import Orchestrator`。

### Step 5: 替换 /metrics 端点 + 加 HTTP 中间件

找到 main.py 第 124-168 行（`@app.get("/metrics", tags=["Observability"])` 整段），替换为：

```python
@app.middleware("http")
async def prometheus_http_middleware(request, call_next):
    """统计每个 HTTP 请求"""
    response = await call_next(request)
    path_template = request.url.path
    if path_template != "/metrics":
        HTTP_REQUESTS_TOTAL.labels(
            method=request.method,
            path=path_template,
            status=str(response.status_code),
        ).inc()
    return response


@app.get("/metrics", tags=["Observability"])
async def metrics() -> Response:
    """Prometheus 指标采集端点（使用 prometheus_client 真实统计）"""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
```

### Step 6: 替换 /ready 端点

找到 main.py 第 180-188 行（`@app.get("/ready"` 整段），替换为：

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
            await infra.chroma_client._ensure_initialized()
            checks["chromadb"] = {
                "status": "ready" if infra.chroma_client._initialized else "unhealthy"
            }
            if not infra.chroma_client._initialized:
                all_critical_ok = False
        except Exception as e:
            checks["chromadb"] = {"status": "error", "error": str(e)}
            all_critical_ok = False
    else:
        checks["chromadb"] = {"status": "not_initialized"}

    # 2. MemorySystem
    if infra and infra.memory_system:
        checks["memory"] = {"status": "ready"}
    else:
        checks["memory"] = {"status": "degraded"}

    # 3. Prometheus (non-critical)
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

### Step 7: 验证

```bash
cd /Users/apple/资料/大模型/项目/运维多智能体故障定位/aiops-agent-platform/backend
python -c "from app.main import create_app; print('import OK')"
```
Expected: `import OK`

```bash
cd /Users/apple/资料/大模型/项目/运维多智能体故障定位/aiops-agent-platform/backend
timeout 30 python -m uvicorn app.main:app --port 8000 2>&1 | head -60
```
Expected: 日志中包含 `ChromaDB connected` 或 warning、`MemorySystem ready`、`Orchestrator built`、`Startup complete`

```bash
# 另开 terminal
curl -s localhost:8000/ready | python -m json.tool 2>&1 | head -30
```
Expected: JSON 含 5 个 checks（chromadb / memory / prometheus / knowledge_base / langgraph）

```bash
curl -s localhost:8000/metrics | grep -E "^(app_up|http_requests_total)" | head -5
```
Expected: 至少 `app_up 1.0` 出现

### Step 8: 记录 + 清理

在 `_plan_log.md` 追加：
```
Task 0.2-0.4 — main.py 启动底座 + /metrics + /ready  ✅  <时间戳>
```

最后用 Ctrl+C 关闭 uvicorn 进程。

## 报告要求

完成后在 `.superpowers/sdd/phase-0-report.md` 写入：
1. 修改了哪些文件、每个文件的关键 diff 摘要
2. Step 7 三个验证命令的实际输出
3. 是否需要 API 校正（与 brief 不一致的地方）
4. 任何问题

## 约束
- 不要改 routes.py / agents / tools 等其他文件
- 不要重构 main.py 其他无关部分
- 如果某个验证失败，停下来分析 + 报告，不要绕过去
- 端口 8000 占用冲突时换 8001 并在报告里说明