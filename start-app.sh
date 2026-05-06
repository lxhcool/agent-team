#!/usr/bin/env bash
#
# Team Agent 桌面 App 一键启动脚本
# - 启动 Electron 开发壳
# - Electron 内部会自动拉起本地 FastAPI + Next
# - 与 start.sh（网页模式）分离，避免混用
#

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
FRONTEND_DIR="$PROJECT_DIR/frontend"
BACKEND_DIR="$PROJECT_DIR/backend"
PID_DIR="$PROJECT_DIR/.run"
APP_PID_FILE="$PID_DIR/desktop-app.pid"
APP_LOG="$PID_DIR/desktop-app.log"
ENV_FILE="$PROJECT_DIR/.env"
FRONTEND_STAMP_FILE="$PID_DIR/frontend-deps.sha256"
BACKEND_STAMP_FILE="$PID_DIR/backend-deps.sha256"
BACKEND_DEFAULT_PORT=8200
FRONTEND_DEFAULT_PORT=3200
PORT_SCAN_RANGE=10

kill_port_if_owned_by_project() {
    local port=$1
    if ! command -v lsof >/dev/null 2>&1; then
        return 0
    fi

    local pids
    pids=$(lsof -t -iTCP:"$port" -sTCP:LISTEN -n -P 2>/dev/null | sort -u || true)
    [ -z "$pids" ] && return 0

    local pid cmd
    for pid in $pids; do
        cmd=$(ps -p "$pid" -o command= 2>/dev/null || true)
        case "$cmd" in
            *"$PROJECT_DIR"*|*"uvicorn app.main:app"*|*"next dev"*|*"electron ."*|*"team-agent-frontend"*)
                echo -e "${YELLOW}   清理端口 $port 上的旧进程 (PID: $pid)${NC}"
                kill -TERM "$pid" 2>/dev/null || true
                sleep 1
                kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null || true
                ;;
        esac
    done
}

kill_project_port_range() {
    local base_port=$1
    local end_port=$((base_port + PORT_SCAN_RANGE))
    local port=$base_port
    while [ "$port" -le "$end_port" ]; do
        kill_port_if_owned_by_project "$port"
        port=$((port + 1))
    done
}

reset_frontend_cache() {
    local next_dir="$FRONTEND_DIR/.next"
    if [ -d "$next_dir" ]; then
        echo -e "${YELLOW}   清理前端构建缓存 .next${NC}"
        rm -rf "$next_dir"
    fi
}

file_sha256() {
    local file=$1
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$file" | awk '{print $1}'
        return 0
    fi
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$file" | awk '{print $1}'
        return 0
    fi
    echo ""
}

ensure_env() {
    if [ -f "$ENV_FILE" ]; then
        return 0
    fi

    echo -e "${YELLOW}   生成 .env 配置文件...${NC}"
    local enc_key
    enc_key=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))" 2>/dev/null || echo "change-me-please-generate-a-new-key")

    if [ -f "$PROJECT_DIR/.env.example" ]; then
        cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
        if grep -q '^ENCRYPTION_KEY=' "$ENV_FILE"; then
            perl -0pi -e 's/^ENCRYPTION_KEY=.*/ENCRYPTION_KEY='"$enc_key"'/m' "$ENV_FILE"
        else
            printf '\nENCRYPTION_KEY=%s\n' "$enc_key" >> "$ENV_FILE"
        fi
    else
        printf '%s\n' \
            "HOST=0.0.0.0" \
            "PORT=8200" \
            "DEBUG=true" \
            "DATABASE_URL=sqlite+aiosqlite:///./data/team_agent.db" \
            "ENCRYPTION_KEY=$enc_key" \
            "CORS_ORIGINS=[\"http://localhost:3200\",\"http://127.0.0.1:3200\",\"http://localhost:3000\",\"http://127.0.0.1:3000\"]" \
            > "$ENV_FILE"
    fi

    echo -e "${GREEN}   ✓ .env 已生成${NC}"
}

ensure_backend_venv() {
    if [ -x "$BACKEND_DIR/.venv/bin/python" ]; then
        return 0
    fi

    echo -e "${YELLOW}   创建后端虚拟环境...${NC}"
    (cd "$BACKEND_DIR" && python3 -m venv .venv)
    echo -e "${GREEN}   ✓ 后端虚拟环境已创建${NC}"
}

ensure_backend_deps() {
    local requirements_file="$BACKEND_DIR/requirements.txt"
    local current_hash=""
    local installed_hash=""

    [ -f "$requirements_file" ] || return 0

    current_hash=$(file_sha256 "$requirements_file")
    installed_hash=$(cat "$BACKEND_STAMP_FILE" 2>/dev/null || true)

    if [ -n "$current_hash" ] && [ "$current_hash" = "$installed_hash" ]; then
        return 0
    fi

    echo -e "${YELLOW}   安装后端依赖...${NC}"
    (
        cd "$BACKEND_DIR"
        ./.venv/bin/python -m pip install --upgrade pip >/dev/null
        ./.venv/bin/python -m pip install -r requirements.txt
    )
    [ -n "$current_hash" ] && echo "$current_hash" > "$BACKEND_STAMP_FILE"
    echo -e "${GREEN}   ✓ 后端依赖已就绪${NC}"
}

ensure_frontend_deps() {
    local lock_file="$FRONTEND_DIR/package-lock.json"
    local package_file="$FRONTEND_DIR/package.json"
    local source_file="$lock_file"
    local current_hash=""
    local installed_hash=""

    if [ ! -f "$source_file" ]; then
        source_file="$package_file"
    fi
    current_hash=$(file_sha256 "$source_file")
    installed_hash=$(cat "$FRONTEND_STAMP_FILE" 2>/dev/null || true)

    if [ -d "$FRONTEND_DIR/node_modules" ] && [ -n "$current_hash" ] && [ "$current_hash" = "$installed_hash" ]; then
        return 0
    fi

    echo -e "${YELLOW}   安装前端依赖...${NC}"
    (
        cd "$FRONTEND_DIR"
        npm install
    )
    [ -n "$current_hash" ] && echo "$current_hash" > "$FRONTEND_STAMP_FILE"
    echo -e "${GREEN}   ✓ 前端依赖已就绪${NC}"
}

doctor() {
    mkdir -p "$PID_DIR"
    echo -e "${CYAN}Team Agent 桌面 App 环境检查:${NC}"

    if command -v node >/dev/null 2>&1; then
        echo -e "   Node: ${GREEN}$(node -v)${NC}"
    else
        echo -e "   Node: ${RED}未安装${NC}"
    fi

    if command -v npm >/dev/null 2>&1; then
        echo -e "   npm:  ${GREEN}$(npm -v)${NC}"
    else
        echo -e "   npm:  ${RED}未安装${NC}"
    fi

    if command -v python3 >/dev/null 2>&1; then
        echo -e "   Python: ${GREEN}$(python3 --version 2>&1)${NC}"
    else
        echo -e "   Python: ${RED}未安装${NC}"
    fi

    [ -f "$ENV_FILE" ] \
        && echo -e "   .env: ${GREEN}已存在${NC}" \
        || echo -e "   .env: ${YELLOW}缺失，启动时会自动生成${NC}"

    [ -x "$BACKEND_DIR/.venv/bin/python" ] \
        && echo -e "   backend/.venv: ${GREEN}已存在${NC}" \
        || echo -e "   backend/.venv: ${YELLOW}缺失，启动时会自动创建${NC}"

    [ -d "$FRONTEND_DIR/node_modules" ] \
        && echo -e "   frontend/node_modules: ${GREEN}已存在${NC}" \
        || echo -e "   frontend/node_modules: ${YELLOW}缺失，启动时会自动安装${NC}"

    echo -e "   日志文件: $APP_LOG"
}

cleanup_stale_pid() {
    if [ ! -f "$APP_PID_FILE" ]; then
        return 0
    fi

    local pid
    pid=$(cat "$APP_PID_FILE" 2>/dev/null || true)
    if [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
        return 0
    fi
    rm -f "$APP_PID_FILE"
}

stop_app() {
    cleanup_stale_pid
    echo -e "${YELLOW}⏹  停止桌面 App...${NC}"

    if [ ! -f "$APP_PID_FILE" ]; then
        echo -e "${YELLOW}   桌面 App 未运行${NC}"
        return 0
    fi

    local pid
    pid=$(cat "$APP_PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
        kill -- -"$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
        sleep 1
        kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
        echo -e "${GREEN}✓  已停止桌面 App (PID: $pid)${NC}"
    else
        echo -e "${YELLOW}   桌面 App 进程已不存在${NC}"
    fi
    rm -f "$APP_PID_FILE"
}

show_status() {
    cleanup_stale_pid
    echo -e "${CYAN}Team Agent 桌面 App 状态:${NC}"
    if [ -f "$APP_PID_FILE" ]; then
        local pid
        pid=$(cat "$APP_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo -e "   状态: ${GREEN}运行中${NC} (PID: $pid)"
            echo -e "   日志: $APP_LOG"
            return 0
        fi
    fi
    echo -e "   状态: ${YELLOW}未启动${NC}"
    echo -e "   日志: $APP_LOG"
}

show_logs() {
    echo -e "${CYAN}桌面 App 日志 (Ctrl+C 退出):${NC}"
    tail -f "$APP_LOG" 2>/dev/null || echo "日志文件不存在"
}

prepare_runtime() {
    mkdir -p "$PID_DIR"
    cleanup_stale_pid
    ensure_env
    ensure_backend_venv
    ensure_backend_deps
    ensure_frontend_deps
    kill_project_port_range "$BACKEND_DEFAULT_PORT"
    kill_project_port_range "$FRONTEND_DEFAULT_PORT"
    reset_frontend_cache
    mkdir -p "$BACKEND_DIR/data"
}

start_app() {
    mkdir -p "$PID_DIR"
    prepare_runtime

    if [ -f "$APP_PID_FILE" ]; then
        local pid
        pid=$(cat "$APP_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo -e "${YELLOW}桌面 App 已在运行 (PID: $pid)${NC}"
            echo -e "   如需重启：${CYAN}./start-app.sh restart${NC}"
            return 0
        fi
        rm -f "$APP_PID_FILE"
    fi

    : > "$APP_LOG"

    echo -e "${GREEN}🚀 启动 Team Agent 桌面 App...${NC}"
    (
        cd "$FRONTEND_DIR"
        set -m
        ELECTRON_ENABLE_LOGGING=1 npm run desktop:dev >> "$APP_LOG" 2>&1
    ) &
    local app_pid=$!
    echo "$app_pid" > "$APP_PID_FILE"

    echo -e "${GREEN}✓  桌面 App 已启动${NC}"
    echo -e "   PID: $app_pid"
    echo -e "   日志: $APP_LOG"
    echo -e "   停止命令: ${CYAN}./start-app.sh stop${NC}"
}

case "${1:-}" in
    stop)
        stop_app
        ;;
    restart)
        stop_app
        sleep 1
        start_app
        ;;
    status)
        show_status
        ;;
    logs)
        show_logs
        ;;
    doctor)
        doctor
        ;;
    help|--help|-h)
        echo "用法: $0 [命令]"
        echo ""
        echo "命令:"
        echo "  (无)       启动桌面 App"
        echo "  stop       停止桌面 App"
        echo "  restart    重启桌面 App"
        echo "  status     查看桌面 App 状态"
        echo "  logs       查看桌面 App 日志"
        echo "  doctor     检查本地运行环境"
        ;;
    *)
        start_app
        ;;
esac
