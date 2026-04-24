#!/usr/bin/env bash
#
# Team Agent 一键启动脚本
# - 后端默认端口 8200，前端默认端口 3200
# - 端口占用时自动 +1 寻找可用端口
# - 停止时自动清理所有子进程
#

set -euo pipefail

# ─── 颜色 ───
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# ─── 项目根目录 ───
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"

# ─── PID / 日志文件 ───
PID_DIR="$PROJECT_DIR/.run"
BACKEND_PID_FILE="$PID_DIR/backend.pid"
FRONTEND_PID_FILE="$PID_DIR/frontend.pid"
PORT_FILE="$PID_DIR/ports.env"
BACKEND_LOG="$PID_DIR/backend.log"
FRONTEND_LOG="$PID_DIR/frontend.log"

# ─── 默认端口 ───
BACKEND_DEFAULT_PORT=8200
FRONTEND_DEFAULT_PORT=3200

# ─── 清理函数 ───
cleanup() {
    echo ""
    echo -e "${YELLOW}⏹  正在停止所有服务...${NC}"

    local stopped=0

    # 停止前端
    if [ -f "$FRONTEND_PID_FILE" ]; then
        local fe_pid
        fe_pid=$(cat "$FRONTEND_PID_FILE")
        if kill -0 "$fe_pid" 2>/dev/null; then
            echo -e "${CYAN}   停止前端 (PID: $fe_pid)${NC}"
            # 杀掉整个进程组
            kill -- -"$fe_pid" 2>/dev/null || kill "$fe_pid" 2>/dev/null || true
            stopped=$((stopped + 1))
        fi
        rm -f "$FRONTEND_PID_FILE"
    fi

    # 停止后端
    if [ -f "$BACKEND_PID_FILE" ]; then
        local be_pid
        be_pid=$(cat "$BACKEND_PID_FILE")
        if kill -0 "$be_pid" 2>/dev/null; then
            echo -e "${CYAN}   停止后端 (PID: $be_pid)${NC}"
            kill -- -"$be_pid" 2>/dev/null || kill "$be_pid" 2>/dev/null || true
            stopped=$((stopped + 1))
        fi
        rm -f "$BACKEND_PID_FILE"
    fi

    # 清理端口文件
    rm -f "$PORT_FILE"

    if [ $stopped -gt 0 ]; then
        echo -e "${GREEN}✓  已停止 $stopped 个服务${NC}"
    else
        echo -e "${YELLOW}   没有运行中的服务${NC}"
    fi

    exit 0
}

trap cleanup SIGINT SIGTERM EXIT

# ─── 检查端口是否占用 ───
is_port_in_use() {
    local port=$1
    if command -v lsof &>/dev/null; then
        lsof -iTCP:"$port" -sTCP:LISTEN -P -n &>/dev/null
    elif command -v ss &>/dev/null; then
        ss -tlnp "sport = :$port" &>/dev/null
    elif command -v netstat &>/dev/null; then
        netstat -tlnp 2>/dev/null | grep -q ":$port "
    else
        ! (echo > /dev/tcp/127.0.0.1/"$port") 2>/dev/null
    fi
}

# ─── 查找可用端口 ───
find_available_port() {
    local port=$1
    local max_tries=100
    local i=0
    while [ $i -lt $max_tries ]; do
        if ! is_port_in_use "$port"; then
            echo "$port"
            return 0
        fi
        port=$((port + 1))
        i=$((i + 1))
    done
    echo -e "${RED}错误: 从 $1 开始找不到可用端口 (尝试了 $max_tries 次)${NC}" >&2
    return 1
}

# ─── 等待端口就绪 ───
wait_for_port() {
    local port=$1
    local name=$2
    local max_wait=${3:-30}
    local elapsed=0
    while [ $elapsed -lt $max_wait ]; do
        if is_port_in_use "$port"; then
            return 0
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
    echo -e "${YELLOW}警告: $name 在 ${max_wait}s 内未就绪 (端口 $port)${NC}"
    return 1
}

# ─── 检查是否已有运行实例 ───
check_running() {
    if [ -f "$BACKEND_PID_FILE" ] || [ -f "$FRONTEND_PID_FILE" ]; then
        echo -e "${YELLOW}检测到上次的运行记录，正在清理...${NC}"
        local be_pid fe_pid
        [ -f "$BACKEND_PID_FILE" ] && be_pid=$(cat "$BACKEND_PID_FILE") && kill -0 "$be_pid" 2>/dev/null && kill -- -"$be_pid" 2>/dev/null || true
        [ -f "$FRONTEND_PID_FILE" ] && fe_pid=$(cat "$FRONTEND_PID_FILE") && kill -0 "$fe_pid" 2>/dev/null && kill -- -"$fe_pid" 2>/dev/null || true
        rm -f "$BACKEND_PID_FILE" "$FRONTEND_PID_FILE" "$PORT_FILE"
        sleep 1
    fi
}

# ─── 确保 .env 文件存在 ───
ensure_env() {
    local env_file="$PROJECT_DIR/.env"
    if [ ! -f "$env_file" ]; then
        echo -e "${YELLOW}   生成 .env 配置文件...${NC}"
        local enc_key
        enc_key=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))" 2>/dev/null || echo "change-me-please-generate-a-new-key")
        cat > "$env_file" <<EOF
# Team Agent Configuration

# Server
HOST=0.0.0.0
PORT=8200
DEBUG=true

# Database
DATABASE_URL=sqlite+aiosqlite:///./data/team_agent.db

# Encryption key for API keys
ENCRYPTION_KEY=$enc_key

# CORS
CORS_ORIGINS=["http://localhost:3200","http://127.0.0.1:3200","http://localhost:3000","http://127.0.0.1:3000"]

# Default LLM Provider
DEFAULT_LLM_PROVIDER=openai
DEFAULT_LLM_MODEL=gpt-4o-mini

# Budget
DEFAULT_SESSION_BUDGET_USD=10.0
EOF
        echo -e "   ${GREEN}✓ .env 已生成${NC}"
    fi
}

# ─── 主逻辑 ───
main() {
    mkdir -p "$PID_DIR"
    check_running
    ensure_env

    echo -e "${GREEN}🚀 Team Agent 一键启动${NC}"
    echo ""

    # 查找可用端口
    local backend_port frontend_port
    backend_port=$(find_available_port $BACKEND_DEFAULT_PORT) || exit 1
    frontend_port=$(find_available_port $FRONTEND_DEFAULT_PORT) || exit 1

    echo -e "   后端端口: ${CYAN}$backend_port${NC} (默认 $BACKEND_DEFAULT_PORT)"
    echo -e "   前端端口: ${CYAN}$frontend_port${NC} (默认 $FRONTEND_DEFAULT_PORT)"
    echo ""

    # 保存端口信息
    cat > "$PORT_FILE" <<EOF
BACKEND_PORT=$backend_port
FRONTEND_PORT=$frontend_port
EOF

    # ─── 启动后端 ───
    echo -e "${GREEN}▶  启动后端...${NC}"
    cd "$BACKEND_DIR"

    # 检查 venv
    local python_cmd="python3"
    if [ -d ".venv" ]; then
        python_cmd=".venv/bin/python"
    elif [ -d "venv" ]; then
        python_cmd="venv/bin/python"
    fi

    # 确保 data 目录存在
    mkdir -p data

    # 清空旧日志
    > "$BACKEND_LOG"

    # 设置环境变量并启动（日志输出到文件）
    set -m
    PORT="$backend_port" \
    HOST="0.0.0.0" \
    CORS_ORIGINS="[\"http://localhost:$frontend_port\",\"http://127.0.0.1:$frontend_port\"]" \
        $python_cmd -m uvicorn app.main:app --host 0.0.0.0 --port "$backend_port" --reload \
        >> "$BACKEND_LOG" 2>&1 &
    local backend_pid=$!
    set +m

    echo "$backend_pid" > "$BACKEND_PID_FILE"
    echo -e "   后端 PID: $backend_pid"
    echo -e "   日志: $BACKEND_LOG"

    # 等待后端就绪
    if wait_for_port "$backend_port" "后端" 30; then
        echo -e "   ${GREEN}✓ 后端就绪${NC} → http://localhost:$backend_port"
    else
        echo -e "   ${RED}✗ 后端启动失败，查看日志: $BACKEND_LOG${NC}"
        echo -e "   最近日志:"
        tail -5 "$BACKEND_LOG" 2>/dev/null | sed 's/^/     /'
    fi

    # ─── 启动前端 ───
    echo ""
    echo -e "${GREEN}▶  启动前端...${NC}"
    cd "$FRONTEND_DIR"

    # 检查 node_modules
    if [ ! -d "node_modules" ]; then
        echo -e "${YELLOW}   安装前端依赖...${NC}"
        npm install --silent 2>/dev/null || npm install
    fi

    # 创建/更新 .env.local
    cat > .env.local <<EOF
NEXT_PUBLIC_API_URL=http://localhost:$backend_port
NEXT_PUBLIC_BACKEND_PORT=$backend_port
EOF

    # 清空旧日志
    > "$FRONTEND_LOG"

    set -m
    PORT="$frontend_port" npx next dev --port "$frontend_port" \
        >> "$FRONTEND_LOG" 2>&1 &
    local frontend_pid=$!
    set +m

    echo "$frontend_pid" > "$FRONTEND_PID_FILE"
    echo -e "   前端 PID: $frontend_pid"
    echo -e "   日志: $FRONTEND_LOG"

    # 等待前端就绪
    if wait_for_port "$frontend_port" "前端" 60; then
        echo -e "   ${GREEN}✓ 前端就绪${NC} → http://localhost:$frontend_port"
    else
        echo -e "   ${RED}✗ 前端启动失败，查看日志: $FRONTEND_LOG${NC}"
        echo -e "   最近日志:"
        tail -5 "$FRONTEND_LOG" 2>/dev/null | sed 's/^/     /'
    fi

    # ─── 启动完成 ───
    echo ""
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${GREEN}✓  所有服务已启动！${NC}"
    echo -e ""
    echo -e "   前端:    ${CYAN}http://localhost:$frontend_port${NC}"
    echo -e "   后端:    ${CYAN}http://localhost:$backend_port${NC}"
    echo -e "   API文档: ${CYAN}http://localhost:$backend_port/docs${NC}"
    echo -e ""
    echo -e "   后端日志: $BACKEND_LOG"
    echo -e "   前端日志: $FRONTEND_LOG"
    echo -e ""
    echo -e "   按 ${YELLOW}Ctrl+C${NC} 停止所有服务"
    echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""

    # 持续运行，等待中断信号
    wait
}

# ─── stop 子命令 ───
stop_services() {
    echo -e "${YELLOW}⏹  停止 Team Agent 服务...${NC}"
    local stopped=0

    if [ -f "$BACKEND_PID_FILE" ]; then
        local be_pid
        be_pid=$(cat "$BACKEND_PID_FILE")
        if kill -0 "$be_pid" 2>/dev/null; then
            echo -e "   停止后端 (PID: $be_pid)"
            kill -- -"$be_pid" 2>/dev/null || kill "$be_pid" 2>/dev/null || true
            stopped=$((stopped + 1))
        fi
        rm -f "$BACKEND_PID_FILE"
    fi

    if [ -f "$FRONTEND_PID_FILE" ]; then
        local fe_pid
        fe_pid=$(cat "$FRONTEND_PID_FILE")
        if kill -0 "$fe_pid" 2>/dev/null; then
            echo -e "   停止前端 (PID: $fe_pid)"
            kill -- -"$fe_pid" 2>/dev/null || kill "$fe_pid" 2>/dev/null || true
            stopped=$((stopped + 1))
        fi
        rm -f "$FRONTEND_PID_FILE"
    fi

    rm -f "$PORT_FILE"

    if [ $stopped -gt 0 ]; then
        echo -e "${GREEN}✓  已停止 $stopped 个服务${NC}"
    else
        echo -e "${YELLOW}   没有运行中的服务${NC}"
    fi
}

# ─── status 子命令 ───
show_status() {
    echo -e "${CYAN}Team Agent 服务状态:${NC}"
    echo ""

    if [ -f "$BACKEND_PID_FILE" ]; then
        local be_pid
        be_pid=$(cat "$BACKEND_PID_FILE")
        if kill -0 "$be_pid" 2>/dev/null; then
            echo -e "   后端: ${GREEN}运行中${NC} (PID: $be_pid)"
        else
            echo -e "   后端: ${RED}已停止${NC} (残留 PID 文件)"
        fi
    else
        echo -e "   后端: ${YELLOW}未启动${NC}"
    fi

    if [ -f "$FRONTEND_PID_FILE" ]; then
        local fe_pid
        fe_pid=$(cat "$FRONTEND_PID_FILE")
        if kill -0 "$fe_pid" 2>/dev/null; then
            echo -e "   前端: ${GREEN}运行中${NC} (PID: $fe_pid)"
        else
            echo -e "   前端: ${RED}已停止${NC} (残留 PID 文件)"
        fi
    else
        echo -e "   前端: ${YELLOW}未启动${NC}"
    fi

    if [ -f "$PORT_FILE" ]; then
        source "$PORT_FILE"
        echo ""
        echo -e "   后端端口: $BACKEND_PORT"
        echo -e "   前端端口: $FRONTEND_PORT"
    fi

    echo ""
    echo -e "   日志: $PID_DIR/backend.log / $PID_DIR/frontend.log"
}

# ─── logs 子命令 ───
show_logs() {
    local target=${1:-all}
    case "$target" in
        backend|be)
            echo -e "${CYAN}后端日志 (Ctrl+C 退出):${NC}"
            tail -f "$BACKEND_LOG" 2>/dev/null || echo "日志文件不存在"
            ;;
        frontend|fe)
            echo -e "${CYAN}前端日志 (Ctrl+C 退出):${NC}"
            tail -f "$FRONTEND_LOG" 2>/dev/null || echo "日志文件不存在"
            ;;
        *)
            echo -e "${CYAN}后端日志:${NC}"
            tail -20 "$BACKEND_LOG" 2>/dev/null || echo "无"
            echo ""
            echo -e "${CYAN}前端日志:${NC}"
            tail -20 "$FRONTEND_LOG" 2>/dev/null || echo "无"
            ;;
    esac
}

# ─── 子命令分发 ───
case "${1:-}" in
    stop)
        stop_services
        ;;
    status)
        show_status
        ;;
    logs)
        show_logs "${2:-all}"
        ;;
    restart)
        stop_services
        sleep 2
        main
        ;;
    help|--help|-h)
        echo "用法: $0 [命令]"
        echo ""
        echo "命令:"
        echo "  (无)       启动所有服务 (默认)"
        echo "  stop       停止所有服务"
        echo "  status     查看服务状态"
        echo "  logs       查看日志 (backend/frontend/all)"
        echo "  restart    重启所有服务"
        echo "  help       显示帮助信息"
        echo ""
        echo "默认端口: 后端 8200, 前端 3200 (占用时自动 +1)"
        ;;
    *)
        main
        ;;
esac
