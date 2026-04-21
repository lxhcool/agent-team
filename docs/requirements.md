# Team Agent 需求文档

## 1. 文档目标

本文档是 Team Agent 的叙述性主需求文档，回答四个问题：

1. 这个系统要解决什么问题。
2. Web 端和本地 CLI 分别负责什么。
3. MVP 先做什么，不做什么。
4. 各模块的边界、状态、权限和技术取舍是什么。

结构化条目、优先级和唯一需求编号以 `docs/functional-requirements.md` 为准；本文档负责解释设计意图、阶段划分和实现边界。

---

## 2. 产品目标与范围

### 2.1 产品目标

Team Agent 是一个 **Web 主导、本地 CLI 执行落地** 的多 Agent 协作产品。

它的核心目标不是只做“讨论型 Agent”，而是跑通一条完整链路：

需求输入 -> 多 Agent 分析 -> 生成方案 -> 导出方案文档 -> 生成执行计划 -> 调用本地 CLI 在项目仓库中落地开发 -> 回传执行结果

系统需要同时服务两类人：

- **非开发角色**：产品、测试、项目负责人，主要使用 Web 端提出问题、确认方案、查看结果
- **开发角色**：开发者，使用 Web 端接收方案和任务，并通过本地 CLI 在本地代码仓库中执行开发

### 2.2 产品形态

系统由两类入口组成：

#### Web 端

Web 是主产品入口，负责：

- 用户输入需求、问题或目标
- 多 Agent 分析、讨论、规划
- 方案展示与确认
- 导出 `proposal.md`
- 生成执行计划
- 发起本地执行
- 查看执行状态、产物、日志和结果
- 审批高风险动作

#### 本地 CLI

CLI 不是主产品入口，而是 **本地代码执行器**，负责：

- 绑定本地项目仓库
- 接收 Web 端生成的执行计划
- 修改本地代码文件
- 运行测试、lint、build 等验证命令
- 生成执行报告和产物
- 将执行结果回传给 Web 端或导出为本地结果文件

### 2.3 MVP 范围

MVP 的目标不是做一个完整平台，而是跑通一个稳定的最小闭环：

Web 输入需求 -> Agent 团队分析和生成方案 -> 导出 Markdown 方案 -> 生成机器可执行计划 -> 本地 CLI 接收计划并在本地仓库执行开发 -> 返回结果摘要和验证结果

MVP 默认采用以下边界：

- Web 为主入口
- 本地 CLI 为执行器
- 单机单进程服务端
- `asyncio` 协程运行时
- `InMemoryMessageBus`
- SQLite 持久化
- Markdown + DB 两层记忆
- 串行为主的任务执行模型
- 最小可用的 Tool 集合
- Web 到 CLI 采用显式交付，而不是远程控制本地终端

### 2.4 非目标

以下能力不属于 MVP 默认范围：

- 生产级消息中间件
- 复杂 Web 后台管理与组织权限系统
- 向量检索与复杂长期记忆
- 多模态全链路上传与解析
- 自动 Git 工作流
- 插件市场与第三方生态
- Docker/容器级隔离执行
- 大规模并行 DAG 调度
- Web 端直接远程实时操控本地 CLI

这些能力可以在第二阶段按真实使用需求逐步引入。

---

## 3. 核心术语与域模型

### 3.1 Planning Session

Planning Session 是 Web 端的分析与规划会话，负责：

- 接收用户目标
- 组织多 Agent 讨论
- 形成方案
- 生成面向人阅读的方案文档
- 生成面向机器执行的执行计划

Planning Session 的核心产物是：

- `proposal.md`
- `execution_plan.json`

### 3.2 Execution Session

Execution Session 是本地 CLI 执行计划时创建的会话，负责：

- 绑定本地项目仓库
- 根据执行计划改动本地代码
- 运行验证命令
- 记录产物和执行结果
- 回传执行摘要

Planning Session 与 Execution Session 通过 `plan_id` / `execution_id` 关联，但职责不同：

- Planning Session 负责“想清楚”
- Execution Session 负责“做出来”

### 3.3 Roundtable Session

Roundtable Session 是一种 **探索性、发散性讨论模式**，不直接承担开发落地主链路。它适用于：

- 头脑风暴
- 方案对比
- 风险识别
- 多角色观点补充

Roundtable 的产出不是直接执行，而是：

- 形成若干候选结论
- 归纳优劣势
- 在必要时转化为正式 Planning Session 的输入

因此，Roundtable 在产品中应被定位为 **辅助模式**，不是 MVP 的核心主路径。

### 3.4 Task

Task 是 Leader 在会话内拆解出的一个可执行工作单元。Task 应至少包含：

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

### 3.5 Message

Message 是 Agent 之间或系统组件之间的通信对象。Message 需要包含最小可靠性语义：

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

### 3.6 Attachment / Artifact

- `Attachment` 指用户在 Web 或 CLI 输入给会话的文件。
- `Artifact` 指 Agent 或 CLI 在执行过程中产生的输出，如方案文档、执行计划、代码文件、检查报告、执行日志。

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

### 3.7 Proposal 协议

系统应将“方案”和“执行协议”拆成两类产物，其中 `proposal.md` 是面向人的正式方案文档。

#### `proposal.md`

给人阅读，面向产品、测试、开发协作，建议固定结构：

- 标题
- 背景
- 问题定义
- 目标
- 范围
- 非范围
- 方案概述
- 实施步骤
- 风险与注意事项
- 验收标准
- 待确认项

#### Proposal 元数据建议

- `proposal_id`
- `session_id`
- `title`
- `status`
- `version`
- `generated_by`
- `created_at`
- `updated_at`

### 3.8 Execution Plan 协议

`execution_plan.json` 给 CLI 执行，面向机器，建议固定字段：

- `plan_id`
- `source_session_id`
- `proposal_id`
- `title`
- `goal`
- `summary`
- `tasks`
- `dependencies`
- `target_paths`
- `constraints`
- `validation_commands`
- `expected_artifacts`
- `approval_requirements`
- `stop_conditions`
- `metadata`

#### Execution Plan 中的 task 对象建议字段

- `task_id`
- `title`
- `description`
- `owner_role`
- `inputs`
- `target_paths`
- `steps`
- `validation_commands`
- `expected_artifacts`
- `done_definition`
- `risk_level`

### 3.9 Execution Result 协议

CLI 执行后应生成结构化执行结果对象，可命名为 `execution_result.json`，建议固定字段：

- `execution_id`
- `plan_id`
- `status`
- `started_at`
- `finished_at`
- `changed_files`
- `validation_results`
- `artifacts`
- `error_summary`
- `result_summary`
- `follow_up_suggestions`

#### validation_results 中的字段建议

- `name`
- `command`
- `status`
- `duration_ms`
- `summary`
- `log_ref`

### 3.10 Agent Template 与 Skill Metadata

为了支持“预置 + 自定义 + 导入”，需要尽早固定 Agent 和 Skill 的元数据字段。

#### Agent Template 建议字段

- `name`
- `display_name`
- `description`
- `role`
- `goal`
- `model`
- `skills`
- `capabilities`
- `allowed_tools`
- `constraints`
- `participation_modes`
  - planning
  - roundtable
  - execution
  - review
- `risk_level`
- `version`

#### Skill frontmatter 建议字段

- `name`
- `display_name`
- `description`
- `version`
- `author`
- `source`
- `tools`
- `recommended_for`
- `output_format`
- `tags`
- `safety_notes`

其中：

- `output_format` 默认建议为 `markdown`
- `source` 用于区分 `builtin / custom / imported`
- `tools` 只声明依赖，不直接授予权限

### 3.11 Checkpoint

Checkpoint 是为恢复、展示和审计而记录的执行快照。MVP 默认记录业务级 Checkpoint，Tool 级 Checkpoint 作为调试增强能力。

---

## 4. 系统架构与运行时边界

### 4.1 总体架构

MVP 架构采用：

- 一个 Web App
- 一个 Orchestrator / Session 服务
- 一个 Leader
- 多个子 Agent
- 一个 MessageBus
- 一个 Memory 层
- 一个 LLM Router
- 一个本地 CLI 执行器
- 一个 Workspace 抽象

逻辑流：

User -> Web -> Leader / Agents -> Proposal + Execution Plan -> Local CLI -> Local Workspace -> Result -> Web

### 4.2 Agent 运行时

服务端 Agent 在 MVP 阶段采用 `asyncio` 协程运行时：

- 每个 Agent 是一个独立 `asyncio.Task`
- 单个 Agent 异常不应拖垮整个会话
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

### 4.4 Local CLI Executor

CLI 的本质是本地执行代理，不负责主规划。它负责：

- 导入 `execution_plan.json` 或通过计划 ID 拉取执行计划
- 绑定本地项目路径
- 在本地项目中执行文件修改和命令验证
- 生成执行结果对象
- 将结果回传给服务端或导出为本地结果包

MVP 不要求 Web 直接远程操控本地 shell。

### 4.5 Workspace

Workspace 是 CLI 或受控 Agent 操作文件系统和命令执行的统一抽象。MVP 使用 `LocalWorkspace`，但必须受安全策略约束：

- 工作目录限制
- 路径逃逸防护
- 敏感文件保护
- 命令黑名单
- `safe_mode`

### 4.6 Memory

MVP 采用两层记忆：

- Layer 1: Markdown，只读的人写约定和角色文件
- Layer 2: 数据库，存储会话、任务、消息、日志和摘要

Layer 3 向量检索保留到第二阶段。

### 4.7 LLM Router

LLM Router 负责在统一接口下调用不同 Provider，并处理：

- 模型路由
- 重试
- fallback
- token 统计
- 成本预算
- 流式或完整模式

---

## 5. 核心行为定义

### 5.1 Planning Session 状态机

统一采用以下状态机：

`CREATED -> PLANNING -> AWAITING_APPROVAL -> READY_FOR_EXPORT -> COMPLETED`

分支状态：

- `PLANNING / AWAITING_APPROVAL / READY_FOR_EXPORT -> CANCELLED`
- `PLANNING / AWAITING_APPROVAL / READY_FOR_EXPORT -> FAILED`

状态说明：

- `CREATED`: Planning Session 已建立。
- `PLANNING`: Leader 与 Agent 团队正在分析需求和形成方案。
- `AWAITING_APPROVAL`: 等待用户确认方案或高风险决策。
- `READY_FOR_EXPORT`: 方案已确认，可导出 `proposal.md` 和 `execution_plan.json`。
- `COMPLETED`: 方案阶段完成。
- `FAILED`: 不可恢复错误导致终止。
- `CANCELLED`: 用户主动取消。

### 5.2 Execution Session 状态机

Execution Session 采用以下状态机：

`CREATED -> READY -> EXECUTING -> COMPLETED`

分支状态：

- `EXECUTING -> PAUSED -> EXECUTING`
- `READY / EXECUTING -> CANCELLED`
- `READY / EXECUTING -> FAILED`

Execution Session 更关注本地开发落地和验证结果。

### 5.3 Roundtable Session 规则

Roundtable 应采用“受控发散”而不是“无限讨论”：

- 默认限定轮数
- 每轮结束生成阶段性摘要
- 到达轮数阈值后自动收束
- 可以提前因共识达成而结束
- 默认不直接进入本地执行，需显式转入 Planning Session

Roundtable 的使用建议：

- 用于探索、比较、补充视角
- 不用于直接驱动 CLI 执行
- 输出应被结构化归纳，否则极易造成上下文膨胀和重复讨论

### 5.4 Task 状态机

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
- Agent 或 CLI Executor 负责上报 `RUNNING / COMPLETED / FAILED / BLOCKED`
- 人类审批影响 `WAITING_APPROVAL` 的流转

### 5.5 上下文共享规则

MVP 不采用“所有 Agent 共享完整上下文”的做法，而采用分层共享：

- Planning Session 级共享：用户目标、当前计划、步骤摘要、产物引用、必要结论
- Agent 私有：working context、最近消息、执行中的局部推理材料
- Agent 间协作：只共享完成当前协作所需的最小上下文
- Execution Session：主要消费结构化 `execution_plan.json`，而不是重新理解整段规划历史
- Roundtable：每轮必须产出摘要，用摘要替代原始长讨论

### 5.6 协作规则

MVP 采用以下协作边界：

- 每个 Agent 同时只有一个主任务
- 协作请求不能抢占主任务，只能轻量回复或进入队列等待处理
- 复杂协作应由 Leader 转成正式子任务
- Leader 的介入方式是追加控制消息，而不是拦截已投递消息
- CLI Executor 负责执行，不承担主规划职责

### 5.7 审批与安全优先级

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

### 5.8 Agent 与 CLI 活性检测

系统应记录服务端 Agent 与 CLI Executor 的：

- `heartbeat_at`
- `last_progress_at`
- `current_task_id`
- `status`

判定无响应时，Leader 或执行管理器需要依据状态采取：

- 继续等待
- 重试
- 转交其他 Agent
- 标记失败并通知用户

---

## 6. 模块化需求说明

### 6.1 Web 端

Web 是主入口，应提供：

- 需求输入
- 对话式分析
- 多 Agent 讨论结果展示
- 方案确认
- `proposal.md` 导出
- 执行计划生成
- 执行结果查看
- 审批入口

MVP 不要求复杂项目管理后台，但应先支持“提出需求 -> 生成方案 -> 交付给 CLI”的完整路径。

#### 对话输出格式规则

这是产品体验的一条明确规则：

- **所有面向用户的对话输出默认采用 Markdown 结构化格式**
- 聊天区里的回复不应是一整段无结构纯文本，除非内容非常短
- 对话输出优先使用：
  - 标题
  - 列表
  - 表格
  - 代码块
  - 引用块
  - 加粗重点
- 长回复必须分节展示，至少包含清晰的小节结构
- 导出文档与聊天回复共用同一套 Markdown 语义，但文档会更完整

也就是说：

- **聊天消息正文** 用 Markdown 表达结构
- **导出文档** 用 Markdown 文件承载完整内容
- **机器协议** 仍使用 JSON

### 6.2 执行计划交付

Web 到 CLI 的交付是系统关键桥梁。MVP 推荐支持：

- 下载 `execution_plan.json`
- 或生成一条 CLI 执行命令，由本地 CLI 拉取计划

MVP 不要求本地 CLI 常驻在线接单。

### 6.3 本地执行

CLI 应支持：

- 导入计划
- 绑定本地项目目录
- 执行文件修改
- 运行验证命令
- 输出执行摘要
- 可选回传执行结果

CLI 在 MVP 阶段的职责边界是：

- 以执行计划为主
- 允许本地进行少量交互修正
- 不取代 Web 端的主规划与主协作功能

### 6.4 通信系统

通信模型采用 Leader + 子 Agent 架构。

MVP 约束如下：

- Leader 可以给子 Agent 分配任务和发送控制消息
- 子 Agent 可以给 Leader 汇报状态、结果和阻塞原因
- 子 Agent 间允许协作请求，但不允许互相下达 command
- CLI Executor 与服务端通过计划对象和执行结果对象进行显式交付
- 关键消息要持久化，普通协作消息可仅保存在内存

第二阶段可扩展：

- 子团队委派
- 异步仲裁
- 消息重放
- 外部消息中间件
- CLI 在线接单与实时回传

### 6.5 任务编排

Leader 负责：

- 将用户目标拆成 Task
- 生成执行顺序
- 分配给合适 Agent
- 形成 `proposal.md`
- 形成 `execution_plan.json`
- 在执行过程中根据结果动态调整

MVP 默认采用串行执行优先：

- 先保证流程闭环与状态可控
- 并行 DAG 调度在第二阶段引入

### 6.6 圆桌模式

我对圆桌模式的看法是：**它值得保留，但不能放在主链路，也不能在 MVP 做太重。**

原因：

- 圆桌擅长发散，不擅长收敛
- 没有强约束时容易无限拉长对话
- 对长上下文和记忆压力很大
- 如果直接接入执行链路，容易把模糊结论错误地交给 CLI 落地

因此建议：

- MVP 只保留轻量圆桌
- 默认限定轮数
- 每轮自动摘要
- 结束后必须显式转为 Planning Session
- 不允许圆桌直接触发 Execution Session

### 6.7 LLM 调用层

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

### 6.8 Skill 与 Tool 系统

Skill 是任务执行的提示模板和流程知识，Tool 是底层执行能力。

MVP 建议：

- Skill 使用 Markdown + frontmatter
- Tool 使用 Python 函数注册
- 服务端先实现最小 Tool 集：
  - `file_read`
  - `file_write`
  - `file_list`
  - `shell_execute`
  - `web_search`
  - `send_message`
  - `ask_human`

CLI 端应额外具备对本地项目执行开发的能力，但必须受 Workspace 安全约束。

#### 我对 Skill 来源的建议

Skill 非常适合做成可配置资源，但要区分来源和信任级别。

MVP 推荐支持三类来源：

1. **内置 Skill**
   - 由系统预置
   - 质量和格式最稳定
   - 适合作为默认模板

2. **用户自建 Skill**
   - 用户在 Web 或本地文件中自行创建
   - 适合团队内部工作流和角色定制
   - 应支持版本、描述、适用场景和所需 Tool 声明

3. **导入 Skill**
   - 从外部来源导入，如 GitHub 仓库、团队共享库或技能模板包
   - 适合复用社区模板和优秀 prompt 设计
   - 但必须有信任和审核边界，不能无条件直接启用

#### Skill 导入机制建议

如果支持从 GitHub 等外部来源导入，我建议采用“导入 -> 预览 -> 审核 -> 启用”的流程，而不是直接执行：

- 导入 Skill 文件或 Skill 包
- 展示 Skill 的元信息：
  - name
  - description
  - version
  - author / source
  - required tools
  - recommended scenarios
- 检查格式是否合法
- 标记需要的 Tool 和潜在风险
- 用户确认后再启用到团队或 Agent 上

#### Skill 与权限的边界

Skill 决定“会什么”，Tool / constraints 决定“能做什么”。

也就是说：

- 一个 Agent 可以挂某个 Skill
- 但如果没有对应 Tool 权限，它仍然不能执行高风险动作
- 导入外部 Skill 不应自动提升 Agent 权限

#### 自定义 Agent 与 Skill 的关系

我建议按阶段演进：

- MVP：提供预置 Agent 模板 + 可挂载多个 Skill
- P1：支持用户复制模板创建自定义 Agent，并配置 Skill
- P2：再考虑更自由的 Agent 设计与 Skill 市场

这样既能满足“自建 + 导入”的诉求，又不会让系统在早期就变得过于不可控。

### 6.9 Memory

Memory 层原则：

- DB 是结构化数据的 source of truth
- Markdown 是人可读视图与手写约定层
- 向量库是未来的检索增强层，不作为 MVP 依赖

写入路径：

- 服务端会话运行时写 DB
- 会话归档后生成摘要
- 人写 Markdown 不自动反向同步 DB

记忆写入策略建议：

- **Planning Session**：保留目标、约束、结论和最终方案，不保留所有中间噪声
- **Roundtable Session**：只保留每轮摘要和最终结论，不保留完整发散文本作为长期记忆
- **Execution Session**：保留执行结果、验证结果、产物引用和可复用经验
- **失败案例**：保留失败原因和修复建议，但避免无意义日志灌入长期记忆

检索策略建议：

- MVP 不自动全量检索历史记忆
- 仅在规划、方案复用、执行失败重试等明确场景按需检索
- 记忆优先返回“结论摘要”，而不是原始长对话

### 6.10 长输出与截断处理

这是你这个系统里非常重要的一块，尤其在：

- 多 Agent 讨论
- 方案生成
- Roundtable
- CLI 执行日志
- 长代码/长报告输出

建议采用四层处理：

#### 1. 预防

- 对大输出任务先列结构，再分块生成
- 方案文档和执行计划分离，避免单次输出过大
- Roundtable 每轮结束强制摘要，不积累无限原始文本

#### 2. 检测

- 检测 `finish_reason == length`、`max_tokens` 等截断信号
- 对 JSON、Markdown、代码块做结构完整性检测

#### 3. 恢复

- 短输出截断：自动续写，限制最大续写次数
- 长结构化输出：从最后一个完整逻辑单元继续生成
- 超过限制仍不完整：标记为 partial，并提示用户查看已生成部分

#### 4. 展示

- Web：长文本默认折叠，先展示摘要，再按段展开
- CLI：长输出默认折叠，代码/报告优先写文件并展示路径
- 执行日志：优先按步骤摘要展示，不直接把原始 stdout 全量塞到主对话

### 6.11 Workspace 与安全

安全是架构硬边界，而不是“模型自己注意”。

MVP 应优先落地：

- 工作目录限制
- 路径穿越防护
- 敏感文件保护
- 黑名单命令
- `safe_mode`
- Tool 风险分级
- Prompt Injection 的最小防线：最小权限 + 危险操作硬拦截

### 6.12 可观测性

MVP 重点不在花哨展示，而在“出问题能查”。

MVP 建议记录：

- Planning Session 状态
- Execution Session 状态
- Task 状态
- Agent 当前状态
- CLI Executor 当前状态
- 关键消息流
- Tool 执行元数据
- LLM 调用元数据
- 本地执行验证结果
- 长输出是否被截断、是否自动续写、最终是否完整

### 6.13 配置管理

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

### 6.14 用户文件交互

MVP 的文件交互应同时考虑 Web 与 CLI：

- Web：支持上传需求相关文件、导出 `proposal.md`
- CLI：支持导入 `execution_plan.json`、绑定本地目录、输出执行结果文件

第二阶段再做：

- 剪贴板图片粘贴
- 更丰富的 Web 文件预览
- 智能多模态路由
- 自动化产物中心

### 6.15 Git 与外部集成

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

#### Web 与服务端

- Python 3.12
- FastAPI
- Server-Sent Events 或 WebSocket（二选一，优先选实现更简单的实时推送方式）

#### Web 前端

- Next.js + React + TypeScript
- Tailwind CSS
- 轻量组件库（如 shadcn/ui）

#### 本地 CLI

- Typer + Rich
  - CLI 负责导入/拉取计划、执行本地开发、输出执行摘要

#### 配置与数据模型

- Pydantic v2
- PyYAML

#### 持久化

- SQLAlchemy 2.0 或 SQLModel
- SQLite
  - 先以单文件数据库承载 Planning Session、Execution Session、Task、Message、LLM Call、Tool Execution

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
- 本地 CLI 登录接单
- Web 与 CLI 的实时任务同步
- Docker 隔离执行
- 插件系统
- 多模态解析链路
- Webhook / Slack / 飞书 / 邮件通知

### 7.3 暂不建议过早落地的选择

以下技术不要在第一阶段就作为必需品压进实现范围：

- 生产级消息中间件
- 复杂组织权限系统
- 自动 Git 工作流
- 全量多模态链路
- 插件生态
- 云端 IDE 形态
- Web 直接远控本地 CLI

原因很简单：这些能力会显著提高实现复杂度，但并不决定 MVP 是否能跑通。

---

## 8. MVP 范围与里程碑

### 8.1 MVP 必须跑通的闭环

MVP 必须实现：

- Web 需求输入与分析
- Leader + 2-3 个子 Agent 的基本协作流程
- 方案确认
- `proposal.md` 导出
- `execution_plan.json` 生成
- 本地 CLI 导入/拉取执行计划
- CLI 绑定本地项目并执行开发
- 基础验证命令执行与结果摘要
- SQLite 持久化
- Markdown 记忆加载
- 基础安全边界
- Planning Session 与 Execution Session 状态机
- 长输出截断检测与基础续写能力

### 8.2 MVP 延后能力

以下能力明确延后：

- 向量检索
- 子团队委派
- 异步仲裁
- 并行 DAG 调度
- Session 暂停/恢复的完整断点续跑
- 重型圆桌模式
- 多模态能力
- Git 自动集成
- 多用户权限系统
- 本地 CLI 常驻接单
- Web 实时控制本地执行器
- 插件系统
- Docker 沙箱
- 外部通知和 MCP

### 8.3 建议里程碑

| 里程碑 | 目标 | 核心交付 |
|--------|------|----------|
| M1 | 跑通 Web 规划链路 | Web 输入、Leader/Agent 分析、方案输出 |
| M2 | 跑通方案导出 | `proposal.md` 与 `execution_plan.json` |
| M3 | 跑通本地执行链路 | CLI 导入计划、绑定项目、执行修改 |
| M4 | 跑通验证与回传 | 测试/lint/build 结果、执行摘要 |
| M5 | 打磨安全、记忆与可观测性 | 日志、错误提示、审批、截断恢复、基础调试 |

---

## 9. 建议的最小数据库模型

MVP 建议保留以下核心表：

- `planning_sessions`
- `execution_sessions`
- `roundtable_sessions`
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

- `planning_sessions` 用于方案阶段生命周期管理
- `execution_sessions` 用于本地执行阶段生命周期管理
- `roundtable_sessions` 用于探索性讨论和中间摘要
- `tasks` 用于编排和恢复
- `messages` 用于通信审计与可靠性
- `artifacts` 用于统一管理 `proposal.md`、`execution_plan.json`、`execution_result.json` 和执行产物

---

## 10. 文档维护原则

后续维护建议采用以下规则：

- `functional-requirements.md` 是结构化唯一真相源
- `requirements.md` 负责说明原因、边界和阶段划分
- 同一个定义不要在两份文档里分别扩展成不同版本
- 所有新增重要能力都应先明确：
  - 是否属于 Planning Session、Roundtable Session 还是 Execution Session
  - 是否属于 Web 端还是 CLI 端
  - 是否属于 MVP
  - 默认是否开启
  - 是否影响状态机、安全边界、记忆写入或数据模型

这样可以确保这份需求文档不仅“看起来完整”，也能直接支撑 Web 产品、CLI 执行器和后续实现排期。