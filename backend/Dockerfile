# =============================================================================
# AIOps Agent Platform - Backend Dockerfile
# =============================================================================
# 多阶段构建，优化镜像大小和安全性
# 构建阶段：安装编译依赖并创建虚拟环境
# 生产阶段：仅复制必要文件，以非root用户运行
# =============================================================================

# =============================================================================
# 构建阶段 (Builder)
# =============================================================================
FROM python:3.11-slim AS builder

WORKDIR /app

# 安装构建依赖（编译Python包所需）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libc6-dev \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件（利用Docker层缓存）
COPY requirements.txt .

# 创建虚拟环境并安装Python依赖
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 升级pip并安装依赖
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# =============================================================================
# 生产阶段 (Production)
# =============================================================================
FROM python:3.11-slim

LABEL maintainer="AIOps Team" \
      description="AIOps Agent Platform - Backend API Service" \
      version="0.1.0"

WORKDIR /app

# 安装运行时依赖（curl用于健康检查）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# 复制虚拟环境（从构建阶段）
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 创建非root用户和组（安全最佳实践）
RUN groupadd --system --gid 1000 appgroup && \
    useradd --system --uid 1000 --gid appgroup --shell /bin/false appuser

# 创建数据目录并设置权限
RUN mkdir -p /app/data /app/logs && \
    chown -R appuser:appgroup /app

# 复制应用代码（以非root用户权限）
COPY --chown=appuser:appgroup app/ ./app/

# 切换到非root用户运行
USER appuser

# 暴露服务端口
EXPOSE 8000

# 健康检查：验证API服务是否正常运行
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f -s http://localhost:8000/health > /dev/null || exit 1

# 启动Uvicorn服务
# --host 0.0.0.0: 监听所有网络接口
# --port 8000: 服务端口
# --workers 1: 工作进程数（可根据CPU核心数调整）
# --timeout-keep-alive 30: 保持连接超时时间
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-keep-alive", "30"]
