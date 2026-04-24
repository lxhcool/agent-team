# Team Agent MVP 技术设计文档

## 1. 目标

本文档的目标是把当前需求文档转化为可开工的 MVP 技术方案。

MVP 要跑通的主链路是：

1. 用户在 Web 输入需求
2. 服务端创建 Planning Session
3. Leader + Agent 团队生成方案
4. 导出 `proposal.md`
5. 生成 `execution_plan.json`
6. 开发者在本地使用 CLI 执行计划
7. CLI 修改本地项目、运行验证命令
8. 生成 `execution_result.json`
9. Web 展示执行摘要

本文档只覆盖 MVP，不覆盖第二阶段能力。

---

## 2. MVP 范围收敛

### 2.1 本阶段必须实现

- Web 主入口
- Planning Session
- Leader + 2-3 个 Agent 的最小规划链路
- Proposal 生成
- Execution Plan 生成
- 本地 CLI 导入执行计划
- 本地 Workspace 执行最小开发任务
- 验证命令执行
- Execution Result 生成
- SQLite 持久化
- Markdown 聊天输出与 Markdown 文档导出
- 基础安全边界

### 2.2 本阶段明确不实现

- 多组织权限系统
- 向量检索
- 多模态深度支持
- Web 直接远程控制本地 CLI
- CLI 常驻接单
- 插件市场
- 自动 Git 工作流
- 并行 DAG 执行
- 重型圆桌模式

---

## 3. 模块划分

MVP 建议拆成以下模块。

### 3.1 `web-app`

职责：
- 提供需求输入页面
- 展示聊天对话
- 展示方案
- 导出 `proposal.md`
- 展示可执行计划
- 展示执行结果摘要
- 模型与 Provider 配置
- Agent 模板与 Skill 管理
- 用量统计展示

建议技术：
- Next.js
- React
- TypeScript
- Tailwind CSS
- Markdown 渲染组件

#### 页面结构

| 页面 | 路由 | 优先级 | 职责 |
|------|------|--------|------|
| 会话列表 | `/` | P0 | 展示历史 Planning Session / Roundtable 列表，快速新建会话 |
| Planning 工作区 | `/sessions/:id` | P0 | 聊天区 + Agent 讨论展示 + 方案确认 + Proposal / Plan 导出 + 审批入口 |
| 执行结果详情 | `/execution/:id` | P1 | 查看 CLI 执行状态、改动文件列表、验证结果、执行日志摘要 |
| 模型设置 | `/settings/models` | P0 | 配置 Provider、API Key、默认模型、Fallback 链、预算上限 |
| Agent 管理 | `/settings/agents` | P1 | 查看/创建 Agent 模板，配置挂载 Skill、模型、约束 |
| Skill 管理 | `/settings/skills` | P1 | 查看内置/自建/导入 Skill，导入预览与审核启用 |
| 用量统计 | `/usage` | P1 | 按 Session / Agent / 模型维度查看 Token 消耗和费用估算 |
| 圆桌工作区 | `/roundtable/:id` | P1 | 多 Agent 受控讨论 + 每轮摘要 + 转为 Planning Session |
| 安全配置 | `/settings/security` | P2 | safe_mode、命令黑名单、路径限制、敏感文件保护规则 |

#### 模型设置页核心内容

模型设置是用户使用系统的前提条件（用户自带 API Key），该页面至少需要：

- **Provider 管理**：
  - 内置预设：OpenAI / Anthropic / Google
  - 自定义 Provider：通过 `base_url + api_key` 接入任意 OpenAI 兼容 API 的第三方模型服务（硅基流动、DeepSeek、月之暗面、零一万物、Ollama 本地模型等）
  - 每个 Provider 可配置多个模型
- **API Key 配置**：每个 Provider 填写自己的 Key，加密存储
- **默认模型选择**：全局默认模型、Planning 场景默认模型、Execution 场景默认模型
- **Fallback 链配置**：主模型失败后自动切换的备用模型列表（可跨 Provider）
- **预算设置**：Session 级 Token 或金额上限，超限自动暂停

#### Planning 工作区布局建议

工作区是用户最常驻的页面，建议分为以下区域：

- **左侧栏**：会话内 Task 列表与状态
- **中间主区**：聊天对话流（Markdown 渲染），含 Agent 标签和分色
- **右侧栏**：Proposal 预览 / Execution Plan 预览（按阶段切换）

### 3.2 `api-server`

职责：
- 提供 Web API
- 管理 Session 生命周期
- 调用 Orchestrator
- 存储与读取数据
- 管理 Proposal / Execution Plan / Execution Result

建议技术：
- FastAPI
- Pydantic v2
- SQLAlchemy 2.0

### 3.3 `orchestrator`

职责：
- 创建 Planning Session
- 驱动 Leader
- 维护 Task 状态流转
- 调用 Agent Runtime
- 生成 Proposal 与 Execution Plan

核心对象：
- `PlanningSessionService`
- `ExecutionPlanService`
- `TaskScheduler`

### 3.4 `agent-runtime`

职责：
- 管理 Leader 与子 Agent 生命周期
- 分发消息
- 调用 Skill
- 调用 LLM Router
- 产出结构化结果

核心对象：
- `BaseAgent`
- `LeaderAgent`
- `ResearcherAgent`
- `PlannerAgent`
- `ReviewerAgent`
- `AgentSupervisor`

### 3.5 `skill-system`

职责：
- 发现 Skill
- 解析 Skill frontmatter
- 根据 Agent 配置挂载 Skill
- 区分 builtin / custom / imported 来源

核心对象：
- `SkillRegistry`
- `SkillLoader`
- `SkillMetadata`

### 3.6 `llm-router`

职责：
- 统一调用模型
- Provider 路由
- fallback / retry
- token 统计
- 流式输出与结构化输出支持
- 支持任意 OpenAI 兼容 API 的第三方模型服务

核心对象：
- `LLMProvider`
- `LLMRouter`
- `ProviderAdapter`
- `CustomProviderConfig`

设计原则：
- 所有 Provider 统一走 OpenAI Chat Completions 兼容协议
- 内置预设 Provider（OpenAI / Anthropic / Google）提供开箱即用体验
- 第三方模型服务（硅基流动、DeepSeek、月之暗面、零一万物等）通过 `base_url + api_key + model_name` 配置接入，无需代码改动
- 自定义 Provider 与内置 Provider 在路由、Fallback、Token 统计等方面完全平等

### 3.7 `artifact-service`

职责：
- 统一管理：
  - `proposal.md`
  - `execution_plan.json`
  - `execution_result.json`
  - 执行过程产物
- 管理 artifact 元数据和存储路径

核心对象：
- `ArtifactService`
- `ProposalRenderer`
- `ExecutionPlanSerializer`
- `ExecutionResultSerializer`

### 3.8 `cli-executor`

职责：
- `apply` 执行计划
- `pull-plan` 拉取计划
- 绑定本地项目路径
- 调用 LocalWorkspace
- 运行验证命令
- 输出执行结果

核心对象：
- `ApplyCommand`
- `PullPlanCommand`
- `ExecutionRunner`

### 3.9 `workspace`

职责：
- 提供本地文件和命令操作抽象
- 实施工作目录限制和命令安全策略

核心对象：
- `Workspace`
- `LocalWorkspace`
- `WorkspacePolicy`

### 3.10 `storage`

职责：
- 持久化 Planning Session / Execution Session / Task / Message / Artifact / LLM Call / Tool Execution

核心对象：
- repository 层
- SQLAlchemy models
- migration 管理

---

## 4. 模块依赖关系

建议依赖关系如下：

- `web-app` -> `api-server`
- `api-server` -> `orchestrator`
- `orchestrator` -> `agent-runtime`
- `agent-runtime` -> `skill-system`
- `agent-runtime` -> `llm-router`
- `agent-runtime` -> `artifact-service`
- `api-server` -> `storage`
- `cli-executor` -> `workspace`
- `cli-executor` -> `artifact-service` 的序列化协议
- `cli-executor` -> `storage`（本地可选缓存，MVP 可不强依赖）

原则：
- CLI 不依赖服务端内部实现，只依赖协议对象
- Skill 不直接授予权限
- Workspace 是唯一文件/命令落地层

---

## 5. 关键数据流

### 5.1 Planning 数据流

1. Web 提交需求
2. API 创建 `planning_session`
3. Orchestrator 调用 Leader
4. Leader 调度 Agent 生成方案
5. Agent 输出统一经 Markdown 化整理
6. ArtifactService 生成：
   - `proposal.md`
   - `execution_plan.json`
7. API 返回 Web 展示

### 5.2 Execution 数据流

1. CLI 读取 `execution_plan.json` 或通过 `plan_id` 拉取计划
2. CLI 创建本地 `execution_session`
3. CLI 绑定本地仓库路径
4. ExecutionRunner 逐 task 执行
5. LocalWorkspace 改文件/跑命令
6. 收集验证结果
7. 生成 `execution_result.json`
8. 输出给用户并可选回传服务端

### 5.3 Roundtable 数据流

1. Web 发起 Roundtable
2. Agent 团队多轮受控讨论
3. 每轮结束生成摘要
4. 输出候选结论
5. 用户决定是否转为 Planning Session

Roundtable 不直接产出 Execution Plan。

---

## 6. 数据库最小表设计

MVP 建议至少保留以下表。

### 6.1 `planning_sessions`

字段建议：
- `id`
- `title`
- `user_id`
- `status`
- `mode`
- `input_text`
- `summary`
- `created_at`
- `updated_at`

### 6.2 `execution_sessions`

字段建议：
- `id`
- `plan_id`
- `proposal_id`
- `user_id`
- `status`
- `project_path`
- `summary`
- `created_at`
- `updated_at`

### 6.3 `roundtable_sessions`

字段建议：
- `id`
- `user_id`
- `topic`
- `status`
- `max_rounds`
- `current_round`
- `summary`
- `created_at`
- `updated_at`

### 6.4 `tasks`

字段建议：
- `id`
- `session_type`
- `session_id`
- `title`
- `description`
- `status`
- `assigned_agent`
- `owner_role`
- `dependencies_json`
- `target_paths_json`
- `validation_commands_json`
- `result_summary`
- `assignment_version`
- `created_at`
- `updated_at`

### 6.5 `messages`

字段建议：
- `id`
- `session_type`
- `session_id`
- `seq`
- `sender`
- `receiver`
- `message_type`
- `category`
- `content`
- `attachments_json`
- `dedupe_key`
- `ack_at`
- `retry_count`
- `created_at`

### 6.6 `artifacts`

字段建议：
- `id`
- `session_type`
- `session_id`
- `task_id`
- `artifact_type`
- `filename`
- `path`
- `mime_type`
- `size_bytes`
- `checksum`
- `source`
- `created_by`
- `created_at`

### 6.7 `llm_calls`

字段建议：
- `id`
- `session_type`
- `session_id`
- `agent_name`
- `model`
- `prompt_tokens`
- `completion_tokens`
- `cost`
- `duration_ms`
- `finish_reason`
- `was_truncated`
- `was_continued`
- `created_at`

### 6.8 `tool_executions`

字段建议：
- `id`
- `session_type`
- `session_id`
- `task_id`
- `agent_name`
- `tool_name`
- `status`
- `duration_ms`
- `input_json`
- `output_json`
- `created_at`

### 6.9 `skills`

字段建议：
- `id`
- `name`
- `display_name`
- `description`
- `version`
- `source_type`
- `source_ref`
- `author`
- `tools_json`
- `recommended_for_json`
- `output_format`
- `content`
- `created_at`

### 6.10 `agent_templates`

字段建议：
- `id`
- `name`
- `display_name`
- `role`
- `goal`
- `model`
- `skills_json`
- `capabilities_json`
- `allowed_tools_json`
- `constraints_json`
- `participation_modes_json`
- `risk_level`
- `version`
- `created_at`

---

## 7. API 设计（MVP）

### 7.1 Planning Session

- `POST /api/planning-sessions`
  - 创建会话并提交用户需求
- `GET /api/planning-sessions/{id}`
  - 获取会话详情
- `POST /api/planning-sessions/{id}/approve`
  - 确认方案
- `POST /api/planning-sessions/{id}/cancel`
  - 取消会话

### 7.2 Proposal / Plan

- `GET /api/planning-sessions/{id}/proposal`
  - 获取方案 Markdown
- `GET /api/planning-sessions/{id}/execution-plan`
  - 获取执行计划 JSON
- `POST /api/planning-sessions/{id}/export`
  - 导出 proposal / execution plan

### 7.3 Roundtable

- `POST /api/roundtable-sessions`
  - 发起圆桌讨论
- `GET /api/roundtable-sessions/{id}`
  - 获取圆桌状态与摘要
- `POST /api/roundtable-sessions/{id}/promote`
  - 转成 Planning Session

### 7.4 Skills / Agent Templates

- `GET /api/skills`
- `POST /api/skills`
  - 创建自建 Skill
- `POST /api/skills/import`
  - 导入外部 Skill
- `GET /api/agent-templates`
- `POST /api/agent-templates`
  - 创建自定义 Agent 模板

### 7.5 Settings

- `GET /api/settings/models`
  - 获取当前模型配置（Provider 列表、默认模型、Fallback 链、预算）
- `PUT /api/settings/models`
  - 更新模型配置
- `POST /api/settings/models/test`
  - 测试 Provider 连通性（验证 API Key 是否有效、base_url 是否可达）
- `PUT /api/settings/models/providers/{provider_name}/api-key`
  - 设置指定 Provider 的 API Key（加密存储）
- `POST /api/settings/models/providers`
  - 添加自定义 Provider（base_url + api_key + display_name）
- `PUT /api/settings/models/providers/{provider_name}`
  - 更新自定义 Provider 配置
- `DELETE /api/settings/models/providers/{provider_name}`
  - 删除自定义 Provider（内置 Provider 不可删除）
- `GET /api/settings/models/providers/{provider_name}/models`
  - 拉取 Provider 下可用模型列表（调用远程 /models 接口或手动配置）
- `GET /api/settings/security`
  - 获取安全配置（safe_mode、黑名单、路径限制）
- `PUT /api/settings/security`
  - 更新安全配置
- `GET /api/usage`
  - 获取用量统计（支持按 Session / Agent / 模型维度聚合）
- `GET /api/usage/sessions/{id}`
  - 获取指定 Session 的 Token 和费用明细

### 7.6 Execution Results

- `POST /api/execution-results`
  - CLI 回传执行结果
- `GET /api/execution-results/{plan_id}`
  - Web 查询执行结果

### 7.7 实时推送

MVP 建议只做一种：
- SSE 或 WebSocket

用途：
- Planning Session 状态变化
- 新消息
- 方案生成完成
- 执行结果更新

---

## 8. CLI 命令设计（MVP）

### 8.1 `agent-team apply`

用途：
- 导入本地 `execution_plan.json` 并执行

示例：
```bash
agent-team apply ./execution_plan.json --project /path/to/repo
```

参数建议：
- `--project`
- `--step-by-step`
- `--output`
- `--safe-mode`

### 8.2 `agent-team pull-plan`

用途：
- 通过 `plan_id` 从服务端拉取执行计划

示例：
```bash
agent-team pull-plan --plan-id plan_123 --server http://localhost:8000
```

### 8.3 `agent-team run-validation`

用途：
- 单独执行验证命令

### 8.4 `agent-team show-result`

用途：
- 查看本地执行结果文件

### 8.5 `agent-team debug *`

MVP 保留：
- `debug prompt`
- `debug messages`
- `debug replay`

---

## 9. 关键协议对象建议

### 9.1 Skill frontmatter 示例

```md
---
name: implementation_planning
display_name: Implementation Planning
description: Generate an execution-ready implementation plan
version: 1.0.0
author: team-agent
source: builtin
tools: [file_read, web_search]
recommended_for: [planning, review]
output_format: markdown
tags: [planning, architecture]
safety_notes: read-only planning skill
---
```

### 9.2 Custom Provider Config 示例

```json
{
  "provider_name": "siliconflow",
  "display_name": "硅基流动",
  "base_url": "https://api.siliconflow.cn/v1",
  "api_key": "sk-xxx",
  "api_type": "openai_compatible",
  "models": [
    {
      "model_id": "deepseek-ai/DeepSeek-V3",
      "display_name": "DeepSeek V3",
      "context_window": 65536,
      "pricing": {
        "prompt_per_million": 1.0,
        "completion_per_million": 2.0,
        "currency": "CNY"
      }
    },
    {
      "model_id": "Qwen/Qwen2.5-72B-Instruct",
      "display_name": "Qwen2.5-72B",
      "context_window": 32768,
      "pricing": {
        "prompt_per_million": 4.0,
        "completion_per_million": 4.0,
        "currency": "CNY"
      }
    }
  ],
  "default_model": "deepseek-ai/DeepSeek-V3"
}
```

设计说明：
- `api_type` 默认为 `openai_compatible`，表示走 OpenAI Chat Completions 兼容协议
- 内置 Provider（OpenAI / Anthropic / Google）可使用各自的专有协议适配器
- `pricing` 为可选字段，用于成本估算，用户也可不填
- 支持人民币 / 美元等多种货币的定价

### 9.3 Agent Template 示例

```json
{
  "name": "researcher",
  "display_name": "Researcher",
  "role": "research",
  "goal": "Collect facts and constraints",
  "model": "claude-sonnet",
  "skills": ["research_analysis", "roundtable_summary"],
  "capabilities": ["search", "summarize"],
  "allowed_tools": ["file_read", "web_search"],
  "constraints": ["no_file_write", "no_shell_execute"],
  "participation_modes": ["planning", "roundtable"],
  "risk_level": "low",
  "version": "1.0.0"
}
```

### 9.4 Execution Plan 示例结构

```json
{
  "plan_id": "plan_123",
  "source_session_id": "ps_123",
  "proposal_id": "prop_123",
  "title": "Implement login flow",
  "goal": "Add a basic login flow for web users",
  "summary": "Implement login UI, backend endpoint and validation",
  "tasks": [
    {
      "task_id": "task_1",
      "title": "Add login UI",
      "description": "Create login form and submit flow",
      "owner_role": "coder",
      "target_paths": ["src/ui/login.tsx"],
      "steps": ["create form", "wire submit", "show errors"],
      "validation_commands": ["npm test -- login"],
      "expected_artifacts": ["updated ui files"],
      "done_definition": "login form renders and submits correctly",
      "risk_level": "medium"
    }
  ],
  "dependencies": [],
  "target_paths": ["src/ui", "src/api"],
  "constraints": ["no dependency change"],
  "validation_commands": ["npm test"],
  "expected_artifacts": ["proposal.md", "execution_result.json"],
  "approval_requirements": [],
  "stop_conditions": ["high risk action requested"]
}
```

---

## 10. 推荐开发顺序

### 阶段 1：后端骨架
- FastAPI
- SQLite
- SQLAlchemy models
- Planning Session / Execution Session 表
- Artifact 表

### 阶段 2：规划链路
- LeaderAgent
- 最小 Agent Runtime
- SkillRegistry
- Proposal 生成
- Execution Plan 生成

### 阶段 3：Web MVP
- 会话列表页 `/`
- Planning 工作区 `/sessions/:id`（聊天 Markdown + 方案展示 + 导出）
- 模型设置页 `/settings/models`（Provider + API Key + 默认模型 + Fallback）
- Execution Plan 下载

### 阶段 4：CLI MVP
- `apply`
- LocalWorkspace
- 执行 task
- 运行验证命令
- 输出 `execution_result.json`

### 阶段 5：回传与打磨
- 执行结果回传 API
- Web 结果展示
- 截断处理
- 错误提示
- 基础调试命令

---

## 11. 现在是否可以进入开发阶段

结论：可以。

原因：
- 产品形态已清晰
- 核心状态机已明确
- 核心协议对象已明确
- Agent / Skill 元数据已明确
- MVP 范围已收敛

从这一刻起，继续扩需求的收益会快速下降，最合理的动作是进入代码结构设计与实现阶段。