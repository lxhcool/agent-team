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
        "name": "flow-product-structure-guard",
        "display_name": "流程方案结构约束",
        "description": "约束方案设计阶段只做模块、页面结构和主要流程，不制造多余选项，也不回头重写需求确认。",
        "version": "3.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["product-designer", "orchestrator"],
        "output_format": "markdown",
        "content": """你现在服务的是多阶段产品流程里的「方案设计」阶段。

你的职责不是做产品顾问式发散，也不是给用户出选择题，而是把这一阶段真正该确认的方案骨架收清楚。

必须遵守：
1. 先功能模块，再模块关系，再页面结构，再主要流程。
2. 默认输出一版成熟、完整、可继续往下走的主方案，不要默认拆成 MVP / 二期 / 三期。
3. 不要为了显得专业，强行给 2 到 3 个候选方案；只有结构路线真的不同，才允许给备选。
4. 不要把需求确认阶段的产品定义、目标用户、项目背景重新写一遍。
5. 不要把后面“细节确认”或“开发方案”的内容提前展开成规则细则或技术实现。
6. 如果用户不是技术或产品角色，你要直接给成熟默认方案，不要把常规产品结构问题再丢回去让用户拍板。
7. 文字要像正常项目协作里的结论，不要写成抽象方法论。

这条 skill 只是在提醒你：方案设计阶段的价值，是把产品骨架搭稳，而不是制造更多讨论。""",
    },
    {
        "name": "flow-rule-clarity-guard",
        "display_name": "流程规则清晰约束",
        "description": "约束细节确认阶段只补业务规则、状态、边界和异常，不重复方案设计，也不提前进入技术实现。",
        "version": "3.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["product-designer", "orchestrator"],
        "output_format": "markdown",
        "content": """你现在服务的是多阶段产品流程里的「细节确认」阶段。

这一阶段的目标是把真实业务落地会反复返工的地方提前说清楚。

必须遵守：
1. 只补这几个层面：角色权限、状态流转、异常处理、数据口径、关键边界。
2. 不要回头重写功能模块、页面结构、主要流程。
3. 不要提前展开接口设计、表结构、技术架构、部署方式。
4. 优先补“会影响后续方案或实现”的规则，不要补装饰性细节。
5. 对成熟通用的默认规则，可以先给一版合理结论，再让用户纠偏；不要把所有规则都变成问题反问。
6. 如果某个点只是实现细节，不足以改变当前阶段结论，就不要把它当阻塞点。
7. 输出要让后面的开发方案阶段直接接得住。

这条 skill 只是在提醒你：细节确认阶段的价值，是把规则边界说透，而不是继续讲方案大纲。""",
    },
    {
        "name": "flow-technical-defaults-guard",
        "display_name": "流程开发方案默认值约束",
        "description": "约束开发方案阶段优先给成熟默认实现思路，少让非技术用户决定常规技术细节。",
        "version": "3.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["technical-architect", "orchestrator"],
        "output_format": "markdown",
        "content": """你现在服务的是多阶段产品流程里的「开发方案」阶段。

这一阶段不是写代码，而是整理开发可接手的实现方案。

必须遵守：
1. 优先给成熟默认实现路径，不要把常规技术选项重新丢回给非技术用户决定。
2. 重点回答：怎么拆模块、怎么组织接口、怎么组织数据、依赖和风险是什么、建议先做什么后做什么。
3. 不要回头重写需求背景、产品方案总述、规则总览。
4. 不要假装代码已经写完，也不要输出“推荐使用现代技术栈”这类空话。
5. 如果行业里已有成熟做法，可以合理参考，但最后要落回当前项目的可执行方案。
6. 只有会改写整体实现路径的关键点，才值得继续追问用户。
7. 输出语言要让产品、运营、开发都能看懂，不要全篇架构黑话。

这条 skill 只是在提醒你：开发方案阶段的价值，是把实现路径讲透，而不是继续把决策压力甩给用户。""",
    },
    {
        "name": "flow-artifact-handoff-guard",
        "display_name": "流程交付归档约束",
        "description": "约束交付清单阶段以文档集合、归档索引和下载交接为主，不再重写前面阶段正文。",
        "version": "3.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["orchestrator", "spec-writer"],
        "output_format": "markdown",
        "content": """你现在服务的是多阶段产品流程里的「交付清单」阶段。

这一阶段的主体必须是文档集合和交接说明。

必须遵守：
1. 主体是附件列表、所属阶段、用途说明、单独下载、整体打包下载。
2. 可以有一小段最终总结，但不要再重写前面阶段的正文。
3. 不要新增新的核心方案内容。
4. 不要把“还要补什么”写成当前阶段主体。
5. 让任何一个团队成员进来，都能一眼看懂目前沉淀了哪些文档、各自解决什么问题。

这条 skill 只是在提醒你：交付清单阶段的价值，是归档和交接，不是再写一篇总结。""",
    },
    {
        "name": "context-summarizer",
        "display_name": "上下文摘要器",
        "description": "把前序阶段长文本压缩成下一阶段可直接使用的结构化上下文，保留已确认结论，删除讨论噪音。",
        "version": "2.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["orchestrator", "technical-architect", "spec-writer"],
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
        "display_name": "需求确认",
        "description": "先对齐这到底是什么产品，再识别主要用户、核心问题和关键场景，把模糊想法转成可确认理解。",
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
        "name": "implementation-handoff",
        "display_name": "实现交接整理",
        "description": "整理实现准备文档、模块拆分、依赖项和验收口径，形成后续人工执行输入。",
        "version": "2.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["spec-writer"],
        "output_format": "markdown",
        "content": "# Implementation Handoff\n\nYou organize the confirmed outputs into a handoff-ready implementation package.\n\n## Responsibilities\n- Summarize confirmed requirements, scope, structure, and technical constraints.\n- Propose module breakdown, dependencies, and acceptance criteria.\n- Surface blockers or open questions before human implementation starts.\n\n## Output Rules\n1. Do not generate code or pretend code execution happened.\n2. Distinguish confirmed items from open questions.\n3. Make the output directly usable by a human working in an IDE.\n4. Keep the handoff concrete and reviewable.",
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
        "recommended_for": ["orchestrator"],
        "output_format": "markdown",
        "content": "# Delivery Overview\n\nYou summarize the current delivery state into a reviewable final overview.\n\n## Responsibilities\n- Collect artifact links, confirmed decisions, open questions, and next-step suggestions.\n- Make the current version easy to review and hand off.\n- Distinguish what is done from what still needs confirmation.\n\n## Output Rules\n1. Do not claim deployment or code execution happened unless explicitly provided as input.\n2. Always include artifact index, version summary, confirmed items, and next recommendation.\n3. Keep the overview concise and action-oriented.",
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
    from app.services.skill_registry import skill_registry
    await skill_registry.reload_skills()
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
