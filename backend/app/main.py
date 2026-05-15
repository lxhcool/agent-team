"""Team Agent Backend - Main Application"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import init_db
from app.llm.router import llm_router
from app.services.event_bus import event_bus
from app.api import (
    planning,
    settings as settings_api,
    health,
    flows,
    messages,
    agents,
    artifacts,
    usage,
    security,
    skills,
    observability,
    tools,
    auth,
    workspaces,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown."""
    await init_db()
    # Ensure artifact directories exist
    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    # Pre-load LLM providers and init built-in agents
    from app.core.database import async_session
    async with async_session() as db:
        await llm_router.load_providers(db)
        # Auto-initialize built-in skills
        from app.api.skills import init_builtin_skills
        await init_builtin_skills(db)
        # Auto-initialize built-in agent templates
        from app.api.agents import init_builtin_agents
        await init_builtin_agents(db)
    # Pre-load skill registry
    from app.services.skill_registry import skill_registry
    await skill_registry.load_skills()
    # Register built-in agents in heartbeat
    from app.services.heartbeat import heartbeat_service
    for agent_name in [
        "product-structure-reviewer",
        "ux-clarity-reviewer",
        "technical-feasibility-reviewer",
        "business-rule-reviewer",
        "interaction-state-reviewer",
        "edge-case-reviewer",
        "architecture-reviewer",
        "data-api-reviewer",
        "engineering-risk-reviewer",
    ]:
        try:
            await heartbeat_service.register(agent_name, agent_type="server")
        except Exception as exc:
            logging.warning("heartbeat bootstrap skipped: agent=%s reason=%s", agent_name, exc)
    # Setup secure logging (SC-011: log desensitization)
    from app.services.security import setup_secure_logging
    setup_secure_logging()
    # Pre-load runtime security settings
    from app.services.security import runtime_security
    await runtime_security.load_settings()
    # A-006: Start agent liveness detection background task
    from app.services.liveness import liveness_detector
    await liveness_detector.start()
    # Recover interrupted planning jobs before the UI subscribes to session state.
    # P2-SS-010: Start session timeout monitor
    from app.services.execution import PlanningSessionRecoveryService, session_timeout_monitor
    await PlanningSessionRecoveryService.recover_stale_planning_sessions()
    await session_timeout_monitor.start()
    logging.info("Team Agent Backend started successfully")
    yield  # App is running
    # Shutdown: stop background tasks
    await liveness_detector.stop()
    await session_timeout_monitor.stop()


app = FastAPI(
    title="Team Agent API",
    description="多 Agent 协作系统后端 API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api", tags=["auth"])
app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(flows.router, prefix="/api", tags=["flows"])
app.include_router(workspaces.router, prefix="/api", tags=["workspaces"])
app.include_router(planning.router, prefix="/api", tags=["planning"])
app.include_router(messages.router, prefix="/api", tags=["messages"])
app.include_router(agents.router, prefix="/api/settings", tags=["agents"])
app.include_router(agents.router_alias, prefix="/api", tags=["agent-templates"])
app.include_router(settings_api.router, prefix="/api/settings", tags=["settings"])
app.include_router(artifacts.router, prefix="/api", tags=["artifacts"])
app.include_router(usage.router, prefix="/api", tags=["usage"])
app.include_router(security.router, prefix="/api/settings", tags=["security"])
app.include_router(skills.router, prefix="/api/settings", tags=["skills"])
app.include_router(observability.router, prefix="/api", tags=["observability"])
app.include_router(tools.router, prefix="/api", tags=["tools"])
