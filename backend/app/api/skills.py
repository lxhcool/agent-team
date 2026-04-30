"""Skills CRUD API - manage custom skills for agent templates."""

import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.models import Skill

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

# Built-in skills for the staged workspace flow and global agent presets
BUILTIN_SKILLS = [
    {
        "name": "context-summarizer",
        "display_name": "上下文摘要器",
        "description": "把前序阶段长文本压缩成下一阶段可直接使用的结构化上下文，保留已确认结论，删除讨论噪音。",
        "version": "2.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["orchestrator", "technical-architect", "implementation-engineer", "release-operator"],
        "output_format": "markdown",
        "content": "# Context Summarizer\n\nYou compress prior-stage outputs into the minimum structured context needed by the next stage.\n\n## Responsibilities\n- Preserve only confirmed conclusions and stage decisions.\n- Remove brainstorming noise, repetition, and speculative discussion.\n- Surface unresolved questions and blockers separately.\n- Produce handoff-ready context that the next agent can consume directly.\n\n## Output Rules\n1. Split content into: Confirmed, Assumptions, Open Questions, Next Inputs.\n2. Prefer bullet summaries over long prose.\n3. Never invent missing facts.\n4. Keep the summary short enough for the next stage to operate without rereading the whole history.",
    },
    {
        "name": "decision-presenter",
        "display_name": "决策呈现器",
        "description": "把复杂分析结果改写成用户可理解、可比较、可确认的决策选项，强制输出推荐与候选差异。",
        "version": "2.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["orchestrator", "product-designer", "qa-reviewer"],
        "output_format": "markdown",
        "content": "# Decision Presenter\n\nYou turn internal analysis into user-facing choices that are concrete, comparable, and confirmable.\n\n## Responsibilities\n- Convert complex tradeoffs into 2-3 clear options.\n- Always identify one recommended option.\n- Explain the real difference between options, not just rename them.\n- Keep wording understandable for non-technical users.\n\n## Output Rules\n1. Every option must include: title, who it fits, what changes, what the tradeoff is.\n2. The recommendation must explain why it is the default.\n3. Avoid abstract labels without substance.\n4. If an option exists, it must be meaningfully different from the others.",
    },
    {
        "name": "user-facing-writer",
        "display_name": "用户表达器",
        "description": "把内部专业分析改写成非技术用户能理解的中文说明，优先使用具体结果，不使用术语堆砌。",
        "version": "2.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["orchestrator", "requirements-analyst", "product-designer", "ui-ux-designer"],
        "output_format": "markdown",
        "content": "# User Facing Writer\n\nYou rewrite expert analysis into concise Chinese that a non-technical user can understand and approve.\n\n## Responsibilities\n- Replace jargon with concrete examples and expected outcomes.\n- Write in short, direct statements.\n- Make every paragraph answer a user decision or confirmation question.\n\n## Output Rules\n1. Prioritize clarity over completeness.\n2. Use concrete nouns and actions instead of abstract concepts.\n3. Avoid filler such as “can consider”, “it may be helpful”, or generic encouragement.\n4. Every output should help the user decide what to confirm next.",
    },
    {
        "name": "requirements-discovery",
        "display_name": "需求澄清",
        "description": "识别目标用户、核心问题、关键场景和使用目标，把模糊愿望转成可确认需求。",
        "version": "2.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["requirements-analyst"],
        "output_format": "markdown",
        "content": "# Requirements Discovery\n\nYou identify the target users, core problem, main scenarios, trigger conditions, and success outcomes behind a fuzzy idea.\n\n## Responsibilities\n- State who the primary user is.\n- State the single most important problem to solve first.\n- Distinguish the main use case from edge cases.\n- Convert vague requests into clear stage-confirmable statements.\n\n## Output Rules\n1. Focus on one primary user group unless there is strong evidence otherwise.\n2. Emphasize the top pain point and the shortest meaningful solution loop.\n3. Explicitly list assumptions when the user input is incomplete.",
    },
    {
        "name": "mvp-scope-control",
        "display_name": "MVP 范围控制",
        "description": "控制第一版边界，防止需求膨胀，把愿望列表收敛成真正的 MVP。",
        "version": "2.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["requirements-analyst"],
        "output_format": "markdown",
        "content": "# MVP Scope Control\n\nYou prevent early-stage scope creep and force a sharp MVP boundary.\n\n## Responsibilities\n- Separate must-have from later-phase ideas.\n- Identify hidden complexity that should be deferred.\n- Keep the first version buildable, demoable, and testable.\n\n## Output Rules\n1. Always produce: Must Do, Can Delay, Explicitly Not Doing Now.\n2. Prefer one strong loop over many weak features.\n3. Call out when a request would slow validation more than it increases value.",
    },
    {
        "name": "product-flow-design",
        "display_name": "产品流程设计",
        "description": "设计页面结构、主流程、异常流和优先级，生成多个可比较的产品路径。",
        "version": "2.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["product-designer"],
        "output_format": "markdown",
        "content": "# Product Flow Design\n\nYou design page structure, user flows, exception flows, and feature priority for an MVP product.\n\n## Responsibilities\n- Define core pages or views.\n- Design the primary user journey and key exception paths.\n- Produce multiple plausible product paths when helpful.\n- Set clear P0, P1, and P2 priorities.\n\n## Output Rules\n1. Every option must answer: what is the entry point, main action, result/status view, and settings/support area.\n2. The default option must support a real prototype later.\n3. Differences between product paths must be structural, not cosmetic.",
    },
    {
        "name": "ui-direction-design",
        "display_name": "界面方向设计",
        "description": "生成可理解的界面方向和页面气质，保证输出能作用于真实原型，而不是抽象风格词。",
        "version": "2.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["ui-ux-designer"],
        "output_format": "markdown",
        "content": "# UI Direction Design\n\nYou generate user-comprehensible UI directions that can materially change how the prototype looks and behaves.\n\n## Responsibilities\n- Provide 2-3 direction options with meaningful visual and interaction differences.\n- Explain density, hierarchy, CTA emphasis, and visual tone.\n- Describe the resulting interface in concrete page terms.\n\n## Output Rules\n1. Do not output design-jargon-only labels.\n2. Each direction must influence the later prototype and design output.\n3. Favor real interface descriptions: list, card, form, navigation, action button, status panel.",
    },
    {
        "name": "prototype-structure-builder",
        "display_name": "原型结构构建",
        "description": "把需求、产品方案、UI 方向组合成真实页面原型结构，优先产出可确认的界面布局。",
        "version": "2.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["ui-ux-designer"],
        "output_format": "markdown",
        "content": "# Prototype Structure Builder\n\nYou combine confirmed requirements, product structure, and UI direction into a prototype that looks like a product, not a specification page.\n\n## Responsibilities\n- Produce concrete page structure and component hierarchy.\n- Prioritize main actions, cards, lists, forms, and visible status feedback.\n- Keep the prototype confirmable by end users.\n\n## Output Rules\n1. Avoid dumping requirement text into the UI as page content.\n2. Every prototype needs a clear primary action.\n3. The output should look like a usable interface, not an explanatory document.",
    },
    {
        "name": "technical-solution-design",
        "display_name": "技术方案设计",
        "description": "设计技术方案、数据边界、模块划分、运行与预览方式，产出开发阶段可直接消费的输入。",
        "version": "2.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["technical-architect"],
        "output_format": "markdown",
        "content": "# Technical Solution Design\n\nYou produce a practical technical plan that the implementation stage can directly consume.\n\n## Responsibilities\n- Define stack, module boundaries, data structure, external dependencies, and runtime model.\n- Produce a default technical route plus optional alternatives when appropriate.\n- State risks, boundaries, and what is intentionally not included.\n\n## Output Rules\n1. Optimize for simple, stable, and buildable defaults.\n2. Always specify project structure, data isolation, preview strategy, and execution boundary.\n3. Avoid architecture theater or unnecessary complexity.",
    },
    {
        "name": "implementation-execution",
        "display_name": "实现执行",
        "description": "执行真实开发，记录文件修改、运行结果和预览产物，形成后续验收输入。",
        "version": "2.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["implementation-engineer"],
        "output_format": "markdown",
        "content": "# Implementation Execution\n\nYou are responsible for real implementation work and execution reporting.\n\n## Responsibilities\n- Modify code according to confirmed stage inputs.\n- Record changed files, commands, validation results, and preview artifacts.\n- Clearly report blockers when implementation cannot complete.\n\n## Output Rules\n1. Never pretend execution happened if it did not.\n2. Always return a concrete execution status.\n3. Prefer artifacts: preview URL, screenshots, diff summary, runtime result.\n4. If blocked, identify the exact step and likely fix path.",
    },
    {
        "name": "qa-acceptance-check",
        "display_name": "验收检查",
        "description": "基于真实原型、截图、预览或执行结果做验收，不基于计划文案做通过判断。",
        "version": "2.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["qa-reviewer"],
        "output_format": "markdown",
        "content": "# QA Acceptance Check\n\nYou judge whether the current result is acceptable based on real artifacts.\n\n## Responsibilities\n- Define pass/fail criteria for the current stage.\n- Inspect functionality, interface quality, mobile behavior, state feedback, and regression risk.\n- Produce a clear acceptance verdict.\n\n## Output Rules\n1. Base all conclusions on real prototypes, screenshots, previews, or execution artifacts.\n2. Do not approve from descriptive text alone.\n3. Always report: Pass items, Problems, Risk level, Next recommendation.",
    },
    {
        "name": "release-safety-check",
        "display_name": "发布安全检查",
        "description": "确保部署结果可验证、可回滚、可交付，整理访问地址、日志摘要和发布风险。",
        "version": "2.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["release-operator"],
        "output_format": "markdown",
        "content": "# Release Safety Check\n\nYou make release outcomes verifiable, reviewable, and recoverable.\n\n## Responsibilities\n- Check environment readiness, build/start commands, and deployment prerequisites.\n- Summarize access URLs, logs, and rollback guidance.\n- Distinguish success, partial success, and failure with evidence.\n\n## Output Rules\n1. Never report deployment success without a verifiable result.\n2. Always include address or concrete failure reason.\n3. Always provide rollback or recovery advice.",
    },
]


@router.post("/skills/init-builtins")
async def init_builtin_skills(db: AsyncSession = Depends(get_db)):
    """Initialize built-in skills. Safe to call multiple times."""
    created = []
    updated = []
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
    return {"status": "initialized", "created": created, "updated": updated}


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
