#!/bin/bash
# =============================================================================
# AIOps Agent Platform - 一键部署脚本
# =============================================================================
# 用法:
#   ./deploy.sh              # 生产环境部署
#   ./deploy.sh dev          # 开发环境部署
#   ./deploy.sh stop         # 停止服务
#   ./deploy.sh restart      # 重启服务
#   ./deploy.sh logs         # 查看日志
#   ./deploy.sh status       # 查看状态
#   ./deploy.sh cleanup      # 清理数据（谨慎使用）
# =============================================================================

set -euo pipefail

# 颜色定义
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[0;34m'
readonly CYAN='\033[0;36m'
readonly BOLD='\033[1m'
readonly NC='\033[0m' # No Color

# 项目配置
readonly PROJECT_NAME="aiops-agent-platform"
readonly COMPOSE_FILE="docker-compose.yml"
readonly COMPOSE_DEV_FILE="docker-compose.dev.yml"

# =============================================================================
# 工具函数
# =============================================================================

log_info() {
    echo -e "${BLUE}[INFO]${NC}  $1"
}

log_success() {
    echo -e "${GREEN}[OK]${NC}   $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERR]${NC}  $1"
}

log_step() {
    echo -e ""
    echo -e "${CYAN}${BOLD}=== $1 ===${NC}"
}

# 检查命令是否存在
check_command() {
    if ! command -v "$1" &> /dev/null; then
        log_error "$1 未安装，请先安装"
        exit 1
    fi
}

# 检查Docker服务状态
check_docker() {
    if ! docker info &> /dev/null; then
        log_error "Docker 服务未运行，请启动 Docker"
        exit 1
    fi
    log_success "Docker 服务运行正常"
}

# 显示Banner
show_banner() {
    echo -e ""
    echo -e "${CYAN}${BOLD}"
    echo -e "    ___    ____  ____       ___             __             __"
    echo -e "   /   |  / __ \/  _/      /   | __  ______/ /_____ ______/ /_"
    echo -e "  / /| | / /_/ // /       / /| |/ / / / __  / ____/ ___/ __ \\"
    echo -e " / ___ |/ ____// /       / ___ / /_/ / /_/ / /__/ /__/ / / /"
    echo -e "/_/  |_/_/   /___/      /_/  |_\\__,_/\\__,_/\\___/\\___/_/ /_/"
    echo -e "${NC}"
    echo -e "  ${BOLD}多智能体智能化运维故障定位系统${NC}"
    echo -e "  ${CYAN}https://github.com/your-org/aiops-agent-platform${NC}"
    echo -e ""
}

# =============================================================================
# 部署功能
# =============================================================================

deploy_production() {
    log_step "生产环境部署"

    # 检查环境文件
    if [ ! -f .env ]; then
        log_warn ".env 文件不存在，正在从模板创建..."
        cp .env.example .env
        log_info "已创建 .env 文件，请编辑填入你的 API Key"
        echo ""
        echo -e "  ${YELLOW}需要配置的变量：${NC}"
        echo -e "    - LLM_API_KEY      (必填) LLM API 密钥"
        echo -e "    - OPENAI_API_KEY   (可选) OpenAI 专用密钥"
        echo -e "    - DEEPSEEK_API_KEY (可选) DeepSeek 专用密钥"
        echo -e ""
        read -p "是否现在编辑 .env 文件? (y/N): " -n 1 -r
        echo ""
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            ${EDITOR:-vi} .env
        fi
        exit 1
    fi

    # 验证必要的环境变量
    if [ -z "${LLM_API_KEY:-}" ] && grep -q "^LLM_API_KEY=your-" .env 2>/dev/null; then
        log_error "LLM_API_KEY 未配置，请编辑 .env 文件填入有效的 API Key"
        exit 1
    fi

    # 拉取最新镜像（如有远程镜像）
    log_info "拉取基础镜像..."
    docker compose -f ${COMPOSE_FILE} pull 2>/dev/null || true

    # 构建服务
    log_info "构建服务..."
    docker compose -f ${COMPOSE_FILE} build --no-cache

    # 启动服务
    log_info "启动服务..."
    docker compose -f ${COMPOSE_FILE} up -d --remove-orphans

    # 等待服务就绪
    log_info "等待服务就绪..."
    sleep 5

    # 健康检查
    check_health

    echo ""
    log_success "生产环境部署完成！"
    show_endpoints
}

deploy_development() {
    log_step "开发环境部署"

    # 检查环境文件
    if [ ! -f .env ]; then
        log_warn ".env 文件不存在，正在从模板创建..."
        cp .env.example .env
    fi

    # 构建并启动开发环境
    log_info "构建并启动开发环境..."
    docker compose -f ${COMPOSE_DEV_FILE} up -d --build

    echo ""
    log_success "开发环境已启动！"
    echo ""
    echo -e "  ${BOLD}服务地址：${NC}"
    echo -e "    后端API:   ${CYAN}http://localhost:8000${NC}"
    echo -e "    前端页面:  ${CYAN}http://localhost:3000${NC}"
    echo -e "    ChromaDB:  ${CYAN}http://localhost:8001${NC}"
    echo -e ""
    echo -e "  ${YELLOW}提示：代码修改后会自动热重载${NC}"
    echo -e "  ${YELLOW}查看日志: ./deploy.sh logs dev${NC}"
}

stop_services() {
    local compose_file=${1:-$COMPOSE_FILE}
    log_step "停止服务"
    docker compose -f ${compose_file} down --remove-orphans
    log_success "服务已停止"
}

restart_services() {
    local compose_file=${1:-$COMPOSE_FILE}
    log_step "重启服务"
    docker compose -f ${compose_file} restart
    sleep 3
    check_health
    log_success "服务已重启"
}

show_logs() {
    local compose_file=${1:-$COMPOSE_FILE}
    local service=${2:-}
    log_step "查看日志"
    if [ -n "$service" ]; then
        docker compose -f ${compose_file} logs -f "$service"
    else
        docker compose -f ${compose_file} logs -f
    fi
}

show_status() {
    log_step "服务状态"
    echo ""
    echo -e "${BOLD}容器状态：${NC}"
    docker compose ps 2>/dev/null || docker ps --filter "name=aiops-" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
    echo ""
    echo -e "${BOLD}资源使用：${NC}"
    docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.PIDs}}" 2>/dev/null | grep -E "(aiops-|NAME)" || log_warn "无法获取资源使用统计"
}

cleanup() {
    log_step "数据清理"
    echo ""
    log_warn "此操作将删除所有容器和数据卷，数据将无法恢复！"
    read -p "确定要继续吗? (yes/no): " -r
    if [ "$REPLY" = "yes" ]; then
        docker compose -f ${COMPOSE_FILE} down -v --remove-orphans
        docker compose -f ${COMPOSE_DEV_FILE} down -v --remove-orphans 2>/dev/null || true
        log_success "所有容器和数据卷已清理"
    else
        log_info "操作已取消"
    fi
}

check_health() {
    log_info "检查服务健康状态..."

    # 检查后端
    local retries=0
    local max_retries=30
    while [ $retries -lt $max_retries ]; do
        if curl -f -s http://localhost:8000/health > /dev/null 2>&1; then
            log_success "后端服务健康"
            break
        fi
        retries=$((retries + 1))
        if [ $retries -eq $max_retries ]; then
            log_warn "后端服务健康检查超时，请查看日志"
            break
        fi
        sleep 1
    done

    # 检查前端
    if curl -f -s http://localhost:8080 > /dev/null 2>&1; then
        log_success "前端服务健康"
    else
        log_warn "前端服务暂不可用"
    fi
}

show_endpoints() {
    echo ""
    echo -e "  ${BOLD}${GREEN}服务访问地址：${NC}"
    echo -e "  ───────────────────────────────────────"
    echo -e "    前端界面:    ${CYAN}http://localhost:8080${NC}"
    echo -e "    后端API:     ${CYAN}http://localhost:8000${NC}"
    echo -e "    API文档:     ${CYAN}http://localhost:8000/docs${NC}"
    echo -e "    ReDoc文档:   ${CYAN}http://localhost:8000/redoc${NC}"
    echo -e "    ChromaDB:    ${CYAN}http://localhost:8001${NC}"
    echo -e "  ───────────────────────────────────────"
    echo ""
}

show_help() {
    echo ""
    echo -e "${BOLD}用法:${NC} ./deploy.sh [命令]"
    echo ""
    echo -e "${BOLD}命令:${NC}"
    echo -e "  ${CYAN}prod${NC}          生产环境部署（默认）"
    echo -e "  ${CYAN}dev${NC}           开发环境部署（支持热重载）"
    echo -e "  ${CYAN}stop${NC}          停止生产环境服务"
    echo -e "  ${CYAN}stop-dev${NC}      停止开发环境服务"
    echo -e "  ${CYAN}restart${NC}       重启生产环境服务"
    echo -e "  ${CYAN}logs${NC}          查看生产环境日志"
    echo -e "  ${CYAN}logs-dev${NC}      查看开发环境日志"
    echo -e "  ${CYAN}status${NC}        查看服务状态"
    echo -e "  ${CYAN}cleanup${NC}       清理所有容器和数据（谨慎使用）"
    echo -e "  ${CYAN}health${NC}        健康检查"
    echo -e "  ${CYAN}help${NC}          显示帮助信息"
    echo ""
    echo -e "${BOLD}示例:${NC}"
    echo -e "  ./deploy.sh           # 生产环境部署"
    echo -e "  ./deploy.sh dev       # 开发环境部署"
    echo -e "  ./deploy.sh logs      # 查看日志"
    echo ""
}

# =============================================================================
# 主入口
# =============================================================================

main() {
    show_banner

    # 检查必要工具
    check_command docker
    check_command docker-compose || check_command "docker compose"
    check_docker

    # 进入项目目录
    cd "$(dirname "$0")"

    # 解析命令
    local cmd=${1:-prod}

    case "$cmd" in
        prod|production|""|deploy)
            deploy_production
            ;;
        dev|development)
            deploy_development
            ;;
        stop)
            stop_services "$COMPOSE_FILE"
            ;;
        stop-dev)
            stop_services "$COMPOSE_DEV_FILE"
            ;;
        restart)
            restart_services "$COMPOSE_FILE"
            ;;
        logs)
            show_logs "$COMPOSE_FILE" "${2:-}"
            ;;
        logs-dev)
            show_logs "$COMPOSE_DEV_FILE" "${2:-}"
            ;;
        status)
            show_status
            ;;
        cleanup|clean)
            cleanup
            ;;
        health)
            check_health
            ;;
        help|--help|-h)
            show_help
            ;;
        *)
            log_error "未知命令: $cmd"
            show_help
            exit 1
            ;;
    esac
}

main "$@"
