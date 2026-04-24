# Team Agent

Web 主导 + 本地 CLI 执行的多 Agent 协作系统。

## 架构

- **后端**：Python FastAPI + SQLAlchemy + SQLite + SSE
- **前端**：Next.js 15 + React 19 + TypeScript + Tailwind CSS 4
- **CLI**：Python Click

## 功能

### Web 端

| 页面 | 路由 | 功能 |
|------|------|------|
| 会话列表 | `/` | 创建/搜索/删除 Planning Session |
| 规划工作区 | `/sessions/[id]` | 聊天 + Agent 讨论 + 方案确认 + 导出 + 执行结果入口 |
| 执行结果详情 | `/executions/[id]` | 查看 CLI 执行状态和结果 |
| 模型设置 | `/settings/models` | Provider + API Key + 默认模型 + Fallback + 自定义 Provider |
| Agent 管理 | `/settings/agents` | 内置/自定义 Agent 模板 |
| Skill 管理 | `/settings/skills` | 内置/自定义 Skill CRUD |
| 安全配置 | `/settings/security` | safe_mode + 命令黑名单 + 路径限制 |
| 用量统计 | `/usage` | Token/费用按 Provider/Model/Agent 聚合 |

### CLI 命令

| 命令 | 功能 |
|------|------|
| `agent-team apply` | 导入执行计划并执行 |
| `agent-team pull-plan` | 从服务端拉取执行计划 |
| `agent-team push-result` | 回传执行结果到服务端 |
| `agent-team show-result` | 查看本地执行结果 |

### 后端 API

| 路由前缀 | 模块 | 说明 |
|---------|------|------|
| `/api/planning-sessions` | planning | 规划会话 CRUD |
| `/api/planning-sessions/{id}/messages` | messages | 消息 + SSE 流 |
| `/api/execution-results` | execution-results | CLI 结果回传/查询 |
| `/api/artifacts` | artifacts | 产物文件管理 |
| `/api/roundtable-sessions` | roundtable | 圆桌讨论 |
| `/api/usage` | usage | 用量统计 |
| `/api/settings/agents` | agents | Agent 模板 |
| `/api/settings/skills` | skills | Skill CRUD |
| `/api/settings/security` | security | 安全配置 |
| `/api/settings/models` | settings | 模型/Provider 配置 |

## 快速开始

### 1. 配置环境

```bash
cp .env.example .env
# 编辑 .env 填入 ENCRYPTION_KEY 和 LLM Provider 信息
```

### 2. 启动后端

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

后端启动在 http://localhost:8000，API 文档在 http://localhost:8000/docs

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev
```

前端启动在 http://localhost:3000，API 请求自动代理到后端

### 4. 安装 CLI

```bash
cd cli
pip install -e .
agent-team --help
```

## 内置 Agent

系统启动时自动初始化 4 个内置 Agent：

| Agent | 角色 | 职责 |
|-------|------|------|
| Leader | leader | 分析需求、拆解任务、编排方案 |
| Researcher | researcher | 调研分析、收集信息和约束 |
| Reviewer | reviewer | 代码审查、方案评审、质量保证 |
| Tester | tester | 测试用例设计、验证方案、自动化测试 |

## 数据模型

12 张核心表：planning_sessions, execution_sessions, roundtable_sessions, tasks, messages, artifacts, llm_calls, tool_executions, skills, agent_templates, provider_configs, model_settings

## 文档

- [需求文档](docs/requirements.md)
- [MVP 技术设计](docs/mvp-technical-design.md)
