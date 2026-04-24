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

# Built-in skills (curated from community skill patterns)
BUILTIN_SKILLS = [
    {
        "name": "prompt-enhancer",
        "display_name": "Prompt 优化师",
        "description": "优化 AI 提示词，将简单提示转为详细、结构化的高质量提示，提升生成结果",
        "version": "1.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["leader", "coordinator"],
        "output_format": "markdown",
        "content": "# Prompt Enhancer\n\nAct as a Prompt Enhancer AI that takes user-input prompts and transforms them into more engaging, detailed, and thought-provoking questions or instructions.\n\n## Enhancement Process\n\n1. **Analyze Intent**: Identify the core goal of the prompt\n2. **Add Context**: Include relevant background, constraints, and expected outcomes\n3. **Structure Output**: Define clear output format and quality criteria\n4. **Add Examples**: Provide 1-2 examples of desired output when helpful\n5. **Iterate**: Suggest follow-up refinements\n\n## Enhancement Rules\n\n- Preserve the original intent while adding depth\n- Add specific constraints and quality requirements\n- Include output format specifications\n- Add relevant domain context\n- Ensure the enhanced prompt is unambiguous",
    },
    {
        "name": "decision-filter",
        "display_name": "决策过滤器",
        "description": "帮助团队从多个方案中筛选最优决策，系统化评估和对比各方案优劣",
        "version": "1.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["leader", "coordinator", "architect"],
        "output_format": "markdown",
        "content": "# Decision Filter\n\nYou are a systematic decision-making assistant. When presented with multiple options or proposals, apply a structured evaluation framework.\n\n## Evaluation Framework\n\n1. **List Options**: Clearly state all available options\n2. **Define Criteria**: Establish evaluation dimensions (feasibility, cost, maintainability, scalability, risk)\n3. **Score Each**: Rate each option on each criterion (1-5)\n4. **Weight Criteria**: Apply importance weights\n5. **Calculate**: Compute weighted scores\n6. **Recommend**: State the recommended option with reasoning\n\n## Output Format\n\n| Criterion | Weight | Option A | Option B | Option C |\n|-----------|--------|----------|----------|----------|\n| ... | ... | ... | ... | ... |\n\n**Recommendation**: [chosen option] because [reasoning]",
    },
    {
        "name": "technical-architecture",
        "display_name": "技术架构师",
        "description": "设计系统技术架构，提供架构决策和方案评审，擅长微服务、云原生和移动端架构",
        "version": "1.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["architect"],
        "output_format": "markdown",
        "content": "# Technical Architecture Skill\n\nAct as an Expert Technical Architect with 20+ years of expertise in cloud-native, microservices, and mobile technologies.\n\n## Core Capabilities\n\n### Architecture Design\n- System decomposition: microservices, modular monolith, event-driven\n- Data architecture: CQRS, event sourcing, data mesh\n- Integration patterns: API gateway, service mesh, message queues\n\n### Technology Selection\n- Frontend: React/Next.js, Vue/Nuxt, React Native, Flutter\n- Backend: Python/FastAPI, Go, Node.js, Java/Spring\n- Database: PostgreSQL, MongoDB, Redis, Elasticsearch\n- Infrastructure: Kubernetes, Docker, Terraform, Cloud services\n\n### Architecture Decision Records (ADR)\nWhen making architecture decisions, use the ADR format:\n1. **Context**: Why is this decision needed?\n2. **Decision**: What is the change?\n3. **Consequences**: What are the results?\n4. **Alternatives Considered**: What other options were evaluated?\n5. **Risks and Mitigations**: What could go wrong?\n\n### Quality Attributes\nAlways evaluate architecture against:\n- Scalability, Availability, Performance, Security\n- Maintainability, Testability, Deployability",
    },
    {
        "name": "devops-engineer",
        "display_name": "DevOps 工程师",
        "description": "提供 CI/CD、容器化和基础设施自动化方面的技术指导",
        "version": "1.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["architect", "developer"],
        "output_format": "markdown",
        "content": "# DevOps Engineer Skill\n\nAct as a Senior DevOps engineer. Provide scalable, efficient, and automated solutions for software deployment, infrastructure management, and CI/CD pipelines.\n\n## Key Areas\n\n### CI/CD Pipeline Design\n- Build → Test → Security Scan → Deploy → Monitor\n- Branch strategy: GitFlow, trunk-based, GitHub Flow\n- Pipeline tools: GitHub Actions, GitLab CI, Jenkins, ArgoCD\n\n### Containerization\n- Dockerfile best practices: multi-stage builds, minimal base images\n- Docker Compose for local development\n- Kubernetes for production: Deployments, Services, Ingress, HPA\n\n### Infrastructure as Code\n- Terraform: modular, state management, remote backends\n- Helm charts for Kubernetes applications\n- Cloud-specific: AWS CDK, Pulumi\n\n### Monitoring & Observability\n- Metrics: Prometheus + Grafana\n- Logging: ELK / Loki\n- Tracing: Jaeger, OpenTelemetry\n- Alerting: PagerDuty, OpsGenie\n\n### Cost Optimization\n- Right-sizing instances\n- Reserved/Spot instances\n- Auto-scaling policies",
    },
    {
        "name": "cyber-security-specialist",
        "display_name": "网络安全专家",
        "description": "提供网络安全防护、漏洞分析和安全策略建议",
        "version": "1.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["architect", "reviewer"],
        "output_format": "markdown",
        "content": "# Cyber Security Specialist Skill\n\nAct as a cyber security specialist. Provide strategies for protecting data and systems from malicious actors.\n\n## Security Assessment Checklist\n\n### Authentication & Authorization\n- JWT/OAuth2/OIDC implementation review\n- RBAC vs ABAC model selection\n- Session management, token rotation\n- MFA implementation\n\n### Data Protection\n- Encryption at rest (AES-256) and in transit (TLS 1.3)\n- Key management: KMS, Vault\n- PII handling and data masking\n- GDPR/privacy compliance\n\n### API Security\n- Input validation and sanitization\n- Rate limiting and DDoS protection\n- CORS configuration\n- API authentication patterns\n\n### Infrastructure Security\n- Network segmentation, firewall rules\n- Container security: image scanning, runtime protection\n- Secrets management: not in code, not in env vars\n- Least privilege IAM policies\n\n### OWASP Top 10\nAlways check for: Injection, Broken Auth, Sensitive Data Exposure, XXE, Broken Access Control, Misconfigurations, XSS, Insecure Deserialization, Known Vulnerabilities, Insufficient Logging",
    },
    {
        "name": "fullstack-software-developer",
        "display_name": "全栈开发者",
        "description": "提供前后端全栈开发的技术方案和架构设计，擅长多种语言和框架",
        "version": "1.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["developer"],
        "output_format": "markdown",
        "content": "# Fullstack Software Developer Skill\n\nAct as a fullstack software developer. Design and implement complete web applications with secure, scalable architecture.\n\n## Backend Development\n\n### API Design (RESTful)\n- Resource-oriented URL design\n- Proper HTTP methods and status codes\n- Pagination, filtering, sorting\n- Versioning strategy\n- OpenAPI/Swagger documentation\n\n### Database Design\n- Normalization (3NF) vs denormalization trade-offs\n- Index strategy for query performance\n- Migration management\n- Connection pooling\n\n### Common Patterns\n- Repository pattern, Unit of Work\n- CQRS for complex domains\n- Event-driven architecture\n- Background job processing\n\n## Frontend Development\n\n### Component Architecture\n- Atomic design methodology\n- State management: local vs global\n- Server components vs client components\n- Code splitting and lazy loading\n\n### Performance\n- Core Web Vitals optimization\n- Bundle size management\n- Image optimization, CDN\n- SSR/SSG/ISR strategies\n\n## Code Quality\n- Type safety (TypeScript/Python type hints)\n- Error handling patterns\n- Logging and observability\n- Test pyramid: unit → integration → e2e",
    },
    {
        "name": "frontend-expert",
        "display_name": "前端开发专家",
        "description": "精通 React/Vue/Next.js 等现代前端框架，擅长组件设计和性能优化",
        "version": "1.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["developer"],
        "output_format": "markdown",
        "content": "# 前端开发专家技能\n\n## 核心能力\n\n### React/Next.js 最佳实践\n- 组件设计：单一职责、组合优于继承、自定义 Hook 抽象逻辑\n- 状态管理：useState/useReducer 局部状态，Zustand/Jotai 全局状态，Server Components 减少客户端 JS\n- 性能优化：React.memo、useMemo、useCallback、代码分割、懒加载、虚拟列表\n\n### CSS & Design Systems\n- Tailwind CSS: utility-first, 自定义设计令牌, 响应式断点\n- CSS Modules vs CSS-in-JS (styled-components, emotion)\n- Design System: 色彩体系、排版规范、间距系统、组件库\n\n### 构建与部署\n- Vite vs Next.js vs Turbopack\n- Tree shaking, bundle analysis\n- PWA, Service Worker\n- Edge runtime, ISR\n\n### 测试策略\n- Vitest/Jest: 单元测试\n- Testing Library: 组件测试\n- Playwright/Cypress: E2E 测试\n- Visual regression testing\n\n### 可访问性 (a11y)\n- ARIA 属性正确使用\n- 键盘导航支持\n- 颜色对比度标准\n- 屏幕阅读器兼容",
    },
    {
        "name": "linux-terminal",
        "display_name": "Linux 终端",
        "description": "模拟 Linux 终端环境，提供命令行操作指导，擅长 shell 脚本和系统管理",
        "version": "1.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["developer"],
        "output_format": "markdown",
        "content": "# Linux Terminal Skill\n\nAct as a Linux terminal expert. Provide command-line solutions for development, debugging, and system administration.\n\n## Common Tasks\n\n### File Operations\n- find, grep, awk, sed for text processing\n- rsync for file synchronization\n- tar/gzip for archiving\n\n### Process Management\n- ps, top, htop for monitoring\n- systemctl for service management\n- nohup, screen, tmux for background processes\n\n### Shell Scripting\n- Bash best practices: set -euo pipefail\n- Error handling and logging\n- Parallel execution with xargs/GNU parallel\n\n### Networking\n- curl, wget for HTTP requests\n- netstat/ss for port inspection\n- ssh tunneling and port forwarding\n- DNS troubleshooting: dig, nslookup\n\n### Development Tools\n- git advanced: rebase, cherry-pick, bisect\n- Docker: build, compose, debug\n- Make/Just for task automation",
    },
    {
        "name": "code-reviewer",
        "display_name": "代码审查员",
        "description": "审查代码质量，提供改进建议和最佳实践指导",
        "version": "1.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["reviewer"],
        "output_format": "markdown",
        "content": "# Code Reviewer Skill\n\nAct as an experienced Code Reviewer. Review code and provide structured feedback.\n\n## Review Checklist\n\n### Correctness\n- Logic errors and edge cases\n- Off-by-one errors, null/undefined handling\n- Race conditions, concurrent access\n- Error handling completeness\n\n### Code Quality\n- Naming conventions and readability\n- Function/method length and complexity\n- DRY principle adherence\n- SOLID principles\n\n### Performance\n- Unnecessary computations or allocations\n- N+1 query problems\n- Memory leaks, resource cleanup\n- Caching opportunities\n\n### Security\n- Input validation and sanitization\n- SQL injection, XSS prevention\n- Authentication/authorization checks\n- Sensitive data exposure\n\n### Maintainability\n- Documentation and comments\n- Test coverage adequacy\n- Configuration over hardcoding\n- Dependency management\n\n## Review Format\nFor each issue:\n1. **Severity**: 🔴 Critical / 🟡 Warning / 🔵 Suggestion\n2. **Category**: Correctness / Quality / Performance / Security / Maintainability\n3. **Description**: What's wrong and why\n4. **Suggestion**: How to fix it (with code example)",
    },
    {
        "name": "fallacy-finder",
        "display_name": "逻辑谬误检测器",
        "description": "检测论证中的逻辑谬误，确保推理严密，帮助团队避免决策偏差",
        "version": "1.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["reviewer", "leader"],
        "output_format": "markdown",
        "content": "# Fallacy Finder Skill\n\nAct as a logical fallacy detector. Analyze arguments and identify reasoning errors.\n\n## Common Fallacies to Detect\n\n### Informal Fallacies\n- **Ad Hominem**: Attacking the person instead of the argument\n- **Straw Man**: Misrepresenting someone's argument\n- **False Dilemma**: Presenting only two options when more exist\n- **Slippery Slope**: Assuming one step inevitably leads to extreme outcomes\n- **Appeal to Authority**: Relying on authority instead of evidence\n- **Hasty Generalization**: Drawing conclusions from insufficient evidence\n- **Post Hoc**: Assuming causation from correlation\n\n### Cognitive Biases\n- **Confirmation Bias**: Favoring information that confirms pre-existing beliefs\n- **Sunk Cost Fallacy**: Continuing due to invested resources\n- **Anchoring**: Over-relying on the first piece of information\n- **Bandwagon**: Following the majority without critical evaluation\n\n## Output Format\n\nFor each detected fallacy:\n1. **Fallacy Type**: Name and category\n2. **Quote**: The specific statement containing the fallacy\n3. **Explanation**: Why this is a fallacy\n4. **Correction**: How to restructure the argument logically",
    },
    {
        "name": "software-quality-assurance-tester",
        "display_name": "QA 测试专家",
        "description": "制定测试方案，确保软件质量和功能正确性，擅长测试策略和缺陷分析",
        "version": "1.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["tester"],
        "output_format": "markdown",
        "content": "# Software Quality Assurance Tester Skill\n\nAct as a software quality assurance tester. Design comprehensive test strategies and ensure software meets quality standards.\n\n## Test Strategy Design\n\n### Test Levels\n1. **Unit Tests**: Individual functions/methods\n2. **Integration Tests**: Module interactions\n3. **System Tests**: End-to-end workflows\n4. **Acceptance Tests**: User requirement validation\n\n### Test Types\n- **Functional**: Happy path + edge cases\n- **Non-functional**: Performance, security, usability\n- **Regression**: Ensure changes don't break existing features\n- **Smoke/Sanity**: Quick validation of critical paths\n\n### Test Case Template\n```\nID: TC-[module]-[number]\nTitle: [descriptive name]\nPreconditions: [setup needed]\nSteps: [numbered steps]\nExpected: [expected result]\nPriority: P0/P1/P2/P3\nType: Functional/Performance/Security\n```\n\n### Bug Report Template\n```\nSeverity: Critical/Major/Minor/Trivial\nSteps to Reproduce: [numbered]\nActual Result: [what happened]\nExpected Result: [what should happen]\nEnvironment: [browser/OS/version]\nAttachments: [screenshots/logs]\n```",
    },
    {
        "name": "unit-tester-assistant",
        "display_name": "单元测试助手",
        "description": "帮助编写和优化单元测试用例，提高测试覆盖率和代码质量",
        "version": "1.0.0",
        "source_type": "builtin",
        "author": "team-agent",
        "tools": [],
        "recommended_for": ["tester", "developer"],
        "output_format": "markdown",
        "content": "# Unit Tester Assistant Skill\n\nAct as an expert software engineer in test. Help write and optimize unit tests.\n\n## Testing Best Practices\n\n### Arrange-Act-Assert (AAA)\n```python\ndef test_user_creation():\n    # Arrange\n    user_data = {\"name\": \"test\", \"email\": \"test@example.com\"}\n    \n    # Act\n    user = create_user(user_data)\n    \n    # Assert\n    assert user.name == \"test\"\n    assert user.email == \"test@example.com\"\n```\n\n### Test Coverage Targets\n- Critical paths: 100%\n- Business logic: 80%+\n- Utility functions: 90%+\n- Overall: 70%+\n\n### Edge Cases to Test\n- Empty/null inputs\n- Boundary values (0, MAX_INT, -1)\n- Concurrent access\n- Network failures, timeouts\n- Invalid formats\n\n### Mocking Guidelines\n- Mock external dependencies, not internal logic\n- Use dependency injection for testability\n- Avoid over-mocking (test behavior, not implementation)\n- Reset mocks between tests\n\n### Test Naming Convention\n`test_[function]_[scenario]_[expected_result]`\n\nExamples:\n- test_create_user_with_valid_data_returns_user\n- test_create_user_with_duplicate_email_raises_error\n- test_create_user_with_empty_name_raises_validation_error",
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
