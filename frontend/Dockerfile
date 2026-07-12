# =============================================================================
# AIOps Agent Platform - Frontend Dockerfile
# =============================================================================
# 多阶段构建：
#  1. 构建阶段 (builder): 使用Node.js编译TypeScript/React源码
#  2. 生产阶段 (nginx): 使用Nginx提供静态文件服务
# =============================================================================

# =============================================================================
# 构建阶段 (Builder)
# =============================================================================
FROM node:20-alpine AS builder

WORKDIR /app

# 安装构建工具（node-gyp等可能需要）
RUN apk add --no-cache python3 make g++

# 先复制依赖文件（利用Docker层缓存）
COPY package.json package-lock.json* ./

# 安装依赖（使用npm ci确保锁定版本）
RUN npm ci --only=production=false --ignore-scripts

# 复制源码文件
COPY . .

# 执行构建（输出到dist目录）
RUN npm run build

# =============================================================================
# 生产阶段 (Nginx)
# =============================================================================
FROM nginx:alpine

LABEL maintainer="AIOps Team" \
      description="AIOps Agent Platform - Frontend Web Server" \
      version="0.1.0"

# 安装wget用于健康检查
RUN apk add --no-cache wget

# 复制Nginx配置文件
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 从构建阶段复制构建产物到Nginx目录
COPY --from=builder /app/dist /usr/share/nginx/html

# 确保文件权限正确
RUN chown -R nginx:nginx /usr/share/nginx/html && \
    chmod -R 755 /usr/share/nginx/html

# 移除默认的Nginx配置（避免冲突）
RUN rm -f /etc/nginx/conf.d/default.conf 2>/dev/null || true

# 重新复制Nginx配置
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 创建必要的Nginx目录
RUN mkdir -p /var/cache/nginx /var/log/nginx && \
    chown -R nginx:nginx /var/cache/nginx /var/log/nginx

# 切换到非root用户
USER nginx

# 暴露HTTP端口
EXPOSE 80

# 健康检查：验证Nginx是否正常运行
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://localhost/ || exit 1

# 前台运行Nginx
CMD ["nginx", "-g", "daemon off;"]
