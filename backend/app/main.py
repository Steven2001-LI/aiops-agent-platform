"""
AIOps Agent Platform - FastAPI Application Entry

FastAPI 应用入口，包含应用创建、中间件配置、路由注册和生命周期管理。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST

from app.agents.orchestrator import Orchestrator
from app.api.routes import api_router
from app.api.webhooks import webhook_router
from app.api.websocket import websocket_router
from app.config import get_config
from app.infrastructure import InfrastructureRegistry
from app.services.langfuse_service import get_langfuse_service
from app.utils.logging import configure_logging, get_logger

logger = get_logger(__name__)


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

    # 知识库加载（KnowledgeBase 是静态工具类，直接读取模块级常量）
    try:
        from app.data.knowledge_base import KNOWLEDGE_BASE, SERVICE_TOPOLOGY
        kb_count = len(KNOWLEDGE_BASE)
        topo_count = len(SERVICE_TOPOLOGY)
        # 用 dict 包装保留数据访问能力
        app.state.knowledge_base = {
            "knowledge_entries": kb_count,
            "services": topo_count,
            "loaded": True,
        }
        logger.info("Knowledge base loaded", knowledge_entries=kb_count, services=topo_count)
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

    try:
        await get_langfuse_service().initialize()
    except Exception as e:
        logger.warning("Langfuse init failed, continuing without tracing", error=str(e))

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
    get_langfuse_service().shutdown()
    logger.info("AIOps Agent Platform shutdown complete")


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


def create_app() -> FastAPI:
    """
    创建 FastAPI 应用实例

    Returns:
        FastAPI: 配置完成的 FastAPI 应用
    """
    config = get_config()

    app = FastAPI(
        title="AIOps Agent Platform",
        description="""
        多智能体智能化运维故障定位系统 API

        ## 功能模块

        - **故障管理**: 故障触发、查询、状态跟踪
        - **Agent 管理**: Agent 状态监控、执行控制
        - **评估系统**: Agent 效果评估、基准测试
        - **记忆系统**: 记忆存储、检索
        - **服务拓扑**: 拓扑发现、查询
        """,
        version="0.1.0",
        docs_url="/docs" if config.is_development else None,
        redoc_url="/redoc" if config.is_development else None,
        openapi_url="/openapi.json" if config.is_development else None,
        lifespan=lifespan,
    )

    # === CORS 中间件 ===
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # === 路由注册 ===
    app.include_router(api_router)
    app.include_router(webhook_router)
    app.include_router(websocket_router)

    # === 异常处理 ===
    @app.exception_handler(Exception)
    async def global_exception_handler(request, exc):
        """全局异常处理器"""
        logger.error(
            "Unhandled exception",
            error=str(exc),
            path=request.url.path,
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "Internal Server Error",
                "detail": str(exc) if config.is_development else "Please contact support",
            },
        )

    # === Prometheus Metrics 端点 ===
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

    # === 健康检查 ===
    @app.get("/health", tags=["Health"])
    async def health_check() -> dict[str, str]:
        """健康检查端点"""
        return {
            "status": "healthy",
            "service": "aiops-agent-platform",
            "version": "0.1.0",
        }

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

    # === 根路径 ===
    @app.get("/", tags=["Root"])
    async def root() -> dict[str, str]:
        """API 根路径"""
        return {
            "name": "AIOps Agent Platform",
            "version": "0.1.0",
            "docs": "/docs",
        }

    return app


# 应用实例（用于 uvicorn 启动）
app = create_app()

if __name__ == "__main__":
    import uvicorn

    config = get_config()
    uvicorn.run(
        "app.main:app",
        host=config.host,
        port=config.port,
        reload=config.is_development,
        log_level=config.log_level.lower(),
    )
