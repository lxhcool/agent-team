# Team Agent 需求文档

## 1. 项目概述

一个支持多模型、多 Skill、独立配置的团队协作 Agent 框架。

## 2. 核心需求

### 2.1 Agent 生命周期

- **模式**：混合模式（服务长驻 + 会话级隔离）
- 用户每次任务创建一个会话（Session）
- 会话内 Agent 长驻协作，共享上下文
- 会话结束后上下文归档到记忆系统
- 参考 Kimi 的会话模式：底层长驻，会话级隔离

### 2.2 通信模型

- **架构**：Leader（Orchestrator）+ 多个子 Agent
- **通信规则**：
  - Leader → 子 Agent：直接指令（分配任务、召回）
  - 子 Agent → Leader：汇报（完成、求助、阻塞）
  - 子 Agent ↔ 子 Agent：直接对话（请求协作、交换信息），不需要经过 Leader 中转
  - Leader 广播：全员通知
- **约束**：
  - 子 Agent 之间可以互相对话 ✅
  - 对话内容对 Leader 透明（Leader 监听所有通信）
  - Leader 可以随时介入（发现偏题、冲突时）
  - 子 Agent 不能给别的 Agent 分配任务，只有 Leader 能分配
  - 子 Agent 之间只能"请求协作"，不能"命令"
  - Leader 负责冲突仲裁
- **参考产品**：CrewAI + Google A2A 协议

### 2.3 记忆系统

- **三层记忆架构**：
  - **Layer 1: Markdown（手写层）** — 人写，Agent 读
    - `memory/project.md`：项目约定、技术栈、团队规范
    - `memory/{agent}.md`：Agent 专属知识、角色定义
    - 特点：人可读可编辑、Git 友好
  - **Layer 2: 数据库（结构化层）** — Agent 写，结构化存储
    - 对话历史
    - 任务执行记录（谁做了什么、结果如何）
    - Agent 间消息日志
  - **Layer 3: 向量检索层** — 语义搜索历史经验
    - 知识条目 embedding
    - 支持相似度检索（"之前类似问题怎么解决的？"）
- **数据库引擎可切换**：
  - 开发/轻量场景 → SQLite（零依赖，开箱即用）
  - 生产场景 → PostgreSQL + pgvector（一栈搞定结构化 + 向量检索 + 全文搜索）
  - 配置项：`memory.engine = "sqlite" | "postgresql"`
  - 用抽象层屏蔽差异
- **关键机制**：
  - Agent 启动时自动加载对应 Markdown 文件作为上下文
  - Agent 协作中产生的重要发现写入记忆
  - 会话结束时 Leader 将关键信息归档

### 2.4 Skill 系统

- **Skill 形态**：Markdown 文件（当前主流标准，同 Claude Code / Cursor / Copilot）
- **Skill 与 Tool 的关系**：
  - **Skill（md 文件）** = 提示词 + 知识 + 使用流程 → 用户关心的
  - **Tool（Python 函数）** = 底层执行能力（file_read, code_execute 等）→ 框架内置
  - Skill 在 frontmatter 中声明需要的 tools，如 `tools: [file_read, code_execute]`
- **内置 Skill**：从 GitHub 找成熟的 prompt 模板内置
- **用户自定义 Skill**：放到 `skills/` 目录，自动发现
- **Skill 文件示例**：
  ```markdown
  ---
  name: code_review
  description: 代码审查技能
  tools: [file_read, code_execute]
  ---
  # 代码审查技能
  你是一个资深代码审查专家，请按以下步骤审查代码...
  ```

### 2.5 任务编排

- **LLM 驱动为主**（参考 CrewAI / AutoGen / OpenAI Swarm 的做法）
- **流程**：
  1. Leader 接收任务 → LLM 规划（拆解 + 分配）
  2. 展示方案给用户确认（可配置 `auto_approve=true` 跳过）
  3. 执行过程中 Leader 动态调整
- **可选约束**：支持用户预定义 pipeline 作为约束
  - 例："这个项目必须走 设计→开发→测试 流程"
- **常见规划缓存**：缓存为模板，减少 LLM 调用
- **防死循环**：
  - 设置 `max_iterations`
  - Leader 强制终止权

### 2.6 人类介入

- **可配置模式**：
  - `mode: "auto"` — 全自动，不介入
  - `mode: "supervised"` — 关键节点审批（默认）
  - `mode: "manual"` — 每步确认
- **关键介入点**（supervised 模式下）：
  - 任务规划确认 — Leader 出方案后，用户确认再执行
  - 危险操作审批 — 删除文件、部署上线、发送邮件等
  - 最终交付审查 — 输出结果前，人过一眼
- **中间过程不介入** — 搜索资料、写代码草稿等

### 2.7 容错机制

- **Checkpoint 机制**：每步操作落盘，失败可续，内容不丢
  - 开始前 → checkpoint 记录"正在做 X"
  - 执行中 → 中间结果实时写入数据库
  - 完成后 → checkpoint 更新为"X 已完成"
  - 失败了 → checkpoint 标记"X 失败，原因 Y"
- **各故障场景策略**：
  - LLM API 调用失败 → 指数退避重试（1s→2s→4s→8s，最多3次）→ 切换备用模型（fallback_model）→ 仍失败上报用户
  - Agent 输出异常 → Leader 把之前输出 + 问题描述发给 Agent 重新生成
  - Tool 执行失败 → 报错信息喂回 Agent 修复重跑，最多3轮
  - Agent 间冲突 → Leader 仲裁拍板
  - 死循环 → max_iterations 强制终止，输出已完成的成果，标记未完成部分

### 2.8 上下文管理

- **生成截断**（输出到一半停了）：
  - 检测 finish_reason == "length"
  - 自动续写：已输出内容 + "请继续" 再调 LLM，拼接直到完成
  - 任务拆分：超长输出说明任务太大，Leader 应拆分子任务
- **输入超限**（上下文塞不进去）：
  - 分层压缩策略：
    - 优先级1: System Prompt + Skill 内容 [必须保留]
    - 优先级2: 当前任务指令 [必须保留]
    - 优先级3: 最近 N 轮对话 [完整保留]
    - 优先级4: 更早对话 → 压缩为摘要 [有损保留]
    - 优先级5: Agent 间其他对话 → 只保留结论 [高度压缩]
    - 优先级6: 记忆文件 → 只检索相关片段 [按需加载]
  - 滑动窗口 + 摘要压缩 + 按需检索
- **上下文膨胀**（长时间运行）：
  - 每完成一个关键步骤 → 成果写入 checkpoint + 生成摘要
  - 新一轮对话用摘要替代历史，"清空缓存但保留结论"
- **核心原则**：永远不让 LLM 看到所有历史，只给当前需要的信息

### 2.9 交互模式

用户有三种交互方式：

| 模式 | 场景 | 流程 |
|------|------|------|
| **任务模式** | 有明确任务要完成 | 用户 → Leader 编排 → Agent 团队执行 |
| **单聊模式** | 探索性、针对性讨论 | 用户 → 指定某个 Agent 自由对话 |
| **圆桌模式** | 头脑风暴、多视角讨论 | 用户 → 拉多个 Agent 在聊天室自由发言，无 Leader 编排 |

- **单聊模式**：用户可选择任意 Agent 直接对话，如"找 Researcher 聊聊技术方案"
- **圆桌模式**：多个 Agent 自由讨论，没有 Leader 编排，所有参与 Agent 平等发言
- **模式转换**：讨论出成果后，可以把圆桌结论交给 Leader 转为正式任务执行

### 2.10 用户体系

- **认证方式**：账号密码 + API Key（方便程序调用）
- **数据隔离**：每个用户数据完全独立
  - Agent 配置
  - 会话历史
  - 记忆文件
  - Skill 库
  - 任务记录
  - **LLM API Key**
- **LLM Key 管理**：
  - 每个用户在 Web 界面"设置→模型配置"中配置自己的 Key
  - 界面支持：添加/编辑/删除 Provider、填写 Key、选择默认模型、测试连通性
  - 费用各自承担
  - Key 加密存储（AES-256），日志中脱敏（sk-***...xxx）
  - Agent 配置中引用 provider 名，Key 从用户配置中自动取
  - Agent 级别可覆盖使用不同 Key
  - 平台也可提供统一 Key 作为默认兜底
- **两种使用模式**：
  - **个人模式**：单用户，所有数据私有
  - **团队模式**：多用户共享一个项目空间，可协作
    - 团队创建者 → 管理员（可管理成员、配置）
    - 团队成员 → 共享项目、Agent、记忆，会话各自独立
    - 团队公共资源：公共 Skill、项目记忆
    - 每个人仍用自己的 Key 调用模型
- **扩展预留**：OAuth 登录、公共 Skill 市场、用量配额

### 2.11 可观测性

- **实时状态面板**：
  - 每个 Agent 当前在做什么（空闲/执行中/等待中）
  - 任务整体进度（待做/进行中/已完成）
  - Agent 间通信实时展示
- **执行轨迹**：
  - 每步调用了什么 LLM、消耗多少 token、耗时多少
  - 输入输出完整记录
  - 可回溯任意步骤的详情
- **日志系统**：
  - 分级日志（DEBUG/INFO/WARN/ERROR）
  - 按会话/Agent/任务筛选
  - 方便调试和审计
- **用量统计**：
  - 每个用户的 token 消耗
  - 每个 Agent/模型的调用次数和成本
  - 按时间段统计

### 2.12 工作空间

- **主模式：CLI**（类似 Claude Code）
  - 用户在终端运行，指定项目目录作为工作空间
  - Agent 直接读写本地文件、执行命令
  - 交互方式：终端对话
  - 适合已有本地项目的开发者
- **Workspace 抽象接口**：
  - `read_file(path)` / `write_file(path, content)` / `execute(command)` / `list_files(pattern)`
  - Agent 通过统一接口操作，不关心底层实现
  - 预留扩展：LocalWorkspace / SandboxWorkspace / CloudWorkspace
- **扩展预留**：
  - 远程沙箱模式（Docker 容器隔离）
  - 云端 IDE 模式（集成 Web IDE）

- 支持多模型（OpenAI、Claude、本地模型，可插拔）
- 每个 Agent 支持配置独立的模型
- 每个 Agent 可配备多个 Skill
- 每个 Agent 有独立的提示词
- 用户可自定义创建 Agent
