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
- **技术实现 — 消息总线 + Leader 异步旁听**：
  - 子 Agent 间消息通过总线直达，不经 Leader 中转
  - Leader 使用独立的异步队列旁听所有消息，不阻塞主消息投递
  - 消息流向：Agent A → Bus → Agent B（直达）+ Leader 队列（旁听，只读）
  - 区别于中转模式：Leader 不是必经之路，不能拦截/修改消息，只能被动监听
- **因果一致性策略 — 事后审计 + 全局序列号**：
  - **架构决策**：Leader 旁听采用「事后审计」模式，不要求实时因果一致性
  - 每条消息由 MessageBus 分配全局单调递增序列号 (seq)，保证因果序
  - 子 Agent 间消息直达是同步的，接收者看到的顺序天然正确
  - Leader 旁听走异步队列，可能有延迟；消费时攒一批消息按 seq 排序，还原因果顺序
  - 任务状态变更 (TASK_COMPLETE/FAILED) 直接投递给 Leader，不走旁听队列，无乱序风险
  - Leader 不需要实时感知子 Agent 间每一轮对话顺序；真正关心的是任务级别的状态变化
- **参考产品**：CrewAI + Google A2A 协议
- **Leader 扩展性 — 子团队委派 + 异步仲裁**：
  - **问题**：Leader 承担规划、分配、监听、仲裁、归档，子 Agent 数量增加时成为瓶颈
  - **子团队委派**：Leader 可将一组相关子任务委派给某个子 Agent 管理
    - 该子 Agent 成为"子团队长"（sub_leader），获得对组内成员发送 command 类型消息的权限
    - Leader 通过 `TASK_DELEGATE` 消息委派，指定子团队成员列表
    - 子团队长完成任务后自动归还权限，Leader 仍然旁听子团队内通信
  - **异步仲裁**：冲突仲裁走独立异步队列，不阻塞子 Agent 间正常通信
    - 子 Agent 发送 `ARBITRATION_REQUEST` → 总线路由到仲裁队列
    - Leader 异步消费仲裁队列，返回 `ARBITRATION_RESULT`
    - 仲裁期间子 Agent 可继续其他工作，不阻塞
- **消息权限边界 — 三级分类 + 总线层强制拦截**：
  - **问题**：子 Agent 不能发命令，但技术实现上如何约束？
  - **消息权限分类**（`MessageCategory`）：
    - `command`：指令类（TASK_ASSIGN、INTERRUPT、SHUTDOWN、TASK_DELEGATE、ARBITRATION_RESULT）— 只有 Leader/子团队长能发
    - `request`：请求类（COLLAB_REQUEST/RESPONSE、QUESTION、ARBITRATION_REQUEST）— 子 Agent 间协作请求
    - `inform`：信息类（TASK_COMPLETE、INFO、ANSWER 等）— 无权限限制
  - **总线层强制拦截**：`MessageBus.send()` 在投递前校验 `message.category()`
    - 非 Leader/子团队长发送 command 类型 → 直接拒绝，不投递
    - 这是技术硬约束，不依赖 Agent 自觉

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
    - **触发方式：Agent 主动调用**（非自动触发）
      - 向量检索作为 `memory_search` Tool 注册，Agent 在 Skill 中声明启用
      - Agent 自主判断是否需要参考历史经验，需要时才调用
      - 避免每次都搜导致浪费 token 和上下文膨胀
      - 类似人类：只有遇到不确定的问题时才去翻笔记
    - Skill 中启用方式：`tools: [file_read, memory_search]`
- **数据库引擎可切换**：
  - 开发/轻量场景 → SQLite（零依赖，开箱即用）
  - 生产场景 → PostgreSQL + pgvector（一栈搞定结构化 + 向量检索 + 全文搜索）
  - 配置项：`memory.engine = "sqlite" | "postgresql"`
  - 用抽象层屏蔽差异
- **关键机制**：
  - Agent 启动时自动加载对应 Markdown 文件作为上下文
  - Agent 协作中产生的重要发现写入记忆
  - 会话结束时 Leader 将关键信息归档
- **记忆一致性 — 单向数据流 + DB 为 source of truth**：
  - **写路径规则**（Agent 不直接写 Markdown）：
    - Agent 写 → DB（唯一写入入口）
    - DB 变更 → 触发异步同步 → Markdown 摘要（追加）+ 向量库（embedding）
    - 人写 → Markdown（手写层，Agent 只读）
    - 人改 Markdown → 不自动同步到 DB（需要手动 reload 或重启会话）
  - **读路径规则**：
    - Agent 启动时 → 读 Markdown（项目约定 + 历史摘要）
    - 运行时 → 读 DB（当前会话上下文）
    - 需要时 → 搜向量库（通过 `memory_search` Tool）
  - **同步策略**：
    - Markdown 摘要同步：会话结束时 Leader 调用 `archive_session()`，生成摘要写入 Markdown
    - 向量索引同步：DB 写入后异步触发 embedding，不阻塞主流程（最终一致）
    - 轻量同步：任务完成时追加摘要行到 Markdown（不覆盖人写内容）
  - **一致性保证**：
    - DB 是 source of truth（结构化数据，完整记录）
    - Markdown 是人可读的摘要/视图（可能落后于 DB，但不会超前）
    - 向量库是检索索引（最终一致，允许短暂延迟）

### 2.4 Agent 发现与注册

- **Agent 自动发现**：
  - 配置文件驱动：`agents/` 目录下的 YAML/JSON 配置文件，启动时自动扫描加载
  - 每个 Agent 配置文件声明：name、model、skills、system_prompt、capabilities
  - 类似 Skill 的自动发现机制，放到 `agents/` 目录即自动注册
- **Agent Card（能力声明）**：
  - 每个 Agent 启动后向总线注册 Agent Card，广播自身能力
  - Agent Card 内容（参考 Google A2A 协议）：
    - `name`：Agent 名称
    - `description`：角色描述
    - `capabilities`：能做什么（如 "代码审查"、"资料搜索"）
    - `skills`：配备的 Skill 列表
    - `model`：使用的模型
    - `constraints`：限制（如 "只能读文件，不能执行命令"）
  - Leader 规划任务时读取 Agent Card 进行路由决策
  - 其他 Agent 协作请求时也可查询 Agent Card 找到合适的协作对象
- **新 Agent 动态加入**：
  - 运行中可通过 API/CLI 动态注册新 Agent
  - 新 Agent 加入后广播 Agent Card，Leader 和其他 Agent 即刻可知
  - 不需要 Leader 批准（注册是基础设施层，任务是业务层）
  - 但 Agent 能否接到任务取决于 Leader 的路由决策
- **Agent 退出**：
  - 正常退出：广播 `SHUTDOWN`，Leader 重新分配其未完成任务
  - 异常退出：Monitor 超时检测 → Leader 仲裁（重试/转交/跳过）

### 2.5 Skill 系统

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

### 2.6 任务编排

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
- **并发任务执行**：
  - **同一 Session 内支持并行执行多个独立任务**
  - Plan 中每个步骤有 `dependencies` 字段，无依赖的步骤可并行
  - Leader 根据依赖图（DAG）调度，同层级无依赖步骤同时分配给不同 Agent
  - 资源调度策略：
    - 每个 Agent 同一时刻只处理一个任务（独占式，避免上下文混乱）
    - Leader 维护可用 Agent 池，空闲 Agent 优先分配
    - Agent 不够时，任务排队等待；可配置 `max_queue_size`
    - 子团队委派：一组相关任务可委派子团队长统筹，Leader 只管子团队长
  - 并行度控制：`max_parallel_tasks` 配置（默认=Agent 数量，即全并行）
  - 配置示例：
    ```yaml
    orchestration:
      max_parallel_tasks: 3
      max_queue_size: 10
      strategy: "dag"  # dag | pipeline | manual
    ```

### 2.7 人类介入

- **可配置模式**：
  - `mode: "auto"` — 全自动，不介入
  - `mode: "supervised"` — 关键节点审批（默认）
  - `mode: "manual"` — 每步确认
- **关键介入点**（supervised 模式下）：
  - 任务规划确认 — Leader 出方案后，用户确认再执行
  - 危险操作审批 — 删除文件、部署上线、发送邮件等
  - 最终交付审查 — 输出结果前，人过一眼
- **中间过程不介入** — 搜索资料、写代码草稿等

### 2.8 容错机制

- **Checkpoint 机制**：每步操作落盘，失败可续，内容不丢
  - **粒度：业务步骤为主 + Tool 执行为辅**
    - Level 1（默认开启）：业务步骤 Checkpoint — 用户能理解的最小有意义单元
      - 如 "搜索资料" 完成、"写代码" 完成 → 保存结果摘要和产出物路径
    - Level 2（可选，`debug_mode=true` 开启）：Tool 执行 Checkpoint — 细粒度调试
      - 如 `file_read("./main.py")` → 保存读取结果，`execute("npm test")` → 保存执行输出
  - Checkpoint 记录内容：step、status、result_summary、artifacts、时间、token_usage
  - 状态流转：开始前 → "正在做 X" / 完成后 → "X 已完成" / 失败 → "X 失败，原因 Y"
- **各故障场景策略**：
  - LLM API 调用失败 → 指数退避重试（1s→2s→4s→8s，最多3次）→ 切换备用模型（fallback_model）→ 仍失败上报用户
  - Agent 输出异常 → Leader 把之前输出 + 问题描述发给 Agent 重新生成
  - Tool 执行失败 → 报错信息喂回 Agent 修复重跑，最多3轮
  - Agent 间冲突 → Leader 仲裁拍板
  - 死循环 → max_iterations 强制终止，输出已完成的成果，标记未完成部分

### 2.9 上下文管理

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

### 2.10 交互模式

用户有三种交互方式：

| 模式 | 场景 | 流程 |
|------|------|------|
| **任务模式** | 有明确任务要完成 | 用户 → Leader 编排 → Agent 团队执行 |
| **单聊模式** | 探索性、针对性讨论 | 用户 → 指定某个 Agent 自由对话 |
| **圆桌模式** | 头脑风暴、多视角讨论 | 用户 → 拉多个 Agent 在聊天室自由发言，无 Leader 编排 |

- **单聊模式**：用户可选择任意 Agent 直接对话，如"找 Researcher 聊聊技术方案"
- **圆桌模式**：多个 Agent 自由讨论，没有 Leader 编排，所有参与 Agent 平等发言
- **圆桌收敛策略**（无 Leader 时讨论如何结束）：
  - 轮数限制（默认）：配置 `max_rounds=5`，每轮所有 Agent 发言一次，到轮数自动结束
  - 共识检测（可选）：每轮结束后用 LLM 判断是否达成共识，达成则提前结束
  - 用户手动（默认开启）：每轮结束后展示给用户，可继续/结束/追加问题
  - 配置示例：
    ```yaml
    roundtable:
      max_rounds: 5
      consensus_check: false
      user_control: true
    ```
- **模式转换**：讨论出成果后，可以把圆桌结论交给 Leader 转为正式任务执行

### 2.11 用户体系

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

### 2.12 可观测性

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

### 2.13 版本控制集成

- **Git 协作策略**：
  - Agent 修改代码后需要与 Git 协作，避免混乱
- **自动 Commit 策略**：
  - 每完成一个业务步骤（与 Checkpoint 对齐），自动 commit
  - Commit message 由 Agent 生成，格式：`[agent-name] step description`
  - 例：`[coder] feat: add user authentication module`
  - 可配置 `auto_commit: true`（默认）| `manual`
- **分支策略**：
  - 每个 Session 创建独立分支：`agent/session-{id}`
  - 避免直接在 main 分支上操作
  - Session 完成后，用户决定是否合并（类似 PR 流程）
  - 配置：`git.branch_strategy: "session"` | `"feature"` | `"direct"`
- **冲突处理**：
  - Agent 自身产生的冲突：Agent 自主 `git rebase` 或 `git merge` 解决
  - Agent 间产生冲突：Leader 仲裁，指定某个 Agent 负责合并
  - 与人类修改冲突：暂停 Agent，通知用户手动解决
- **安全约束**：
  - 禁止 `git push --force`
  - 禁止直接 push 到 main/master（需通过 PR 或用户确认）
  - `git reset --hard` 需要用户确认
- **配置示例**：
  ```yaml
  git:
    auto_commit: true
    branch_strategy: "session"
    protected_branches: [main, master, production]
    commit_prefix: "[{agent_name}]"
  ```

### 2.14 多模态支持

- **设计原则**：文本为主，多模态为辅，按需扩展
- **输入多模态**（Agent 接收）：
  - **图像**：UI 截图、架构图、错误截图
    - 通过 LLM 的视觉能力处理（GPT-4o / Claude 3.5+ 支持图片输入）
    - 新增 Tool：`image_analyze(path, question)` — 读取图片 + 提问
  - **文档**：PDF、Word、Excel
    - 新增 Tool：`document_parse(path)` → 提取文本/表格/结构
    - PDF 解析：PyMuPDF / pdfplumber
    - Word/Excel：python-docx / openpyxl
  - **音频**：语音备忘、会议录音
    - 新增 Tool：`audio_transcribe(path)` → 转文字
    - 使用 Whisper API 或本地模型
- **输出多模态**（Agent 生成）：
  - **图表**：Mermaid / PlantUML 生成架构图、流程图
  - **文件**：生成 PDF 报告、Excel 数据表
- **Skill 声明方式**：
  - 在 Skill 的 frontmatter 中声明需要的多模态 tools
  - 例：`tools: [file_read, image_analyze, document_parse]`
  - Agent 只在 Skill 需要时才具备多模态能力
- **LLM 适配**：
  - 不同模型的多模态能力不同，Router 根据 Agent Card 中的能力需求选择合适模型
  - 如需要图片理解的任务路由到支持视觉的模型
- **配置示例**：
  ```yaml
  multimodal:
    image:
      enabled: true
      max_size_mb: 10
      supported_formats: [png, jpg, webp, gif]
    document:
      enabled: true
      supported_formats: [pdf, docx, xlsx, pptx]
    audio:
      enabled: false  # 默认关闭，按需开启
      max_duration_min: 30
  ```

### 2.15 工作空间

- **主模式：CLI**（类似 Claude Code）
  - 用户在终端运行，指定项目目录作为工作空间
  - Agent 直接读写本地文件、执行命令
  - 交互方式：终端对话
  - 适合已有本地项目的开发者
- **Workspace 抽象接口**：
  - `read_file(path)` / `write_file(path, content)` / `execute(command)` / `list_files(pattern)`
  - Agent 通过统一接口操作，不关心底层实现
  - 预留扩展：LocalWorkspace / SandboxWorkspace / CloudWorkspace
- **安全策略（关键 — 即使本地模式也需要防护）**：
  - **命令执行安全**：
    - 命令白名单/黑名单机制：配置 `allowed_commands` 和 `blocked_commands`
    - 默认黑名单：`rm -rf /`、`format`、`del /s`、`mkfs`、`dd`、`chmod 777` 等破坏性命令
    - 受限模式（`safe_mode=true`，默认开启）：仅允许白名单命令，未列出的需用户确认
    - 完整模式（`safe_mode=false`）：允许所有命令，但危险命令仍需确认
    - 危险命令确认：即使关闭 safe_mode，破坏性操作仍弹出确认（参考人类介入 2.8）
  - **文件系统安全**：
    - 工作目录限制：Agent 只能在指定项目目录内操作
    - 路径穿越防护：拒绝 `../`、符号链接逃逸等
    - 敏感文件保护：`.env`、`id_rsa`、`credentials.json` 等默认禁止读写
    - 写保护：可配置 `readonly_paths`（如生产配置文件）
  - **安全模式配置示例**：
    ```yaml
    workspace:
      type: local
      safe_mode: true
      allowed_commands: [git, npm, pip, python, node, ls, cat, grep, find]
      blocked_commands: [rm -rf, format, mkfs, dd]
      readonly_paths: [".env", "config/production.yml"]
      protected_files: [".env", "id_rsa", "*.pem"]
    ```
- **远程沙箱模式**（第二阶段）：
  - Docker 容器隔离：每个 Session 独立容器
  - 网络隔离：限制出站网络
  - 资源限制：CPU/内存/磁盘配额
  - 代码执行安全兜底
- **云端 IDE 模式**（远期）：
  - 集成 Web IDE（如 code-server）
  - 浏览器内直接查看 Agent 修改的代码

## 3. 技术栈

### 后端
- Python 3.12 + FastAPI
- SQLAlchemy（ORM）
- asyncio（异步原生）
- SQLite（开发）/ PostgreSQL + pgvector（生产）
- Click + Rich（CLI 交互）

### 前端
- React + Next.js
- TypeScript
- Tailwind CSS + shadcn/ui
- WebSocket（实时状态推送）

### 部署
- Docker Compose（App + DB）
- 一键部署脚本（支持宝塔面板等）
  - 自动安装依赖、配置数据库、启动服务
  - 提供 install.sh 一键脚本
  - 宝塔面板集成：Docker 应用模板

### 分阶段计划
- **MVP**：CLI（Rich 终端界面）+ FastAPI 后端
- **第二阶段**：React Web 管理界面（配置管理 + 可观测性面板）

## 4. 非功能需求

- 支持多模型（OpenAI、Claude、本地模型，可插拔）
- 每个 Agent 支持配置独立的模型
- 每个 Agent 可配备多个 Skill
- 每个 Agent 有独立的提示词
- 用户可自定义创建 Agent
- Agent 能力声明（Agent Card）支持自动发现和路由
- 同一 Session 内支持并行执行独立任务（DAG 调度）
- 工作空间安全防护（命令白/黑名单、文件系统隔离、safe_mode）
- Git 版本控制集成（自动 commit、Session 分支、冲突策略）
- 多模态支持（图像/文档/音频，按需启用）
- 可扩展的 Tool 和 Skill 生态
