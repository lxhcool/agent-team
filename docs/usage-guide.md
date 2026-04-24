# Team Agent 使用文档

## 目录

- [1. 产品简介](#1-产品简介)
- [2. 快速开始](#2-快速开始)
  - [2.1 环境要求](#21-环境要求)
  - [2.2 一键启动](#22-一键启动)
  - [2.3 手动启动](#23-手动启动)
  - [2.4 安装 CLI](#24-安装-cli)
- [3. 启动脚本详细说明](#3-启动脚本详细说明)
- [4. Web 端功能](#4-web-端功能)
  - [4.1 页面总览](#41-页面总览)
  - [4.2 核心工作流](#42-核心工作流)
  - [4.3 设置页面](#43-设置页面)
- [5. CLI 命令](#5-cli-命令)
- [6. Agent 系统](#6-agent-系统)
- [7. Tool 系统](#7-tool-系统)
- [8. 安全机制](#8-安全机制)
- [9. 配置说明](#9-配置说明)
- [10. 常见问题](#10-常见问题)

---

## 1. 产品简介

Team Agent 是一个 **Web 主导、本地 CLI 执行落地** 的多 Agent 协作系统。

核心链路：

```
需求输入 → 多Agent分析 → 方案确认 → 导出 proposal.md → 生成 execution_plan.json → 本地CLI执行 → 回传结果
```

系统同时服务两类角色：

- **非开发角色**：产品、测试、项目负责人 — 主要使用 Web 端提出需求、确认方案、查看结果
- **开发角色**：开发者 — 使用 Web 端接收方案和任务，通过本地 CLI 在本地代码仓库中执行开发

技术栈：

| 组件 | 技术 |
|------|------|
| 后端 | Python FastAPI + SQLAlchemy + SQLite + SSE |
| 前端 | Next.js 15 + React 19 + TypeScript + Tailwind CSS 4 |
| CLI | Python Click + Rich |

---

## 2. 快速开始

### 2.1 环境要求

- Python 3.12+
- Node.js 18+
- npm 9+

### 2.2 一键启动

```bash
# 首次使用：配置环境
cp .env.example .env
# 编辑 .env 填入 ENCRYPTION_KEY 和 LLM Provider 信息

# 一键启动所有服务
./start.sh
```

启动后输出示例：

```
🚀 Team Agent 一键启动

   后端端口: 8200 (默认 8200)
   前端端口: 3200 (默认 3200)

▶  启动后端...
   后端 PID: 12345
   ✓ 后端就绪 → http://localhost:8200

▶  启动前端...
   前端 PID: 12346
   ✓ 前端就绪 → http://localhost:3200

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✓  所有服务已启动！

   前端:  http://localhost:3200
   后端:  http://localhost:8200
   API文档: http://localhost:8200/docs

   按 Ctrl+C 停止所有服务
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**端口自动分配规则**：
- 后端默认端口 `8200`，前端默认端口 `3200`
- 如果默认端口被占用，自动 +1 寻找可用端口（最多尝试 100 次）
- 当前使用的端口会保存在 `.run/ports.env` 中

**停止服务**：
- 在启动终端按 `Ctrl+C` 自动停止并清理所有进程
- 或在另一个终端运行 `./start.sh stop`

### 2.3 手动启动

如果不使用一键脚本，可以分别启动各组件：

**启动后端**：

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8200 --reload
```

后端启动在 `http://localhost:8200`，API 文档在 `http://localhost:8200/docs`。

**启动前端**：

```bash
cd frontend
npm install
npm run dev -- --port 3200
```

前端启动在 `http://localhost:3200`，API 请求通过 Next.js rewrites 自动代理到后端。

> 注意：手动启动时，需确保 `frontend/next.config.ts` 中的 `NEXT_PUBLIC_BACKEND_PORT` 与后端端口一致。可在前端目录创建 `.env.local`：
>
> ```
> NEXT_PUBLIC_BACKEND_PORT=8200
> ```

### 2.4 安装 CLI

```bash
cd cli
pip install -e .
agent-team --help
```

首次使用建议运行初始化向导：

```bash
agent-team init
```

向导将引导你完成：
1. 服务器连通性检查
2. 模型配置
3. 项目绑定

---

## 3. 启动脚本详细说明

`start.sh` 提供以下子命令：

| 命令 | 说明 |
|------|------|
| `./start.sh` | 启动所有服务 |
| `./start.sh stop` | 停止所有服务 |
| `./start.sh status` | 查看服务状态（PID + 端口） |
| `./start.sh restart` | 重启所有服务 |
| `./start.sh help` | 显示帮助信息 |

**特性说明**：

| 特性 | 说明 |
|------|------|
| 端口自动递增 | 默认后端 8200、前端 3200，占用时自动 +1 |
| 停止自动清理 | Ctrl+C 或 `stop` 命令杀掉所有子进程、清理 PID 文件 |
| 动态 CORS | 自动将前端端口加入后端 CORS 白名单 |
| 动态 API 代理 | 前端通过 `NEXT_PUBLIC_BACKEND_PORT` 环境变量代理到正确后端端口 |
| 残留进程清理 | 检测到上次未正常退出的进程会自动清理 |
| 依赖自动安装 | 前端 `node_modules` 不存在时自动 `npm install` |

**运行时文件**（已加入 `.gitignore`）：

```
.run/
  ├── backend.pid      # 后端进程 PID
  ├── frontend.pid     # 前端进程 PID
  └── ports.env        # 当前使用的端口
```

---

## 4. Web 端功能

### 4.1 页面总览

| 页面 | 路由 | 功能 |
|------|------|------|
| 会话列表 | `/` | 创建、搜索、删除 Planning Session |
| 规划工作区 | `/sessions/[id]` | 聊天 + Agent 讨论 + 方案确认 + 导出 + 文件上传（拖拽/粘贴）+ 执行结果 |
| 执行结果详情 | `/executions/[id]` | 查看 CLI 执行状态和结果 |
| 模型设置 | `/settings/models` | Provider + API Key + 默认模型 + Fallback + 自定义 Provider |
| Agent 管理 | `/settings/agents` | 内置/自定义 Agent 模板 CRUD |
| Skill 管理 | `/settings/skills` | 内置/自定义 Skill CRUD + 远程导入预览/确认 |
| 安全配置 | `/settings/security` | safe_mode + 命令黑名单 + 路径限制 + 敏感文件模式 |
| 用量统计 | `/usage` | Token/费用按 Provider/Model/Agent 聚合 |

### 4.2 核心工作流

#### 第一步：创建会话

1. 访问首页 `/`
2. 点击"新建会话"
3. 输入需求描述

#### 第二步：多 Agent 分析

1. 进入规划工作区 `/sessions/[id]`
2. Leader Agent 接收需求，协调其他 Agent 进行分析
3. 可在聊天区继续补充需求和约束
4. Agent 间通过圆桌讨论（Roundtable）碰撞观点
5. 可上传相关文件（支持拖拽和粘贴）

#### 第三步：方案确认

1. Agent 团队生成 `proposal.md` 方案文档
2. 在工作区中预览和确认方案
3. 可提出修改意见，Agent 会调整方案

#### 第四步：导出与执行

1. 方案确认后，系统生成 `execution_plan.json`
2. 导出执行计划或获取 CLI 拉取命令
3. 使用本地 CLI 执行计划（参见 [CLI 命令](#5-cli-命令)）
4. 执行结果回传后在 Web 端查看

### 4.3 设置页面

#### 模型设置 (`/settings/models`)

- 添加 LLM Provider（OpenAI / Anthropic / 自定义 OpenAI-compatible）
- 配置 API Key（加密存储）
- 设置默认模型和 Fallback 链
- 测试连接
- 拉取 Provider 支持的模型列表

#### Agent 管理 (`/settings/agents`)

- 查看内置 Agent 模板（Leader、Researcher、Planner、Reviewer、Architect、Developer、Tester）
- 创建自定义 Agent（基于模板复制后修改）
- 配置 Agent 的角色、目标、Skill、允许使用的 Tool

#### Skill 管理 (`/settings/skills`)

- 查看内置 Skill
- 创建自定义 Skill（Markdown + frontmatter 格式）
- 从远程导入 Skill（支持预览 → 审核 → 启用流程）

#### 安全配置 (`/settings/security`)

- `safe_mode`：开启后只允许白名单命令
- 命令黑名单：阻止特定危险命令
- 路径限制：防止路径逃逸
- 敏感文件模式：保护 `.env`、`*.key`、`*.pem` 等文件

---

## 5. CLI 命令

### 基础命令

| 命令 | 功能 |
|------|------|
| `agent-team init` | 交互式初始化向导 |
| `agent-team --help` | 查看帮助 |

### 执行命令

| 命令 | 功能 |
|------|------|
| `agent-team apply` | 导入 `execution_plan.json` 并本地执行 |
| `agent-team pull-plan --plan-id <ID> --server <URL>` | 从服务端拉取执行计划 |
| `agent-team push-result` | 回传执行结果到服务端 |
| `agent-team show-result` | 查看本地执行结果（Rich 渲染） |
| `agent-team run-validation` | 运行验证命令 |

### Debug 命令

| 命令 | 功能 |
|------|------|
| `agent-team debug prompt` | 查看发给 LLM 的最终 prompt |
| `agent-team debug messages` | 查看会话最近消息 |
| `agent-team debug replay` | 逐步回放执行计划 |
| `agent-team debug timeline` | 生成 Mermaid 时序图 |

### 典型使用流程

```bash
# 1. 初始化
agent-team init

# 2. 拉取执行计划（从 Web 端获取 plan_id）
agent-team pull-plan --plan-id plan_xxx --server http://localhost:8200

# 3. 执行计划
agent-team apply

# 4. 查看结果
agent-team show-result

# 5. 回传结果
agent-team push-result

# 6. （可选）调试
agent-team debug timeline
```

---

## 6. Agent 系统

系统内置 7 个 Agent：

| Agent | 角色 | 专长 |
|-------|------|------|
| **Leader** | coordinator | 需求分析 → 方案生成 → 计划生成，三阶段驱动 |
| **Researcher** | researcher | 技术调研、可行性分析、风险识别 |
| **Planner** | planner | 方案分解为任务、依赖分析 |
| **Reviewer** | reviewer | 方案/代码审查、质量保证 |
| **Architect** | architect | 架构设计、技术选型 |
| **Developer** | developer | 编码实现、API 设计、数据库建模 |
| **Tester** | tester | 测试设计、自动化测试、功能验证 |

### Agent 核心能力

- **Agent Card 能力广播**：Agent 向系统声明自己的能力
- **子 Agent 协作请求**：Agent 之间可以发送协作请求
- **Leader 中断/指令干预**：Leader 可以追加控制消息干预流程
- **流式输出 + 截断续写**：长输出自动检测截断并续写
- **Tool 执行**：Agent 可以调用 Tool 完成具体操作
- **动态创建**：通过 AgentFactory 按模板动态创建 Agent

### Planning Session 状态机

```
CREATED → PLANNING → AWAITING_APPROVAL → READY_FOR_EXPORT → COMPLETED
                    ↓ (任何阶段)          ↓
                 CANCELLED             FAILED
```

### Roundtable 规则

- 默认限定轮数（配置在 `config.yaml` 的 `max_roundtable_rounds`）
- 每轮结束自动生成摘要
- 到达轮数阈值后自动收束
- 可提前因共识达成而结束
- 不直接进入本地执行，需显式转入 Planning Session

---

## 7. Tool 系统

系统内置 9 个 Tool：

| Tool | 风险级 | 功能 | 特殊说明 |
|------|--------|------|----------|
| `file_read` | LOW | 读取文件 | |
| `file_write` | MEDIUM | 写入文件 | |
| `file_list` | LOW | 列出目录 | |
| `file_delete` | MEDIUM | 删除文件 | 需 confirm，路径安全检查 |
| `shell_execute` | HIGH | 执行命令 | 超时控制、黑名单拦截 |
| `web_search` | LOW | 网络搜索 | |
| `send_message` | LOW | Agent 间发消息 | |
| `ask_human` | LOW | 请求人类确认 | |
| `git_command` | MEDIUM | Git 操作 | 受保护分支阻断、禁止 force push |

### Tool 安全机制

- **三级风险映射**：LOW / MEDIUM / HIGH
- **审批检查**：HIGH 风险 Tool 需要人工确认
- **执行超时**：通过 `max_command_timeout` 配置
- **标准化输出**：所有 Tool 返回统一的 `ToolResult`
- **动态注册/注销**：支持运行时注册和注销 Tool

---

## 8. 安全机制

安全优先级（从高到低）：

1. **硬安全约束**：工作目录限制、路径逃逸防护、敏感文件保护、黑名单命令
2. **Tool 风险级别**：LOW / MEDIUM / HIGH
3. **Session 人工介入模式**：自动/手动审批
4. **单次动作授权**：用户对单次高风险操作的确认

规则：
- 低层配置只能收紧高层安全限制，不能放宽
- 即使在 `auto` 模式，高风险操作仍需审批
- 即使关闭 `safe_mode`，破坏性操作仍触发硬确认或硬拦截

### Workspace 安全

CLI 执行受 `WorkspacePolicy` 约束：
- 命令黑名单
- 路径逃逸防护
- 敏感文件保护
- `safe_mode` 白名单

### API Key 安全

- API Key 使用 Fernet 对称加密存储
- 加密密钥通过 `ENCRYPTION_KEY` 环境变量配置
- 生成密钥：`python -c "import secrets; print(secrets.token_urlsafe(32))"`

---

## 9. 配置说明

### 环境变量 (`.env`)

```bash
# 服务器
HOST=0.0.0.0
PORT=8200                    # 一键脚本会自动覆盖此值
DEBUG=true

# 数据库
DATABASE_URL=sqlite+aiosqlite:///./data/team_agent.db

# 加密密钥（用于 API Key 加密存储）
ENCRYPTION_KEY=              # 必须填写

# CORS（逗号分隔，一键脚本会自动覆盖）
CORS_ORIGINS=["http://localhost:3200"]

# LLM Provider
DEFAULT_LLM_PROVIDER=openai
DEFAULT_LLM_MODEL=gpt-4o-mini

# 预算
DEFAULT_SESSION_BUDGET_USD=10.0
```

### YAML 配置 (`config.yaml`)

```yaml
# 治理类配置：只能收紧，不能放宽
governance:
  safe_mode: false
  command_blacklist:
    - "rm -rf /"
    - "rm -rf ~"
    - "mkfs"
    - "dd if=/dev/zero"
    - ":(){ :|:& };:"
    - "chmod -R 777 /"
    - "chown -R"
    - "> /dev/sda"
    - "mv / /dev/null"
  protected_paths:
    - /etc
    - /root
    - ~/.ssh
    - /var
    - /sys
    - /proc
  sensitive_file_patterns:
    - .env
    - "*.key"
    - "*.pem"
    - "*.p12"
    - "*.pfx"
    - id_rsa
    - id_ed25519
    - credentials.json
    - "service-account*.json"
  max_command_timeout: 300
  auto_approve: false
  high_risk_requires_approval: true

# 行为类配置：可按层覆盖
behavior:
  fallback_chain: []
  session_budget_usd: 10.0
  stream: true
  temperature: 0.7
  max_tokens: 4096
  max_continuation_rounds: 3
  max_roundtable_rounds: 5
```

### 前端环境变量 (`frontend/.env.local`)

一键启动脚本会自动创建此文件：

```bash
NEXT_PUBLIC_API_URL=http://localhost:8200
NEXT_PUBLIC_BACKEND_PORT=8200
```

---

## 10. 常见问题

### Q: 端口被占用怎么办？

一键脚本会自动检测并递增端口。如果手动启动，修改启动命令中的 `--port` 参数即可。

### Q: 启动后前端无法连接后端？

1. 检查后端是否正常启动（访问 `http://localhost:<端口>/api/health`）
2. 检查 `frontend/.env.local` 中的 `NEXT_PUBLIC_BACKEND_PORT` 是否与后端端口一致
3. 检查后端 CORS 配置是否包含前端地址

### Q: CLI 执行时提示安全拦截？

1. 检查 `config.yaml` 中的 `governance` 配置
2. 确认命令不在 `command_blacklist` 中
3. 确认目标路径不在 `protected_paths` 中
4. 高风险操作需要手动确认

### Q: 如何查看 LLM 调用详情？

1. Web 端：访问 `/usage` 页面查看用量统计
2. CLI：使用 `agent-team debug prompt` 查看发给 LLM 的 prompt
3. 数据库：查询 `llm_calls` 表

### Q: 如何添加自定义 Agent？

1. 访问 `/settings/agents`
2. 选择一个内置 Agent 作为模板
3. 复制并修改角色、目标、Skill 等配置
4. 保存后即可在会话中使用

### Q: 一键脚本的进程没有正常退出？

1. 运行 `./start.sh stop` 强制停止
2. 或手动清理：`rm -rf .run/`
3. 如果进程仍在，使用 `lsof -i :8200` / `lsof -i :3200` 找到进程并 `kill`

### Q: 如何重置数据库？

删除 `backend/data/` 目录后重启后端即可。后端启动时会自动重新初始化。

---

## 相关文档

- [需求文档](requirements.md)
- [MVP 技术设计](mvp-technical-design.md)
