"""FastAPI 应用 — REST API + WebSocket"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from team_agent.api.auth import AuthManager
from team_agent.config import ProjectConfig

logger = logging.getLogger(__name__)

# 全局状态
_auth_manager: AuthManager | None = None
_session_managers: dict[str, Any] = {}  # user_id -> SessionManager


def get_auth_manager() -> AuthManager:
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = AuthManager()
    return _auth_manager


# === 请求/响应模型 ===


class RegisterRequest(BaseModel):
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    user_id: str
    username: str
    api_key: str
    token: str


class LLMKeyRequest(BaseModel):
    provider: str
    api_key: str
    base_url: str | None = None
    is_default: bool = False


class TaskRequest(BaseModel):
    task: str
    auto_approve: bool = False
    workspace: str = "."


class ChatRequest(BaseModel):
    agent_name: str
    message: str
    session_id: str | None = None


class RoundtableRequest(BaseModel):
    agent_names: list[str]
    message: str


class TeamCreateRequest(BaseModel):
    name: str


class TeamMemberRequest(BaseModel):
    user_id: str
    role: str = "member"


# === 认证依赖 ===


async def get_current_user(api_key: str) -> dict[str, Any]:
    """从 API Key 获取当前用户"""
    auth = get_auth_manager()
    user = auth.authenticate_by_api_key(api_key)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return {"id": user.id, "username": user.username, "is_admin": user.is_admin}


# === 生命周期 ===


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Team Agent API starting...")
    yield
    logger.info("Team Agent API shutting down...")
    # 清理所有会话
    for sm in _session_managers.values():
        for session_id in list(sm.sessions.keys()):
            await sm.destroy_session(session_id)


# === FastAPI 应用 ===


def create_app(config: ProjectConfig | None = None) -> FastAPI:
    app = FastAPI(
        title="Team Agent API",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # === Auth Routes ===

    @app.post("/api/auth/register")
    async def register(req: RegisterRequest):
        auth = get_auth_manager()
        try:
            user = auth.register(req.username, req.password)
            return {"user_id": user.id, "username": user.username, "api_key": user.api_key}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/auth/login")
    async def login(req: LoginRequest):
        auth = get_auth_manager()
        user = auth.authenticate(req.username, req.password)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        import secrets
        token = secrets.token_hex(32)
        return LoginResponse(
            user_id=user.id,
            username=user.username,
            api_key=user.api_key,
            token=token,
        )

    # === LLM Key Routes ===

    @app.post("/api/llm-keys")
    async def save_llm_key(req: LLMKeyRequest, user: dict = Depends(get_current_user)):
        auth = get_auth_manager()
        key_id = auth.save_llm_key(user["id"], req.provider, req.api_key, req.base_url, req.is_default)
        return {"id": key_id, "provider": req.provider}

    @app.get("/api/llm-keys")
    async def list_llm_keys(user: dict = Depends(get_current_user)):
        auth = get_auth_manager()
        keys = auth.get_llm_keys(user["id"])
        return {"keys": keys}

    @app.delete("/api/llm-keys/{key_id}")
    async def delete_llm_key(key_id: str, user: dict = Depends(get_current_user)):
        auth = get_auth_manager()
        success = auth.delete_llm_key(user["id"], key_id)
        if not success:
            raise HTTPException(status_code=404, detail="Key not found")
        return {"deleted": True}

    @app.post("/api/llm-keys/test")
    async def test_llm_key(req: LLMKeyRequest):
        auth = get_auth_manager()
        result = auth.test_llm_key(req.provider, req.api_key, req.base_url)
        return result

    # === Session Routes ===

    @app.post("/api/sessions")
    async def create_session(req: TaskRequest, user: dict = Depends(get_current_user)):
        from team_agent.orchestrator.session import SessionManager

        project_config = config or ProjectConfig()
        sm = SessionManager(project_config)
        _session_managers[user["id"]] = sm

        session = await sm.create_session(user["id"], req.task)
        result = await sm.execute_session(session.id, auto_approve=req.auto_approve)
        return {"session_id": session.id, "result": result}

    @app.post("/api/sessions/{session_id}/approve")
    async def approve_session(session_id: str, user: dict = Depends(get_current_user)):
        sm = _session_managers.get(user["id"])
        if not sm:
            raise HTTPException(status_code=404, detail="No active session manager")
        result = await sm.approve_and_execute(session_id)
        return {"result": result}

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str, user: dict = Depends(get_current_user)):
        sm = _session_managers.get(user["id"])
        if not sm:
            raise HTTPException(status_code=404, detail="No active session manager")
        status = sm.get_session_status(session_id)
        if not status:
            raise HTTPException(status_code=404, detail="Session not found")
        return status

    # === Chat Routes ===

    @app.post("/api/chat")
    async def chat_with_agent(req: ChatRequest, user: dict = Depends(get_current_user)):
        from team_agent.orchestrator.session import SessionManager

        sm = _session_managers.get(user["id"])
        if not sm:
            project_config = config or ProjectConfig()
            sm = SessionManager(project_config)
            _session_managers[user["id"]] = sm

        session_id = req.session_id
        if not session_id:
            session = await sm.create_session(user["id"], req.message)
            session_id = session.id

        result = await sm.chat_with_agent(session_id, req.agent_name, req.message)
        return {"session_id": session_id, "agent": req.agent_name, "result": result}

    @app.post("/api/roundtable")
    async def roundtable(req: RoundtableRequest, user: dict = Depends(get_current_user)):
        from team_agent.orchestrator.session import SessionManager

        sm = _session_managers.get(user["id"])
        if not sm:
            project_config = config or ProjectConfig()
            sm = SessionManager(project_config)
            _session_managers[user["id"]] = sm

        session = await sm.create_session(user["id"], req.message)
        results = await sm.roundtable(session.id, req.agent_names, req.message)
        return {"session_id": session.id, "results": results}

    # === Team Routes ===

    @app.post("/api/teams")
    async def create_team(req: TeamCreateRequest, user: dict = Depends(get_current_user)):
        auth = get_auth_manager()
        team = auth.create_team(req.name, user["id"])
        return {"team_id": team.id, "name": team.name}

    @app.get("/api/teams")
    async def list_teams(user: dict = Depends(get_current_user)):
        auth = get_auth_manager()
        teams = auth.get_user_teams(user["id"])
        return {"teams": teams}

    @app.post("/api/teams/{team_id}/members")
    async def add_team_member(team_id: str, req: TeamMemberRequest, user: dict = Depends(get_current_user)):
        auth = get_auth_manager()
        auth.add_team_member(team_id, req.user_id, req.role)
        return {"added": True}

    # === Monitor Routes ===

    @app.get("/api/monitor")
    async def get_monitor_status(user: dict = Depends(get_current_user)):
        sm = _session_managers.get(user["id"])
        if not sm:
            return {"agents": {}, "total_traces": 0, "total_tokens": 0}
        return sm.monitor.get_status()

    @app.get("/api/monitor/usage")
    async def get_usage(user: dict = Depends(get_current_user)):
        sm = _session_managers.get(user["id"])
        if not sm:
            return {}
        return sm.monitor.get_usage_summary()

    # === WebSocket ===

    @app.websocket("/ws/{user_id}")
    async def websocket_endpoint(websocket: WebSocket, user_id: str):
        await websocket.accept()
        try:
            while True:
                data = await websocket.receive_text()
                # 简单的 WebSocket 消息处理
                import json
                try:
                    msg = json.loads(data)
                    msg_type = msg.get("type", "")
                    if msg_type == "ping":
                        await websocket.send_json({"type": "pong"})
                    elif msg_type == "status":
                        sm = _session_managers.get(user_id)
                        if sm:
                            await websocket.send_json(sm.monitor.get_status())
                except json.JSONDecodeError:
                    await websocket.send_json({"type": "error", "message": "Invalid JSON"})
        except WebSocketDisconnect:
            logger.info(f"WebSocket disconnected: {user_id}")

    return app
