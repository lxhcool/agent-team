"""Agent Template API - CRUD for custom agents with system prompts, roles, and skills."""

import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, async_session
from app.models.models import AgentTemplate
from app.services.flows.expert_assets import builtin_agent_templates

router = APIRouter()


# ===== Schemas =====

class SkillRef(BaseModel):
    name: str
    display_name: Optional[str] = None


class CapabilityRef(BaseModel):
    name: str
    description: Optional[str] = None


class CreateAgentRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_-]*$")
    display_name: str = Field(..., min_length=1, max_length=200)
    role: str = Field(..., min_length=1, max_length=50)
    goal: Optional[str] = None
    system_prompt: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    skills: List[SkillRef] = Field(default_factory=list)
    capabilities: List[CapabilityRef] = Field(default_factory=list)
    allowed_tools: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    participation_modes: List[str] = Field(default=["planning"])
    risk_level: str = Field(default="low")


class UpdateAgentRequest(BaseModel):
    display_name: Optional[str] = None
    role: Optional[str] = None
    goal: Optional[str] = None
    system_prompt: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    skills: Optional[List[SkillRef]] = None
    capabilities: Optional[List[CapabilityRef]] = None
    allowed_tools: Optional[List[str]] = None
    constraints: Optional[List[str]] = None
    participation_modes: Optional[List[str]] = None
    risk_level: Optional[str] = None


class AgentResponse(BaseModel):
    id: str
    name: str
    display_name: str
    role: str
    goal: Optional[str] = None
    system_prompt: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    skills: List[dict] = []
    capabilities: List[dict] = []
    allowed_tools: List[str] = []
    constraints: List[str] = []
    participation_modes: List[str] = []
    risk_level: str = "low"
    is_builtin: bool = False
    version: str = "1.0.0"


BUILTIN_AGENTS = builtin_agent_templates()

RETIRED_BUILTIN_AGENT_NAMES = {
    "orchestrator",
    "requirements-analyst",
    "product-designer",
    "ui-ux-designer",
    "technical-architect",
    "spec-writer",
    "qa-reviewer",
    "release-operator",
    "debater_pro",
    "debater_con",
    "creative_ideator",
    "pragmatic_engineer",
    "user_advocate",
    "storyteller",
    "dialogue_writer",
    "plot_twister",
    "interviewer",
    "interviewee",
    "philosopher",
    "futurist",
    "scientist_agent",
    "ops_engineer",
    "backend_dev",
}

# ===== Endpoints =====

@router.get("/agents", response_model=List[AgentResponse])
async def list_agents(db: AsyncSession = Depends(get_db)):
    """List all agent templates, including built-in ones."""
    result = await db.execute(select(AgentTemplate).order_by(AgentTemplate.name))
    agents = result.scalars().all()

    return [_agent_to_response(a) for a in agents]


@router.get("/agents/{agent_name}", response_model=AgentResponse)
async def get_agent(agent_name: str, db: AsyncSession = Depends(get_db)):
    """Get a specific agent template by name."""
    result = await db.execute(
        select(AgentTemplate).where(AgentTemplate.name == agent_name)
    )
    agent = result.scalars().first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return _agent_to_response(agent)


@router.post("/agents", response_model=AgentResponse)
async def create_agent(
    req: CreateAgentRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a custom agent template."""
    # Check uniqueness
    existing = await db.execute(
        select(AgentTemplate).where(AgentTemplate.name == req.name)
    )
    if existing.scalars().first():
        raise HTTPException(status_code=400, detail=f"Agent '{req.name}' already exists")

    agent = AgentTemplate(
        name=req.name,
        display_name=req.display_name,
        role=req.role,
        goal=req.goal,
        system_prompt=req.system_prompt,
        model=req.model,
        provider=req.provider,
        skills_json=json.dumps([s.dict() for s in req.skills]) if req.skills else None,
        capabilities_json=json.dumps([c.dict() for c in req.capabilities]) if req.capabilities else None,
        allowed_tools_json=json.dumps(req.allowed_tools) if req.allowed_tools else None,
        constraints_json=json.dumps(req.constraints) if req.constraints else None,
        participation_modes_json=json.dumps(req.participation_modes) if req.participation_modes else None,
        risk_level=req.risk_level,
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    return _agent_to_response(agent)


@router.put("/agents/{agent_name}", response_model=AgentResponse)
async def update_agent(
    agent_name: str,
    req: UpdateAgentRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update an agent template."""
    result = await db.execute(
        select(AgentTemplate).where(AgentTemplate.name == agent_name)
    )
    agent = result.scalars().first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    if req.display_name is not None:
        agent.display_name = req.display_name
    if req.role is not None:
        agent.role = req.role
    if req.goal is not None:
        agent.goal = req.goal
    if req.system_prompt is not None:
        agent.system_prompt = req.system_prompt
    if req.model is not None:
        agent.model = req.model
    if req.provider is not None:
        agent.provider = req.provider
    if req.skills is not None:
        agent.skills_json = json.dumps([s.dict() for s in req.skills])
    if req.capabilities is not None:
        agent.capabilities_json = json.dumps([c.dict() for c in req.capabilities])
    if req.allowed_tools is not None:
        agent.allowed_tools_json = json.dumps(req.allowed_tools)
    if req.constraints is not None:
        agent.constraints_json = json.dumps(req.constraints)
    if req.participation_modes is not None:
        agent.participation_modes_json = json.dumps(req.participation_modes)
    if req.risk_level is not None:
        agent.risk_level = req.risk_level

    await db.commit()
    await db.refresh(agent)
    return _agent_to_response(agent)


@router.delete("/agents/{agent_name}")
async def delete_agent(agent_name: str, db: AsyncSession = Depends(get_db)):
    """Delete a custom agent template (built-in agents cannot be deleted)."""
    result = await db.execute(
        select(AgentTemplate).where(AgentTemplate.name == agent_name)
    )
    agent = result.scalars().first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Check if built-in
    builtin_names = {a["name"] for a in BUILTIN_AGENTS}
    if agent.name in builtin_names:
        raise HTTPException(status_code=400, detail="Cannot delete built-in agents")

    await db.delete(agent)
    await db.commit()
    return {"status": "deleted"}


@router.post("/agents/init-builtins")
async def init_builtin_agents(db: AsyncSession = Depends(get_db)):
    """Initialize built-in agent templates. Safe to call multiple times."""
    created = []
    updated = []
    retired = []
    for retired_name in RETIRED_BUILTIN_AGENT_NAMES:
        existing = await db.execute(
            select(AgentTemplate).where(AgentTemplate.name == retired_name)
        )
        retired_agent = existing.scalars().first()
        if retired_agent:
            await db.delete(retired_agent)
            retired.append(retired_name)

    for builtin in BUILTIN_AGENTS:
        existing = await db.execute(
            select(AgentTemplate).where(AgentTemplate.name == builtin["name"])
        )
        existing_agent = existing.scalars().first()
        if existing_agent:
            existing_agent.display_name = builtin["display_name"]
            existing_agent.role = builtin["role"]
            existing_agent.goal = builtin.get("goal")
            existing_agent.system_prompt = builtin.get("system_prompt")
            existing_agent.model = builtin.get("model")
            existing_agent.provider = builtin.get("provider")
            existing_agent.skills_json = json.dumps(builtin.get("skills", []))
            existing_agent.capabilities_json = json.dumps(builtin.get("capabilities", []))
            existing_agent.allowed_tools_json = json.dumps(builtin.get("allowed_tools", []))
            existing_agent.constraints_json = json.dumps(builtin.get("constraints", []))
            existing_agent.participation_modes_json = json.dumps(builtin.get("participation_modes", []))
            existing_agent.risk_level = builtin.get("risk_level", "low")
            updated.append(builtin["name"])
        else:
            agent = AgentTemplate(
                name=builtin["name"],
                display_name=builtin["display_name"],
                role=builtin["role"],
                goal=builtin.get("goal"),
                system_prompt=builtin.get("system_prompt"),
                model=builtin.get("model"),
                provider=builtin.get("provider"),
                skills_json=json.dumps(builtin.get("skills", [])),
                capabilities_json=json.dumps(builtin.get("capabilities", [])),
                allowed_tools_json=json.dumps(builtin.get("allowed_tools", [])),
                constraints_json=json.dumps(builtin.get("constraints", [])),
                participation_modes_json=json.dumps(builtin.get("participation_modes", [])),
                risk_level=builtin.get("risk_level", "low"),
            )
            db.add(agent)
            created.append(builtin["name"])

    await db.commit()
    return {"status": "initialized", "created": created, "updated": updated, "retired": retired}


# ===== Alias: /api/agent-templates (per requirements design doc) =====
# Provides the /api/agent-templates path as required by the design doc,
# while /api/settings/agents remains the primary path.

router_alias = APIRouter()


@router_alias.get("/agent-templates", response_model=List[AgentResponse])
async def _list_agent_templates(db: AsyncSession = Depends(get_db)):
    """List all agent templates (alias for /api/settings/agents per requirements)."""
    return await list_agents(db=db)


@router_alias.post("/agent-templates", response_model=AgentResponse)
async def _create_agent_template(
    req: CreateAgentRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a custom agent template (alias for /api/settings/agents per requirements)."""
    return await create_agent(req=req, db=db)


def _agent_to_response(agent: AgentTemplate) -> AgentResponse:
    """Convert AgentTemplate ORM to AgentResponse, adding computed fields."""
    builtin_names = {a["name"] for a in BUILTIN_AGENTS}

    # Get system_prompt: prefer stored value, fallback to builtin definition
    system_prompt = agent.system_prompt
    if not system_prompt:
        for b in BUILTIN_AGENTS:
            if b["name"] == agent.name:
                system_prompt = b.get("system_prompt")
                break

    return AgentResponse(
        id=agent.id,
        name=agent.name,
        display_name=agent.display_name,
        role=agent.role,
        goal=agent.goal,
        system_prompt=system_prompt,
        model=agent.model,
        provider=agent.provider,
        skills=json.loads(agent.skills_json) if agent.skills_json else [],
        capabilities=json.loads(agent.capabilities_json) if agent.capabilities_json else [],
        allowed_tools=json.loads(agent.allowed_tools_json) if agent.allowed_tools_json else [],
        constraints=json.loads(agent.constraints_json) if agent.constraints_json else [],
        participation_modes=json.loads(agent.participation_modes_json) if agent.participation_modes_json else [],
        risk_level=agent.risk_level,
        is_builtin=agent.name in builtin_names,
        version=agent.version,
    )
