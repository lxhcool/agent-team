# Team Agent

面向小团队的 AI 交付经理工作台。

## 定位

Team Agent 不负责写代码，也不负责本地执行开发任务。

它负责把：

- 一句模糊需求
- 已有 PRD / 会议纪要 / 聊天记录
- 现有项目的迭代材料

收敛成一套 `可评审、可确认、可开工` 的阶段化交付成果。

## 当前主线

统一入口：

- 新项目启动
- 现有项目迭代

统一流程：

1. 理解输入
2. 识别缺口
3. 发起澄清
4. 生成阶段产物
5. 评审与修订
6. 阶段确认
7. 形成最终产物总览

## 核心原则

- 不做代码生成
- 不做 CLI 执行
- 不做自动改仓库
- 每个阶段先沉淀产物，再进入下一阶段
- 产物不走固定模板，走稳定骨架 + 动态编排

## 技术栈

- 后端：Python FastAPI + SQLAlchemy + SQLite + SSE
- 前端：Next.js 15 + React 19 + TypeScript + Tailwind CSS 4
- 桌面壳：Electron

## 当前页面

| 页面 | 路由 | 功能 |
|------|------|------|
| 首页 | `/` | 新需求入口，创建工作区 |
| 工作区列表 | `/workspaces` | 查看项目与当前阶段 |
| 工作区详情 | `/workspaces/[id]` | 分阶段推进、修订、确认、查看产物 |
| 模型设置 | `/settings/models` | Provider、API Key、默认模型配置 |
| Agent 管理 | `/settings/agents` | 交付型 Agent 模板管理 |
| Skill 管理 | `/settings/skills` | Skill 管理 |
| 用量统计 | `/usage` | 模型调用与费用统计 |

## 快速开始

### 1. 配置环境

```bash
cp .env.example .env
```

补充 `ENCRYPTION_KEY` 和所需模型 Provider 配置。

### 2. 启动后端

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev
```

### 4. 桌面开发模式

```bash
cd frontend
npm run desktop:dev
```

## 文档

- [产品需求](docs/requirements.md)
- [功能需求](docs/functional-requirements.md)
- [工作区进展](docs/workspace-development-progress.md)
