"""Skills CRUD API - manage custom skills for agent templates."""

import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.models import Skill
from app.services.flows.expert_assets import FLOW_METHODS

router = APIRouter()


# ===== Schemas =====

class CreateSkillRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_-]*$")
    display_name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    version: str = Field(default="1.0.0")
    source_type: str = Field(default="builtin")
    source_ref: Optional[str] = None
    author: str = Field(default="team-agent")
    tools: List[str] = Field(default_factory=list)
    recommended_for: List[str] = Field(default_factory=list)
    output_format: str = Field(default="markdown")
    content: Optional[str] = None


class UpdateSkillRequest(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    version: Optional[str] = None
    source_type: Optional[str] = None
    source_ref: Optional[str] = None
    author: Optional[str] = None
    tools: Optional[List[str]] = None
    recommended_for: Optional[List[str]] = None
    output_format: Optional[str] = None
    content: Optional[str] = None


class SkillResponse(BaseModel):
    id: str
    name: str
    display_name: str
    description: Optional[str] = None
    version: str
    source_type: str
    source_ref: Optional[str] = None
    author: str
    tools: List[str] = []
    recommended_for: List[str] = []
    output_format: str
    content: Optional[str] = None
    created_at: Optional[str] = None


# ===== Endpoints =====

@router.get("/skills", response_model=List[SkillResponse])
async def list_skills(db: AsyncSession = Depends(get_db)):
    """List all skills."""
    result = await db.execute(select(Skill).order_by(Skill.name))
    skills = result.scalars().all()
    return [_skill_to_response(s) for s in skills]


@router.get("/skills/{skill_id}", response_model=SkillResponse)
async def get_skill(
    skill_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get a skill by ID."""
    skill = await db.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")
    return _skill_to_response(skill)


@router.post("/skills", response_model=SkillResponse)
async def create_skill(
    req: CreateSkillRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a new skill."""
    # Check uniqueness
    existing = await db.execute(
        select(Skill).where(Skill.name == req.name)
    )
    if existing.scalars().first():
        raise HTTPException(status_code=400, detail=f"Skill '{req.name}' already exists")

    skill = Skill(
        name=req.name,
        display_name=req.display_name,
        description=req.description,
        version=req.version,
        source_type=req.source_type,
        source_ref=req.source_ref,
        author=req.author,
        tools_json=json.dumps(req.tools) if req.tools else None,
        recommended_for_json=json.dumps(req.recommended_for) if req.recommended_for else None,
        output_format=req.output_format,
        content=req.content,
    )
    db.add(skill)
    await db.commit()
    await db.refresh(skill)
    return _skill_to_response(skill)


@router.put("/skills/{skill_id}", response_model=SkillResponse)
async def update_skill(
    skill_id: str,
    req: UpdateSkillRequest,
    db: AsyncSession = Depends(get_db),
):
    """Update a skill."""
    skill = await db.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    if req.display_name is not None:
        skill.display_name = req.display_name
    if req.description is not None:
        skill.description = req.description
    if req.version is not None:
        skill.version = req.version
    if req.source_type is not None:
        skill.source_type = req.source_type
    if req.source_ref is not None:
        skill.source_ref = req.source_ref
    if req.author is not None:
        skill.author = req.author
    if req.tools is not None:
        skill.tools_json = json.dumps(req.tools)
    if req.recommended_for is not None:
        skill.recommended_for_json = json.dumps(req.recommended_for)
    if req.output_format is not None:
        skill.output_format = req.output_format
    if req.content is not None:
        skill.content = req.content

    await db.commit()
    await db.refresh(skill)
    return _skill_to_response(skill)


class ImportSkillRequest(BaseModel):
    source_url: str = Field(..., min_length=1)
    name: Optional[str] = None  # Override name
    auto_enable: bool = Field(default=False)


class ImportPreviewResponse(BaseModel):
    name: str
    display_name: str
    description: Optional[str] = None
    version: str
    tools: List[str] = []
    source_url: str
    warnings: List[str] = []


@router.post("/skills/import", response_model=ImportPreviewResponse)
async def import_skill_preview(
    req: ImportSkillRequest,
    db: AsyncSession = Depends(get_db),
):
    """Preview a skill before importing. The skill is NOT saved until explicitly confirmed via POST /skills."""
    import httpx

    # Try to fetch the skill definition
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(req.source_url)
            if resp.status_code != 200:
                raise HTTPException(status_code=400, detail=f"Failed to fetch skill from URL: HTTP {resp.status_code}")
            skill_data = resp.json()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch skill: {str(e)}")

    # Validate skill structure
    required_fields = ["name", "display_name"]
    warnings = []
    for f in required_fields:
        if f not in skill_data:
            warnings.append(f"Missing field: {f}")

    name = req.name or skill_data.get("name", "unnamed_import")

    # Check if skill already exists
    existing = await db.execute(select(Skill).where(Skill.name == name))
    if existing.scalars().first():
        warnings.append(f"Skill '{name}' already exists - will be overwritten on confirm")

    return ImportPreviewResponse(
        name=name,
        display_name=skill_data.get("display_name", name),
        description=skill_data.get("description"),
        version=skill_data.get("version", "1.0.0"),
        tools=skill_data.get("tools", []),
        source_url=req.source_url,
        warnings=warnings,
    )


@router.post("/skills/import/confirm", response_model=SkillResponse)
async def import_skill_confirm(
    req: ImportSkillRequest,
    db: AsyncSession = Depends(get_db),
):
    """Confirm importing a skill after preview."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(req.source_url)
            if resp.status_code != 200:
                raise HTTPException(status_code=400, detail="Failed to fetch skill")
            skill_data = resp.json()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch skill: {str(e)}")

    name = req.name or skill_data.get("name", "unnamed_import")

    # Check if exists - update or create
    existing = await db.execute(select(Skill).where(Skill.name == name))
    existing_skill = existing.scalars().first()

    if existing_skill:
        # Update
        existing_skill.display_name = skill_data.get("display_name", existing_skill.display_name)
        existing_skill.description = skill_data.get("description", existing_skill.description)
        existing_skill.version = skill_data.get("version", existing_skill.version)
        existing_skill.source_type = "imported"
        existing_skill.source_ref = req.source_url
        existing_skill.tools_json = json.dumps(skill_data.get("tools", []))
        existing_skill.recommended_for_json = json.dumps(skill_data.get("recommended_for", []))
        existing_skill.content = json.dumps(skill_data, ensure_ascii=False)
        await db.commit()
        await db.refresh(existing_skill)
        return _skill_to_response(existing_skill)
    else:
        # Create
        skill = Skill(
            name=name,
            display_name=skill_data.get("display_name", name),
            description=skill_data.get("description"),
            version=skill_data.get("version", "1.0.0"),
            source_type="imported",
            source_ref=req.source_url,
            author=skill_data.get("author", "imported"),
            tools_json=json.dumps(skill_data.get("tools", [])),
            recommended_for_json=json.dumps(skill_data.get("recommended_for", [])),
            output_format=skill_data.get("output_format", "markdown"),
            content=json.dumps(skill_data, ensure_ascii=False),
        )
        db.add(skill)
        await db.commit()
        await db.refresh(skill)
        return _skill_to_response(skill)


@router.delete("/skills/{skill_id}")
async def delete_skill(
    skill_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete a skill."""
    skill = await db.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail="Skill not found")

    await db.delete(skill)
    await db.commit()
    return {"status": "deleted"}


# ===== Helpers =====

BUILTIN_SKILLS = FLOW_METHODS

RETIRED_BUILTIN_SKILL_NAMES = {
    "context-summarizer",
    "decision-presenter",
    "user-facing-writer",
    "requirements-discovery",
    "mvp-scope-control",
    "product-flow-design",
    "ui-direction-design",
    "prototype-structure-builder",
    "technical-solution-design",
    "implementation-handoff",
    "qa-acceptance-check",
    "release-safety-check",
}

@router.post("/skills/init-builtins")
async def init_builtin_skills(db: AsyncSession = Depends(get_db)):
    """Initialize built-in skills. Safe to call multiple times."""
    created = []
    updated = []
    retired = []
    for retired_name in RETIRED_BUILTIN_SKILL_NAMES:
        existing = await db.execute(
            select(Skill).where(Skill.name == retired_name, Skill.source_type == "builtin")
        )
        retired_skill = existing.scalars().first()
        if retired_skill:
            await db.delete(retired_skill)
            retired.append(retired_name)

    for builtin in BUILTIN_SKILLS:
        existing = await db.execute(
            select(Skill).where(Skill.name == builtin["name"])
        )
        existing_skill = existing.scalars().first()
        if existing_skill:
            # Update existing builtin skill
            existing_skill.display_name = builtin["display_name"]
            existing_skill.description = builtin["description"]
            existing_skill.version = builtin["version"]
            existing_skill.content = builtin["content"]
            existing_skill.tools_json = json.dumps(builtin.get("tools", []))
            existing_skill.recommended_for_json = json.dumps(builtin.get("recommended_for", []))
            updated.append(builtin["name"])
        else:
            skill = Skill(
                name=builtin["name"],
                display_name=builtin["display_name"],
                description=builtin["description"],
                version=builtin["version"],
                source_type=builtin["source_type"],
                author=builtin["author"],
                tools_json=json.dumps(builtin.get("tools", [])),
                recommended_for_json=json.dumps(builtin.get("recommended_for", [])),
                output_format=builtin.get("output_format", "markdown"),
                content=builtin["content"],
            )
            db.add(skill)
            created.append(builtin["name"])

    await db.commit()
    from app.services.skill_registry import skill_registry
    await skill_registry.reload_skills()
    return {"status": "initialized", "created": created, "updated": updated, "retired": retired}


def _skill_to_response(skill: Skill) -> SkillResponse:
    return SkillResponse(
        id=skill.id,
        name=skill.name,
        display_name=skill.display_name,
        description=skill.description,
        version=skill.version,
        source_type=skill.source_type,
        source_ref=skill.source_ref,
        author=skill.author,
        tools=json.loads(skill.tools_json) if skill.tools_json else [],
        recommended_for=json.loads(skill.recommended_for_json) if skill.recommended_for_json else [],
        output_format=skill.output_format,
        content=skill.content,
        created_at=skill.created_at.isoformat() if skill.created_at else None,
    )
