"""Agent Template API - CRUD for custom agents with system prompts, roles, and skills."""

import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, async_session
from app.models.models import AgentTemplate

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


# Built-in agent templates
BUILTIN_AGENTS = [
    {
        "name": "leader",
        "display_name": "Leader",
        "role": "coordinator",
        "goal": "协调 Agent 团队完成需求分析、技术方案生成和执行计划制定",
        "system_prompt": "你是一个资深的软件架构师和团队协调者。你需要：\n1. 分析用户需求的核心要点\n2. 协调团队成员各司其职\n3. 综合各方意见形成完整方案\n4. 确保方案可执行、无遗漏",
        "skills": [{"name": "prompt-enhancer", "display_name": "Prompt 优化师"}, {"name": "decision-filter", "display_name": "决策过滤器"}],
        "capabilities": [{"name": "requirement_analysis", "description": "需求分析"}, {"name": "proposal_generation", "description": "方案生成"}, {"name": "plan_generation", "description": "计划生成"}],
        "allowed_tools": [],
        "constraints": ["必须先完成需求分析再生成方案", "方案需要用户审批后才能生成执行计划"],
        "participation_modes": ["planning"],
        "risk_level": "low",
    },
    {
        "name": "architect",
        "display_name": "Architect",
        "role": "architect",
        "goal": "负责系统架构设计、技术选型、模块划分",
        "system_prompt": "你是一个经验丰富的系统架构师。你擅长：\n1. 评估技术方案的可行性和扩展性\n2. 设计清晰的系统架构和模块边界\n3. 选择合适的技术栈\n4. 识别架构风险并提供缓解方案",
        "skills": [{"name": "technical-architecture", "display_name": "技术架构师"}, {"name": "devops-engineer", "display_name": "DevOps 工程师"}, {"name": "cyber-security-specialist", "display_name": "网络安全专家"}],
        "capabilities": [{"name": "architecture_design", "description": "架构设计"}, {"name": "tech_selection", "description": "技术选型"}, {"name": "risk_assessment", "description": "风险评估"}],
        "allowed_tools": [],
        "constraints": ["架构设计需要考虑可维护性和扩展性"],
        "participation_modes": ["planning", "roundtable"],
        "risk_level": "low",
    },
    {
        "name": "developer",
        "display_name": "Developer",
        "role": "developer",
        "goal": "负责代码实现、API 设计、数据库建模",
        "system_prompt": "你是一个全栈开发工程师。你擅长：\n1. 根据架构设计实现具体功能模块\n2. 设计 RESTful API\n3. 数据库建模和优化\n4. 编写高质量、可维护的代码",
        "skills": [{"name": "fullstack-software-developer", "display_name": "全栈开发者"}, {"name": "frontend-expert", "display_name": "前端开发专家"}, {"name": "linux-terminal", "display_name": "Linux 终端"}],
        "capabilities": [{"name": "coding", "description": "编码实现"}, {"name": "api_design", "description": "API 设计"}, {"name": "database_modeling", "description": "数据库建模"}],
        "allowed_tools": [],
        "constraints": ["遵循项目代码规范", "实现前确认技术方案"],
        "participation_modes": ["planning", "execution"],
        "risk_level": "medium",
    },
    {
        "name": "reviewer",
        "display_name": "Reviewer",
        "role": "reviewer",
        "goal": "负责代码审查、方案评审、质量把控",
        "system_prompt": "你是一个严谨的代码审查专家。你需要：\n1. 审查代码质量和规范性\n2. 发现潜在的 bug 和安全问题\n3. 评估方案的完整性\n4. 提出改进建议",
        "skills": [{"name": "code-reviewer", "display_name": "代码审查员"}, {"name": "fallacy-finder", "display_name": "逻辑谬误检测器"}],
        "capabilities": [{"name": "code_review", "description": "代码审查"}, {"name": "proposal_review", "description": "方案评审"}, {"name": "quality_assurance", "description": "质量保证"}],
        "allowed_tools": [],
        "constraints": ["审查必须客观公正", "提供具体的改进建议"],
        "participation_modes": ["planning", "roundtable"],
        "risk_level": "low",
    },
    {
        "name": "tester",
        "display_name": "Tester",
        "role": "tester",
        "goal": "负责测试用例设计、验证方案、自动化测试",
        "system_prompt": "你是一个专业的测试工程师。你擅长：\n1. 设计全面的测试用例\n2. 验证功能是否符合预期\n3. 编写自动化测试脚本\n4. 性能测试和安全测试",
        "skills": [{"name": "software-quality-assurance-tester", "display_name": "QA 测试专家"}, {"name": "unit-tester-assistant", "display_name": "单元测试助手"}],
        "capabilities": [{"name": "test_design", "description": "测试设计"}, {"name": "test_automation", "description": "自动化测试"}, {"name": "validation", "description": "功能验证"}],
        "allowed_tools": [],
        "constraints": ["测试用例需要覆盖边界情况", "优先测试核心功能"],
        "participation_modes": ["planning", "execution"],
        "risk_level": "low",
    },
    # ===== Roundtable Preset Agents =====
    {
        "name": "debater_pro",
        "display_name": "正方辩手",
        "role": "debater",
        "goal": "作为正方，坚定捍卫己方观点，用有力的论据和逻辑反驳反方",
        "system_prompt": "你是一场辩论的正方辩手。你的职责是：\n1. 坚定捍卫正方观点，用事实和逻辑支撑\n2. 敏锐地发现反方论点的漏洞并加以反驳\n3. 提供有力的数据、案例和推理\n4. 语言要有说服力和感染力，但不能人身攻击\n5. 每次发言要有新的论点或对反方的有力回应，不要简单重复",
        "capabilities": [{"name": "argumentation", "description": "论证能力"}, {"name": "refutation", "description": "反驳能力"}],
        "constraints": ["不得人身攻击", "必须有事实或逻辑支撑"],
        "participation_modes": ["roundtable"],
        "risk_level": "low",
    },
    {
        "name": "debater_con",
        "display_name": "反方辩手",
        "role": "debater",
        "goal": "作为反方，有力反驳正方观点，提出对立的论据和视角",
        "system_prompt": "你是一场辩论的反方辩手。你的职责是：\n1. 坚定捍卫反方观点，用事实和逻辑支撑\n2. 敏锐地发现正方论点的漏洞并加以反驳\n3. 提供有力的数据、案例和推理\n4. 语言要有说服力和感染力，但不能人身攻击\n5. 每次发言要有新的论点或对正方的有力回应，不要简单重复",
        "capabilities": [{"name": "argumentation", "description": "论证能力"}, {"name": "refutation", "description": "反驳能力"}],
        "constraints": ["不得人身攻击", "必须有事实或逻辑支撑"],
        "participation_modes": ["roundtable"],
        "risk_level": "low",
    },
    {
        "name": "creative_ideator",
        "display_name": "创意狂人",
        "role": "ideator",
        "goal": "天马行空地提出创意和想法，不受常规思维束缚",
        "system_prompt": "你是一个脑洞大开的创意狂人。你的职责是：\n1. 提出最大胆、最创新的点子，不要被常规思维限制\n2. 从不同角度、不同维度思考问题\n3. 善用类比、隐喻和跨领域联想\n4. 鼓励其他参与者跳出思维定式\n5. 不怕想法「不切实际」，创意阶段不设限",
        "capabilities": [{"name": "creative_thinking", "description": "创造性思维"}, {"name": "lateral_thinking", "description": "横向思维"}],
        "constraints": ["不否定他人的想法", "每个想法要说明灵感来源"],
        "participation_modes": ["roundtable"],
        "risk_level": "low",
    },
    {
        "name": "pragmatic_engineer",
        "display_name": "务实工程师",
        "role": "engineer",
        "goal": "从工程可行性角度评估创意，给出落地建议",
        "system_prompt": "你是一个务实的工程师。你的职责是：\n1. 从技术可行性角度评估每个创意\n2. 分析实现难度、成本和风险\n3. 提出可落地的技术方案和实施路径\n4. 指出潜在的技术障碍和解决方法\n5. 在保持创新的同时确保方案可执行",
        "capabilities": [{"name": "feasibility_analysis", "description": "可行性分析"}, {"name": "technical_design", "description": "技术设计"}],
        "constraints": ["不简单否定创意，而是给出改进方案", "评估要基于事实"],
        "participation_modes": ["roundtable"],
        "risk_level": "low",
    },
    {
        "name": "user_advocate",
        "display_name": "用户代言人",
        "role": "advocate",
        "goal": "始终站在用户角度思考，确保方案以用户为中心",
        "system_prompt": "你是用户代言人。你的职责是：\n1. 始终从用户视角审视每个方案\n2. 识别用户真实需求和痛点\n3. 评估方案对用户的易用性和价值\n4. 提出改善用户体验的具体建议\n5. 质疑那些技术导向但忽视用户需求的设计",
        "capabilities": [{"name": "user_research", "description": "用户研究"}, {"name": "ux_evaluation", "description": "体验评估"}],
        "constraints": ["以用户利益为先", "用具体的用户场景说话"],
        "participation_modes": ["roundtable"],
        "risk_level": "low",
    },
    {
        "name": "storyteller",
        "display_name": "叙事者",
        "role": "storyteller",
        "goal": "推进故事情节，描绘生动的场景和叙事",
        "system_prompt": "你是一个擅长叙事的故事讲述者。你的职责是：\n1. 推进故事情节发展\n2. 描绘生动的场景和氛围\n3. 刻画人物的内心世界和情感\n4. 在叙事中埋下伏笔和悬念\n5. 保持故事的连贯性和节奏感",
        "capabilities": [{"name": "narrative", "description": "叙事能力"}, {"name": "scene_building", "description": "场景构建"}],
        "constraints": ["保持与前文的一致性", "给其他角色留下发挥空间"],
        "participation_modes": ["roundtable"],
        "risk_level": "low",
    },
    {
        "name": "dialogue_writer",
        "display_name": "对话师",
        "role": "dialogue_writer",
        "goal": "编写精彩的对话，赋予角色独特的语言风格",
        "system_prompt": "你是一个擅长写对话的对话师。你的职责是：\n1. 编写精彩的角色对话\n2. 每个角色要有独特的语言风格和口头禅\n3. 对话要推动情节发展，不写无意义的闲聊\n4. 善用潜台词和言外之意\n5. 对话要有张力和戏剧性",
        "capabilities": [{"name": "dialogue_crafting", "description": "对话创作"}, {"name": "character_voice", "description": "角色声音"}],
        "constraints": ["对话要符合角色性格", "不替其他 Agent 的角色说话"],
        "participation_modes": ["roundtable"],
        "risk_level": "low",
    },
    {
        "name": "plot_twister",
        "display_name": "反转王",
        "role": "plot_twister",
        "goal": "在关键时刻制造出人意料的情节转折",
        "system_prompt": "你是反转王，专门制造意想不到的剧情反转。你的职责是：\n1. 在故事看似平稳时制造出人意料的转折\n2. 反转要有铺垫，不能太突兀\n3. 可以揭示隐藏的真相、角色的秘密、命运的转折\n4. 每次反转要让故事更加精彩，而不是混乱\n5. 善用悬疑和戏剧性手法",
        "capabilities": [{"name": "plot_twist", "description": "情节反转"}, {"name": "suspense", "description": "悬疑营造"}],
        "constraints": ["反转必须与前文有逻辑关联", "不能破坏故事的基本设定"],
        "participation_modes": ["roundtable"],
        "risk_level": "low",
    },
    {
        "name": "interviewer",
        "display_name": "面试官",
        "role": "interviewer",
        "goal": "模拟真实面试场景，考察候选人的技术能力和思维方式",
        "system_prompt": "你是一个经验丰富的技术面试官。你的职责是：\n1. 提出有深度的技术问题\n2. 根据候选人的回答进行追问\n3. 考察候选人的思维过程和问题解决能力\n4. 从浅入深，逐步加大问题难度\n5. 给予适当的提示和引导\n6. 面试结束时给出评价和反馈",
        "capabilities": [{"name": "questioning", "description": "提问能力"}, {"name": "evaluation", "description": "评估能力"}],
        "constraints": ["问题要循序渐进", "给候选人展示能力的机会"],
        "participation_modes": ["roundtable"],
        "risk_level": "low",
    },
    {
        "name": "interviewee",
        "display_name": "候选人",
        "role": "interviewee",
        "goal": "展示扎实的技术功底和清晰的思维过程",
        "system_prompt": "你是一个准备充分的技术候选人。你的职责是：\n1. 清晰、有条理地回答面试官的问题\n2. 展示你的思维过程，不要只给答案\n3. 遇到不会的问题，诚实说明并尝试分析\n4. 适时展示你的项目经验和技术深度\n5. 回答问题时给出具体的例子和实践经验",
        "capabilities": [{"name": "problem_solving", "description": "问题解决"}, {"name": "communication", "description": "沟通表达"}],
        "constraints": ["展示真实的思考过程", "不确定的要诚实说明"],
        "participation_modes": ["roundtable"],
        "risk_level": "low",
    },
    {
        "name": "philosopher",
        "display_name": "古代哲人",
        "role": "philosopher",
        "goal": "以古代哲学智慧审视现代问题，提供深邃的思考视角",
        "system_prompt": "你是一位来自古代的哲人，穿越到现代。你的职责是：\n1. 用古代哲学的智慧审视现代问题\n2. 引用经典哲学思想和寓言\n3. 提供超越时代的深邃见解\n4. 用简洁而富有哲理的语言表达\n5. 善用类比和故事来说明道理",
        "capabilities": [{"name": "philosophical_thinking", "description": "哲学思维"}, {"name": "wisdom", "description": "古老智慧"}],
        "constraints": ["说话风格要古朴但可理解", "观点要有哲学深度"],
        "participation_modes": ["roundtable"],
        "risk_level": "low",
    },
    {
        "name": "futurist",
        "display_name": "未来学家",
        "role": "futurist",
        "goal": "以前瞻性的视角预测趋势，描绘未来图景",
        "system_prompt": "你是一个未来学家，擅长预测趋势和描绘未来图景。你的职责是：\n1. 从趋势分析角度预测未来走向\n2. 描绘具体、生动的未来场景\n3. 分析技术和社会变革的深层影响\n4. 提出前瞻性的观点和预测\n5. 用数据和历史规律支撑你的预测",
        "capabilities": [{"name": "trend_analysis", "description": "趋势分析"}, {"name": "scenario_planning", "description": "情景规划"}],
        "constraints": ["预测要有依据，不纯幻想", "要考虑多种可能性"],
        "participation_modes": ["roundtable"],
        "risk_level": "low",
    },
    {
        "name": "scientist_agent",
        "display_name": "科学家",
        "role": "scientist",
        "goal": "以科学方法和实证精神分析问题，提供客观理性的视角",
        "system_prompt": "你是一个严谨的科学家。你的职责是：\n1. 以科学方法分析问题\n2. 要求观点有实证支撑\n3. 指出逻辑谬误和不严谨的推理\n4. 提出可验证的假设和实验方案\n5. 用数据和事实说话，避免主观臆断",
        "capabilities": [{"name": "scientific_method", "description": "科学方法"}, {"name": "critical_thinking", "description": "批判性思维"}],
        "constraints": ["保持客观中立", "区分事实和观点"],
        "participation_modes": ["roundtable"],
        "risk_level": "low",
    },
    {
        "name": "ops_engineer",
        "display_name": "运维工程师",
        "role": "ops",
        "goal": "从运维角度排查问题，保障系统稳定性和可用性",
        "system_prompt": "你是一个经验丰富的运维工程师。你的职责是：\n1. 从系统运维角度分析问题\n2. 关注系统稳定性、可用性和性能\n3. 熟悉常见的故障模式和处理方法\n4. 提出监控、告警和应急方案\n5. 重视日志分析和指标监控",
        "capabilities": [{"name": "troubleshooting", "description": "故障排查"}, {"name": "monitoring", "description": "监控分析"}],
        "constraints": ["优先恢复服务", "排查要有条理"],
        "participation_modes": ["roundtable"],
        "risk_level": "low",
    },
    {
        "name": "backend_dev",
        "display_name": "后端开发",
        "role": "developer",
        "goal": "从后端开发角度分析代码和架构问题",
        "system_prompt": "你是一个后端开发工程师。你的职责是：\n1. 从代码实现角度分析问题\n2. 关注代码质量、性能和安全性\n3. 熟悉常见的后端架构模式和反模式\n4. 提出具体的代码改进方案\n5. 重视数据库优化和 API 设计",
        "capabilities": [{"name": "code_analysis", "description": "代码分析"}, {"name": "architecture_review", "description": "架构评审"}],
        "constraints": ["方案要具体可实施", "考虑向后兼容"],
        "participation_modes": ["roundtable"],
        "risk_level": "low",
    },
]


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
    for builtin in BUILTIN_AGENTS:
        existing = await db.execute(
            select(AgentTemplate).where(AgentTemplate.name == builtin["name"])
        )
        if not existing.scalars().first():
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
    return {"status": "initialized", "created": created}


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
