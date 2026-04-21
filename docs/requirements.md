# Team Agent 需求文档

## 1. 文档目标

本文档是 Team Agent 的叙述性主需求文档，回答三个问题：

1. 这个系统要解决什么问题。
2. MVP 先做什么，不做什么。
3. 各模块的边界、状态、权限和技术取舍是什么。

结构化条目、优先级和唯一需求编号以 `docs/functional-requirements.md` 为准；本文档负责解释设计意图、阶段划分和实现边界。

---

## 2. 产品目标与范围

### 2.1 产品目标

Team Agent 是一个面向本地开发与工程协作场景的多 Agent 框架，目标是让用户在一个 Session 中，把一个较复杂的任务交给一个由 Leader 和多个子 Agent 组成的团队完成。

系统需要具备以下能力：

- 以 Session 为单位组织任务、上下文、产物和状态。
- 由 Leader 负责规划、分配、汇总和用户交互。
- 由子 Agent 负责执行具体工作，如搜索、编码、审查、总结。
- 允许 Agent 使用 Tool 操作本地工作空间，并在安全边界内调用外部模型。
- 保留执行轨迹、关键状态和结果摘要，便于恢复、审计和复盘。

### 2.2 MVP 范围

MVP 的目标不是一次性做出完整终局架构，而是跑通一个稳定的最小闭环：

用户输入任务 -> Leader 规划 -> 用户确认 -> 子 Agent 串行执行 -> 结果交付 -> Session 归档

MVP 默认采用以下边界：

- 单机单进程
- `asyncio` 协程运行时
- `InMemoryMessageBus`
- CLI-first
- SQLite 持久化
- Markdown + DB 两层记忆
- 串行为主的任务执行模型
- 最小可用的 Tool 集合

### 2.3 非目标

以下能力不属于 MVP 默认范围：

- 生产级消息中间件
- 完整 Web 管理后台
- 向量检索与复杂长期记忆
- 多模态全链路上传与解析
- 自动 Git 工作流
- 插件市场与第三方生态
- Docker/容器级隔离执行
- 大规模并行 DAG 调度

这些能力可以在第二阶段按真实使用需求逐步引入。

---

## 3. 核心术语与域模型

### 3.1 Session

Session 是一次用户任务执行的边界，包含：

- 用户目标
- 当前计划
- 参与 Agent 列表
- 任务状态
- 关键消息
- 附件与输出产物
- Token 与成本统计
- 归档摘要

Session 是上下文、资源和审计的顶层单位。

### 3.2 Task

Task 是 Leader 在 Session 内拆解出的一个可执行工作单元。Task 应至少包含：

- `task_id`
- `session_id`
- `title`
- `description`
- `status`
- `assigned_agent`
- `dependencies`
- `artifacts`
- `priority`
- `assignment_version`
- `approval_required`
- `result_summary`

Task 是调度、重试、恢复、可观测性的核心对象。

### 3.3 Agent

Agent 是一个具备角色、模型、技能和工具权限的执行单元。MVP 阶段每个 Agent 运行在独立 `asyncio.Task` 中，对外暴露统一接口：

- `send()`
- `receive()`
- `execute()`

### 3.4 Message

Message 是 Agent 之间或用户与系统之间的通信对象。Message 需要包含最小可靠性语义：

- `message_id`
- `session_id`
- `seq`
- `sender`
- `receiver`
- `message_type`
- `category`
- `content`
- `attachments`
- `dedupe_key`
- `ack_at`
- `retry_count`
- `created_at`

### 3.5 Attachment / Artifact

- `Attachment` 指用户输入给 Session 的文件。
- `Artifact` 指 Agent 在执行过程中产生的输出，如代码文件、报告、截图分析结果、检查报告。

二者都应采用统一元数据模型管理，至少包含：

- `id`
- `source`
- `path`
- `mime_type`
- `size_bytes`
- `created_by`
- `related_task_id`
- `checksum`
- `retention_policy`

### 3.6 Checkpoint

Checkpoint 是为恢复、展示和审计而记录的执行快照。MVP 默认记录业务级 Checkpoint，Tool 级 Checkpoint 作为调试增强能力。

---

## 4. 系统架构与运行时边界

### 4.1 总体架构

MVP 架构采用：

- 一个 Leader
- 多个子 Agent
- 一个 MessageBus
- 一个 Workspace 抽象
- 一个 Memory 层
- 一个 LLM Router

逻辑流：

User -> Leader -> Plan -> Task -> Agent -> Tool / LLM -> Result -> Leader -> User

### 4.2 Agent 运行时

MVP 阶段采用 `asyncio` 协程运行时：

- 每个 Agent 是一个独立 `asyncio.Task`
- 单个 Agent 异常不应拖垮整个 Session
- 由 `AgentSupervisor` 负责捕获异常、记录状态、上报 Leader

扩展方向：

- `ProcessAgent`
- `ContainerAgent`

但这两者不进入 MVP 默认范围。

### 4.3 MessageBus

MVP 使用 `InMemoryMessageBus`：

- 普通消息走内存队列
- 关键状态消息写 DB 后投递
- MessageBus 负责分配全局单调递增 `seq`

可靠性原则：

- 关键消息采用至少一次投递
- 接收端必须幂等消费
- 重放消息不能导致重复执行同一 Task assignment

### 4.4 Workspace

Workspace 是 Agent 操作文件系统和命令执行的统一抽象。MVP 使用 `LocalWorkspace`，但必须受安全策略约束：

- 工作目录限制
- 路径逃逸防护
- 敏感文件保护
- 命令黑名单
- `safe_mode`

### 4.5 Memory

MVP 采用两层记忆：

- Layer 1: Markdown，只读的人写约定和角色文件
- Layer 2: 数据库，存储会话、任务、消息、日志和摘要

Layer 3 向量检索保留到第二阶段。

### 4.6 LLM Router

LLM Router 负责在统一接口下调用不同 Provider，并处理：

- 模型路由
- 重试
- fallback
- token 统计
- 成本预算
- 流式或完整模式

---

## 5. 核心行为定义

### 5.1 Session 状态机

统一采用以下状态机：

`CREATED -> PLANNING -> AWAITING_APPROVAL -> EXECUTING -> COMPLETED`

分支状态：

- `EXECUTING -> PAUSED -> EXECUTING`
- `PLANNING / AWAITING_APPROVAL / EXECUTING -> CANCELLED`
- `PLANNING / AWAITING_APPROVAL / EXECUTING -> FAILED`

状态说明：

- `CREATED`: Session 已建立，等待任务输入或初始化完成。
- `PLANNING`: Leader 正在拆解任务和生成计划。
- `AWAITING_APPROVAL`: 等待用户确认计划或高风险操作。
- `EXECUTING`: Agent 正在执行任务。
- `PAUSED`: 执行冻结，可后续恢复。
- `COMPLETED`: 任务已完成并交付。
- `FAILED`: 不可恢复错误导致终止，但应尽量保留已完成成果。
- `CANCELLED`: 用户主动取消。

### 5.2 Task 状态机

Task 状态机定义为：

`PENDING -> READY -> ASSIGNED -> RUNNING -> COMPLETED`

可选中间/终止状态：

- `BLOCKED`
- `WAITING_APPROVAL`
- `FAILED`
- `CANCELLED`
- `SKIPPED`

规则：

- Leader 负责 `READY / ASSIGNED / CANCELLED / SKIPPED`
- Agent 负责上报 `RUNNING / COMPLETED / FAILED / BLOCKED`
- 人类审批影响 `WAITING_APPROVAL` 的流转

### 5.3 上下文共享规则

MVP 不采用“所有 Agent 共享完整上下文”的做法，而采用分层共享：

- Session 级共享：用户目标、当前计划、步骤摘要、产物引用、必要结论
- Agent 私有：working context、最近消息、执行中的局部推理材料
- Agent 间协作：只共享完成当前协作所需的最小上下文

这样做的目标是避免：

- 上下文膨胀
- Agent 间互相污染
- 单个错误输出扩散到整个团队

### 5.4 协作规则

MVP 采用以下协作边界：

- 每个 Agent 同时只有一个主任务
- 协作请求不能抢占主任务，只能轻量回复或进入队列等待处理
- 复杂协作应由 Leader 转成正式子任务
- Leader 的介入方式是追加控制消息，而不是拦截已投递消息

### 5.5 审批与安全优先级

当多个控制机制同时生效时，统一按以下优先级裁决：

1. 硬安全约束
   - 工作目录限制
   - 路径逃逸防护
   - 敏感文件保护
   - 黑名单命令
2. Tool 风险级别
3. Session 的人工介入模式
4. 用户对单次动作的局部授权

规则：

- 低层配置只能收紧高层安全限制，不能放宽
- 即使在 `auto` 模式，高风险操作仍可要求审批
- 即使关闭 `safe_mode`，破坏性操作仍应触发硬确认或硬拦截

### 5.6 Agent 活性检测

系统应记录 Agent 的：

- `heartbeat_at`
- `last_progress_at`
- `current_task_id`
- `status`

判定无响应时，Leader 需要依据状态采取：

- 继续等待
- 重试
- 转交其他 Agent
- 标记失败并通知用户

---

## 6. 模块化需求说明

### 6.1 通信系统

通信模型采用 Leader + 子 Agent 架构。

MVP 约束如下：

- Leader 可以给子 Agent 分配任务和发送控制消息
- 子 Agent 可以给 Leader 汇报状态、结果和阻塞原因
- 子 Agent 间允许协作请求，但不允许互相下达 command
- 关键消息要持久化，普通协作消息可仅保存在内存

第二阶段可扩展：

- 子团队委派
- 异步仲裁
- 消息重放
- 外部消息中间件

### 6.2 任务编排

Leader 负责：

- 将用户目标拆成 Task
- 生成执行顺序
- 分配给合适 Agent
- 在执行过程中根据结果动态调整

MVP 默认采用串行执行优先：

- 先保证流程闭环与状态可控
- 并行 DAG 调度在第二阶段引入

### 6.3 LLM 调用层

MVP 要解决的是“统一调用”和“失败可恢复”，而不是一次性兼容所有高级能力。

MVP 建议：

- 优先支持 OpenAI / Anthropic 两类 Provider
- 支持统一接口
- 支持 retry + fallback
- 支持 token 与成本统计
- 支持完整模式与流式模式

第二阶段再补：

- 更多 Provider
- 更复杂的结构化输出策略
- Prompt A/B 实验
- 更细粒度限流与预算策略

### 6.4 Skill 与 Tool 系统

Skill 是任务执行的提示模板和流程知识，Tool 是底层执行能力。

MVP 建议：

- Skill 使用 Markdown + frontmatter
- Tool 使用 Python 函数注册
- 先实现最小 Tool 集：
  - `file_read`
  - `file_write`
  - `file_list`
  - `shell_execute`
  - `web_search`
  - `send_message`
  - `ask_human`

第二阶段再引入：

- 第三方 Tool 包
- Git Tool
- 多模态 Tool
- 热加载优化

### 6.5 Memory

Memory 层原则：

- DB 是结构化数据的 source of truth
- Markdown 是人可读视图与手写约定层
- 向量库是未来的检索增强层，不作为 MVP 依赖

写入路径：

- Agent / Session 运行时写 DB
- 会话归档后生成摘要
- 人写 Markdown 不自动反向同步 DB

### 6.6 Workspace 与安全

安全是架构硬边界，而不是“模型自己注意”。

MVP 应优先落地：

- 工作目录限制
- 路径穿越防护
- 敏感文件保护
- 黑名单命令
- `safe_mode`
- Tool 风险分级
- Prompt Injection 的最小防线：最小权限 + 危险操作硬拦截

### 6.7 可观测性

MVP 重点不在花哨展示，而在“出问题能查”。

MVP 建议记录：

- Session 状态
- Task 状态
- Agent 当前状态
- 关键消息流
- Tool 执行元数据
- LLM 调用元数据

第二阶段再加强：

- 全链路可视化
- 消息时序图
- 复杂 TUI / Web 看板
- 结构化日志外接 ELK / Loki

### 6.8 配置管理

配置系统统一采用 YAML + Pydantic。

建议区分两类配置：

1. 治理类配置
   - 安全
   - 审批
   - 预算
   - 路径限制
   - 只能被收紧，不能被下层放宽
2. 行为类配置
   - 模型
   - Prompt
   - stream
   - fallback
   - 可以按层覆盖

### 6.9 用户文件交互

MVP 只保留 CLI 场景最关键的文件交互方式：

- 路径引用
- `/attach` 命令
- 基础附件模型

第二阶段再做：

- 剪贴板图片粘贴
- Web 拖拽上传
- 文件预览
- 智能多模态路由
- 输出文件交付目录自动化

### 6.10 Git 与外部集成

Git 自动化和外部系统集成都不应进入 MVP 默认路径。

Git 相关建议：

- MVP 仅保留只读状态检查或显式用户触发动作
- 自动 commit、Session 分支、冲突合并策略放到第二阶段
- `auto_commit` 默认不应开启

外部集成建议：

- Webhook、IM、MCP、CI/CD 均保留为第二阶段扩展点

---

## 7. 技术栈建议

### 7.1 MVP 建议技术栈

#### 语言与后端

- Python 3.12
- FastAPI
  - 只保留最小 API 能力，如健康检查、Session 查询、后续远程接入预留
  - MVP 不强调 Web 管理端

#### CLI 与交互

- Typer + Rich
  - 比 Click 更适合构建 CLI-first 的命令与交互式体验
  - 与 Pydantic/FastAPI 生态搭配更自然

#### 配置与数据模型

- Pydantic v2
- PyYAML

#### 持久化

- SQLAlchemy 2.0 或 SQLModel
- SQLite
  - 先以单文件数据库承载 Session、Task、Message、LLM Call、Tool Execution

#### 并发与调度

- `asyncio`
- `InMemoryMessageBus`

#### 质量保障

- pytest
- ruff

### 7.2 第二阶段建议

当 MVP 稳定、真实负载和需求边界明确后，再逐步引入：

- PostgreSQL + pgvector
- Redis Streams 或 NATS
- React + Next.js 管理端
- WebSocket 实时状态推送
- Docker 隔离执行
- 插件系统
- 多模态解析链路
- Webhook / Slack / 飞书 / 邮件通知

### 7.3 暂不建议过早落地的选择

以下技术不要在第一阶段就作为必需品压进实现范围：

- 生产级消息中间件
- 复杂前端栈
- 自动 Git 工作流
- 全量多模态链路
- 插件生态
- 云端 IDE 形态

原因很简单：这些能力会显著提高实现复杂度，但并不决定 MVP 是否能跑通。

---

## 8. MVP 范围与里程碑

### 8.1 MVP 必须跑通的闭环

MVP 必须实现：

- Leader + 2-3 个子 Agent 的基本协作流程
- InMemoryMessageBus
- 基础 LLM 调用层（OpenAI + Anthropic 至少二选一，支持 retry/fallback）
- Markdown Skill 加载
- 最小 Tool 集
- CLI 交互式 REPL
- SQLite 持久化
- Markdown 记忆加载
- 基础安全边界
- Session 与 Task 状态机
- 任务模式完整流程
- 单聊模式

### 8.2 MVP 延后能力

以下能力明确延后：

- 向量检索
- 子团队委派
- 异步仲裁
- 并行 DAG 调度
- Session 暂停/恢复的完整断点续跑
- 多模态能力
- Git 自动集成
- 用户体系与团队模式
- Web 管理界面
- 插件系统
- Docker 沙箱
- 外部通知和 MCP

### 8.3 建议里程碑

| 里程碑 | 目标 | 核心交付 |
|--------|------|----------|
| M1 | 跑通单 Agent 会话 | 基础 CLI、单 Agent、LLM 调用 |
| M2 | 跑通多 Agent 编排 | Leader、MessageBus、Task 流转 |
| M3 | 跑通 Tool 与 Workspace | 文件与命令 Tool、安全边界 |
| M4 | 跑通持久化与归档 | SQLite、Session/Task/Message 记录、摘要归档 |
| M5 | 打磨可观测性与容错 | 日志、错误提示、重试、基础调试命令 |

---

## 9. 建议的最小数据库模型

MVP 建议保留以下核心表：

- `sessions`
- `agents`
- `tasks`
- `messages`
- `attachments`
- `artifacts`
- `tool_executions`
- `llm_calls`
- `checkpoints`
- `memory_entries`

其中最关键的是：

- `sessions` 用于顶层生命周期管理
- `tasks` 用于编排和恢复
- `messages` 用于通信审计与可靠性
- `llm_calls` / `tool_executions` 用于可观测与调试

---

## 10. 文档维护原则

后续维护建议采用以下规则：

- `functional-requirements.md` 是结构化唯一真相源
- `requirements.md` 负责说明原因、边界和阶段划分
- 同一个定义不要在两份文档里分别扩展成不同版本
- 所有新增重要能力都应先明确：
  - 是否属于 MVP
  - 默认是否开启
  - 是否影响状态机、安全边界或数据模型

这样可以确保这份需求文档不仅“看起来完整”，也能直接支撑实现、排期和验收。