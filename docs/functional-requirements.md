# Team Agent — 功能需求清单

> 结构化功能清单，作为需求编号、优先级和边界的唯一真相源。
>
> **优先级说明**：P0 = MVP 必须 | P1 = MVP 推荐 | P2 = 第二阶段

---

## 一、核心域模型

### 1.1 Session

| ID | 功能 | 描述 | 优先级 |
|----|------|------|--------|
| DM-001 | Session 作为顶层执行单元 | Session 统一承载用户目标、计划、任务、消息、附件、产物和统计 | P0 |
| DM-002 | Session 元数据 | Session 至少包含 id/title/status/mode/user_id/config_snapshot | P0 |
| DM-003 | Session 归档触发 | Session 结束后触发归档流程，保存摘要与关键产物引用 | P0 |
| DM-004 | 高质量归档摘要 | Session 结束后生成更适合人阅读和检索的摘要 | P1 |

### 1.2 Task

| ID | 功能 | 描述 | 优先级 |
|----|------|------|--------|
| DM-010 | Task 领域模型 | Task 至少包含 title/description/status/assigned_agent/dependencies/artifacts | P0 |
| DM-011 | Task 状态机 | 定义 PENDING/READY/ASSIGNED/RUNNING/BLOCKED/WAITING_APPROVAL/COMPLETED/FAILED/CANCELLED/SKIPPED | P0 |
| DM-012 | Task 分配版本 | 通过 assignment_version 避免重复分配和重放造成的重复执行 | P1 |

### 1.3 Message / Artifact

| ID | 功能 | 描述 | 优先级 |
|----|------|------|--------|
| DM-020 | Message 最小可靠性字段 | Message 至少包含 message_id/seq/category/dedupe_key/ack_at/retry_count | P0 |
| DM-021 | Attachment 模型 | 用户上传文件采用统一附件模型管理 | P0 |
| DM-022 | Artifact 模型 | Agent 产出物采用统一产物模型管理，包含来源、路径、关联任务等元数据 | P1 |

---

## 二、Agent 核心

### 2.1 Agent 生命周期

| ID | 功能 | 描述 | 优先级 |
|----|------|------|--------|
| A-001 | 会话级隔离 | 每次任务创建独立 Session，Agent 在 Session 内协作 | P0 |
| A-002 | 分层上下文共享 | Session 共享目标、计划、摘要与产物引用，Agent 保留私有 working context | P0 |
| A-003 | Agent 私有工作上下文 | 每个 Agent 维护自己的 working context，避免全量上下文互相污染 | P0 |
| A-004 | 协程运行时 | Agent 以 asyncio.Task 运行，轻量并发 | P0 |
| A-005 | 崩溃隔离 | 单个 Agent 异常不影响其他 Agent，由 AgentSupervisor 捕获上报 | P0 |
| A-006 | Agent 活性检测 | 记录 heartbeat_at / last_progress_at / current_task_id 以判断是否无响应 | P1 |
| A-007 | 进程隔离运行时 | ProcessAgent，通过 multiprocessing 进程隔离 | P2 |
| A-008 | 容器隔离运行时 | ContainerAgent，Docker 容器隔离 | P2 |

### 2.2 Agent 发现与注册

| ID | 功能 | 描述 | 优先级 |
|----|------|------|--------|
| A-010 | 配置文件自动发现 | 扫描 agents/ 目录下 YAML 配置文件，自动加载 Agent | P0 |
| A-011 | Agent Card 能力声明 | 每个 Agent 注册后广播 name/description/capabilities/skills/model/constraints | P0 |
| A-012 | Leader 路由决策 | Leader 读取 Agent Card 进行任务分配路由 | P0 |
| A-013 | Agent 退出处理 | 正常退出广播 SHUTDOWN，异常退出由 Monitor 超时检测 | P1 |
| A-014 | 动态注册 | 运行中通过 API/CLI 动态添加新 Agent | P2 |

---

## 三、通信系统

### 3.1 消息总线

| ID | 功能 | 描述 | 优先级 |
|----|------|------|--------|
| C-001 | Leader→子Agent 指令 | Leader 可分配任务、召回、下发控制消息 | P0 |
| C-002 | 子Agent→Leader 汇报 | 子 Agent 可汇报完成、阻塞、求助、失败状态 | P0 |
| C-003 | 子Agent↔子Agent 协作请求 | 子 Agent 之间允许 request/inform 类型协作消息，不允许互相下达 command | P0 |
| C-004 | Leader 广播 | Leader 可发送全员通知 | P0 |
| C-005 | Leader 介入语义 | Leader 通过追加 interrupt/command 类消息介入，不拦截已投递消息 | P0 |
| C-006 | 全局序列号 | MessageBus 分配单调递增 seq，保证全局排序基准 | P0 |
| C-007 | 关键消息持久化 | 任务状态变更、控制消息等关键消息写 DB 后投递 | P1 |
| C-008 | 关键消息 ACK | 关键消息需要接收确认，用于恢复与补发 | P1 |
| C-009 | 幂等消费 | 消费者按 message_id 或 dedupe_key 幂等处理关键消息 | P1 |
| C-010 | 重复消息去重 | 重试或重放消息不能导致重复执行 | P1 |
| C-011 | 消息 TTL | 过期消息自动清理（默认 24h） | P1 |
| C-012 | 消息重放 | Agent 重连后从 DB 补发未确认关键消息 | P2 |
| C-013 | 背压机制 | 消费者处理不过来时，总线对发送方限流 | P2 |
| C-014 | 外部消息中间件 | 可替换为 Redis Streams / NATS JetStream | P2 |

### 3.2 消息权限与协作边界

| ID | 功能 | 描述 | 优先级 |
|----|------|------|--------|
| C-020 | 三级消息分类 | command / request / inform 三类消息模型 | P0 |
| C-021 | 总线层强制拦截 | MVP 中仅 Leader 可发送 command 类型消息，其他 Agent 发送时直接拒绝 | P0 |
| C-022 | 协作请求最小化 | Agent 间仅共享完成协作所需的最小上下文 | P0 |
| C-023 | 协作不抢占主任务 | 协作请求不能抢占 Agent 当前主任务，只能轻量回复或排队 | P0 |
| C-024 | 子团队委派 | Leader 将一组任务委派给子 Agent 管理（sub_leader） | P2 |
| C-025 | 异步仲裁 | 冲突仲裁走独立异步队列，不阻塞正常通信 | P2 |

---

## 四、LLM 调用层

| ID | 功能 | 描述 | 优先级 |
|----|------|------|--------|
| L-001 | Provider 适配器 | 统一接口适配 OpenAI / Anthropic / Google / Ollama | P0 |
| L-002 | 自定义 Provider | 通过 base_url 支持任意 OpenAI 兼容 API | P1 |
| L-003 | Fallback 链 | 当前模型失败后自动切换下一个备用模型 | P0 |
| L-004 | 指数退避重试 | API 失败后按指数退避重试 | P0 |
| L-005 | 429 自动等待 | 读取 Retry-After header，自动等待后重试 | P0 |
| L-006 | Token 统计采集 | 按 Session / Agent / 用户维度采集 Token 消耗 | P0 |
| L-007 | Token 预估 | 调用前预估 Token 用量 | P1 |
| L-008 | 成本预算 | 可配置 Session 级 Token 或成本预算，超限暂停 | P1 |
| L-009 | 成本告警 | 达到预算阈值时通知用户 | P1 |
| L-010 | Complete 模式 | 支持等待完整响应 | P0 |
| L-011 | Stream 模式 | 支持流式输出用户可见响应 | P0 |
| L-012 | Prompt 模板管理 | 内置规划/分配/摘要等模板，支持变量替换 | P0 |
| L-013 | 结构化输出 | 支持 Function Calling / Tool Use 或 JSON 校验输出 | P1 |
| L-014 | Prompt 版本化 | 模板文件与代码一起版本控制 | P1 |
| L-015 | Provider 插件 | pip install agent-team-provider-xxx 扩展新 Provider | P2 |

---

## 五、Skill 与 Tool 系统

### 5.1 Skill

| ID | 功能 | 描述 | 优先级 |
|----|------|------|--------|
| S-001 | Markdown Skill | Skill 为 .md 文件，含 frontmatter（name/description/tools） | P0 |
| S-002 | Skill 自动发现 | 扫描 skills/ 目录，自动加载 | P0 |
| S-003 | 内置 Skill | 提供少量可复用的内置 Skill 模板 | P1 |
| S-004 | Skill 热加载 | 运行中修改 Skill 文件自动生效 | P1 |

### 5.2 Tool

| ID | 功能 | 描述 | 优先级 |
|----|------|------|--------|
| T-001 | file_read | 读取文件内容 | P0 |
| T-002 | file_write | 写入/追加文件 | P0 |
| T-003 | file_list | 列出目录文件 | P0 |
| T-004 | shell_execute | 执行 Shell 命令 | P0 |
| T-005 | web_search | 网络搜索 | P0 |
| T-006 | send_message | Agent 间发送消息 | P0 |
| T-007 | ask_human | 请求人类介入/确认 | P0 |
| T-008 | file_delete | 删除文件（需确认） | P1 |
| T-009 | code_execute | 执行代码片段（受限沙箱） | P1 |
| T-010 | Tool 默认风险映射 | 每个内置 Tool 有默认 safety_level，且不得绕过硬安全约束 | P0 |
| T-011 | 三级安全权限 | low / medium / high 三类安全级别 | P0 |
| T-012 | Tool 执行超时 | 默认 30 秒，可按 Tool 配置 | P0 |
| T-013 | Tool 输出规范 | 统一 ToolResult(success, data, error, metadata) | P0 |
| T-014 | Tool 装饰器注册 | 通过装饰器注册自定义 Tool | P1 |
| T-015 | Tool 自动发现 | 扫描 tools/ 目录自动加载 | P1 |
| T-016 | memory_search | 向量记忆检索 | P2 |
| T-017 | git_command | Git 操作（受保护分支限制） | P2 |
| T-018 | image_analyze | 图片分析 | P2 |
| T-019 | document_parse | 文档解析（PDF/Word/Excel） | P2 |
| T-020 | audio_transcribe | 音频转文字 | P2 |
| T-021 | 第三方 Tool 包 | pip install agent-team-tool-xxx | P2 |

---

## 六、任务编排

| ID | 功能 | 描述 | 优先级 |
|----|------|------|--------|
| O-001 | LLM 驱动规划 | Leader 接收任务后进行拆解与分配建议 | P0 |
| O-002 | 用户确认方案 | supervised 模式下，规划后展示方案给用户确认 | P0 |
| O-003 | 动态调整 | 执行过程中 Leader 可根据结果动态调整任务分配 | P1 |
| O-004 | 防死循环 | max_iterations 强制终止执行 | P0 |
| O-005 | 串行任务执行 | MVP 以串行执行为主，保证状态和恢复逻辑简单可控 | P0 |
| O-006 | Agent 独占式 | 每个 Agent 同时只处理一个主任务 | P0 |
| O-007 | 可用 Agent 池 | Leader 维护空闲 Agent 池，优先分配可用 Agent | P0 |
| O-008 | 预定义 Pipeline | 支持用户预定义流程约束（设计→开发→测试） | P1 |
| O-009 | 规划缓存 | 常见规划可缓存为模板，减少 LLM 调用 | P2 |
| O-010 | 并行 DAG 调度 | 无依赖步骤并行分配给不同 Agent | P2 |

---

## 七、记忆系统

| ID | 功能 | 描述 | 优先级 |
|----|------|------|--------|
| M-001 | Layer 1 Markdown | 人写的项目约定 + Agent 角色文件，Agent 只读 | P0 |
| M-002 | Layer 2 数据库 | Agent 写入的对话历史、任务记录、消息日志 | P0 |
| M-003 | DB 为 Source of Truth | Agent 运行时写 DB，Markdown 为摘要视图 | P0 |
| M-004 | Agent 启动加载 | 启动时自动加载对应 Markdown 文件作为上下文 | P0 |
| M-005 | 归档摘要同步 | Session 结束时将 DB 中关键信息整理为摘要同步到 Markdown | P1 |
| M-006 | 数据库引擎切换 | SQLite（开发）↔ PostgreSQL+pgvector（生产） | P1 |
| M-007 | Layer 3 向量检索 | 知识 embedding + 语义相似度搜索 | P2 |

---

## 八、上下文管理

| ID | 功能 | 描述 | 优先级 |
|----|------|------|--------|
| X-001 | 生成截断检测 | 检测 finish_reason == "length" 或等效截断信号 | P0 |
| X-002 | 最大续写限制 | max_continuations 限制续写次数，防止无限续写 | P0 |
| X-003 | 简单续写 | 截断后从断点继续生成，避免重复输出 | P1 |
| X-004 | 分块输出 | 超长输出按逻辑单元拆分为多次 LLM 调用 | P1 |
| X-005 | 输入分层压缩 | System Prompt / 当前任务 / 最近对话优先，其他内容摘要化 | P0 |
| X-006 | 滑动窗口+摘要 | 用摘要替代早期对话历史 | P0 |
| X-007 | CLI 长输出折叠 | 超过阈值的 CLI 输出默认折叠显示 | P1 |
| X-008 | 输出格式自适应 | 代码/表格/报告以更适合的方式展示或保存 | P1 |
| X-009 | 流式截断恢复 | Stream 中断后恢复续传 | P2 |

---

## 九、交互模式

| ID | 功能 | 描述 | 优先级 |
|----|------|------|--------|
| I-001 | 任务模式 | 用户→Leader 编排→Agent 团队执行 | P0 |
| I-002 | 单聊模式 | 用户直接与指定 Agent 对话 | P0 |
| I-003 | 圆桌模式 | 多 Agent 自由讨论，无 Leader 编排 | P2 |
| I-004 | 圆桌轮数限制 | max_rounds 到达后自动结束 | P2 |
| I-005 | 圆桌共识检测 | LLM 判断是否达成共识 | P2 |
| I-006 | 模式转换 | 圆桌结论转为正式任务执行 | P2 |

---

## 十、人类介入

| ID | 功能 | 描述 | 优先级 |
|----|------|------|--------|
| H-001 | auto 模式 | 全自动，不介入普通步骤 | P0 |
| H-002 | supervised 模式 | 关键节点审批（默认） | P0 |
| H-003 | manual 模式 | 每步确认 | P1 |
| H-004 | 任务规划确认 | Leader 出方案后用户确认再执行 | P0 |
| H-005 | 危险操作审批 | 删除文件、部署等高风险动作需人工确认 | P0 |
| H-006 | 最终交付审查 | 输出结果前人过一眼 | P1 |
| H-007 | 审批优先级 | 硬安全约束 > Tool 风险级别 > Session 模式 > 用户局部授权 | P0 |

---

## 十一、容错机制

| ID | 功能 | 描述 | 优先级 |
|----|------|------|--------|
| F-001 | Agent 输出修复 | 输出异常时将错误描述喂回 Agent 重试生成 | P0 |
| F-002 | Tool 重试 | Tool 执行失败时喂回报错信息，最多有限轮重试 | P0 |
| F-003 | 任务失败保留成果 | 失败时保留已完成部分、未完成清单和产物引用 | P0 |
| F-004 | Checkpoint（业务级） | 每个业务步骤记录可查询的 checkpoint | P1 |
| F-005 | Checkpoint（Tool 级） | debug_mode 下记录细粒度 Tool 执行过程 | P2 |
| F-006 | Leader 状态持久化 | Plan、任务分配、执行进度写入 DB | P1 |
| F-007 | Leader 崩溃恢复 | 重启后从 DB 恢复 Session 状态 | P1 |
| F-008 | 三级错误分级 | fatal / recoverable / warning | P0 |
| F-009 | 用户友好错误提示 | 格式化提示 + 可操作建议 | P0 |

---

## 十二、Session 管理

| ID | 功能 | 描述 | 优先级 |
|----|------|------|--------|
| SS-001 | Session 创建 | 创建 Session，关联用户和 Agent | P0 |
| SS-002 | Session 状态机 | CREATED→PLANNING→AWAITING_APPROVAL→EXECUTING↔PAUSED→COMPLETED/FAILED/CANCELLED | P0 |
| SS-003 | Session 取消 | 用户主动取消正在执行的 Session | P0 |
| SS-004 | Session 自动标题 | LLM 自动生成 Session 标题 | P1 |
| SS-005 | Session 暂停/恢复 | 暂停后释放资源，恢复时从 checkpoint 继续 | P2 |
| SS-006 | 活跃超时 | 4h 无操作自动暂停 | P2 |
| SS-007 | 暂停超时归档 | 暂停 24h 后自动归档 | P2 |
| SS-008 | 多 Session 并发 | 同一用户同时运行多个 Session | P2 |

---

## 十三、用户文件交互

| ID | 功能 | 描述 | 优先级 |
|----|------|------|--------|
| UF-001 | CLI 路径引用 | 消息中引用本地文件路径 | P0 |
| UF-002 | /attach 命令 | 显式附加文件 + glob 批量 | P0 |
| UF-003 | 文件大小限制 | 单文件 50MB / Session 总 500MB | P0 |
| UF-004 | 格式白/黑名单 | 允许/禁止的文件扩展名 | P0 |
| UF-005 | Message 附件模型 | Message 中 attachments 字段 + Attachment 数据结构 | P0 |
| UF-006 | Agent 输出文件交付 | 生成文件保存到 outputs/ 并展示路径 | P1 |
| UF-007 | 上传文件清理 | retention_days 到期自动清理 | P2 |
| UF-008 | CLI 剪贴板粘贴 | 截图后粘贴，自动保存到 uploads/ | P2 |
| UF-009 | Web 拖拽/点击/粘贴上传 | Web 端文件上传与预览 | P2 |
| UF-010 | 智能多模态路由 | 图片或文档自动路由到支持对应能力的模型 | P2 |

---

## 十四、安全防护

| ID | 功能 | 描述 | 优先级 |
|----|------|------|--------|
| SC-001 | 命令黑名单 | rm -rf、mkfs 等破坏性命令默认拦截 | P0 |
| SC-002 | safe_mode | 默认仅允许白名单命令，其他命令需确认 | P0 |
| SC-003 | 工作目录限制 | Agent 只能在指定项目目录内操作 | P0 |
| SC-004 | 路径穿越防护 | 拒绝 ../ 和符号链接逃逸 | P0 |
| SC-005 | 敏感文件保护 | .env、id_rsa 等默认禁止读写 | P0 |
| SC-006 | 安全配置收紧原则 | 低层配置只能收紧高层安全限制，不能放宽 | P0 |
| SC-007 | Prompt Injection 最小防线 | 角色隔离、外部输入标记、危险操作硬拦截 | P1 |
| SC-008 | 输出验证 | 检查是否调用未授权 Tool 或超出 Agent constraints | P1 |
| SC-009 | 审计追溯 | 完整记录关键 LLM 输入输出和高风险动作 | P1 |
| SC-010 | API Key 加密存储 | AES-256 加密，密钥来自环境变量或 keyring | P0 |
| SC-011 | 日志全链路脱敏 | 日志、堆栈、对话中的 Key 统一脱敏 | P0 |
| SC-012 | 代码执行资源限制 | CPU / 内存 / 磁盘 / 网络均有限制 | P1 |
| SC-013 | Session 最大时长 | 可配置 Session 最大运行时长 | P1 |

---

## 十五、可观测性

| ID | 功能 | 描述 | 优先级 |
|----|------|------|--------|
| OB-001 | Agent 实时状态 | 空闲/执行中/等待中 | P0 |
| OB-002 | 任务整体进度 | 待做/进行中/已完成 | P0 |
| OB-003 | 关键消息展示 | 展示关键消息流和状态变化 | P1 |
| OB-004 | Tool 执行元数据 | 记录 Tool 名称、耗时、是否成功等元数据 | P0 |
| OB-005 | LLM 调用元数据 | 记录模型、Token、耗时、成本等元数据 | P0 |
| OB-006 | 分级日志 | DEBUG/INFO/WARN/ERROR，按 Session/Agent/任务筛选 | P0 |
| OB-007 | 结构化日志 | JSON 格式，支持外部日志系统接入 | P1 |
| OB-008 | 用量统计展示 | 展示按用户/Agent/模型维度聚合的 Token 和成本数据 | P1 |
| OB-009 | 运行时日志级别调整 | 支持运行时调整日志级别 | P1 |

---

## 十六、配置管理

| ID | 功能 | 描述 | 优先级 |
|----|------|------|--------|
| CF-001 | YAML 配置文件 | 统一 YAML 格式 | P0 |
| CF-002 | 多层配置覆盖 | 框架默认→项目→用户→Session→Agent→运行时 | P0 |
| CF-003 | 治理类配置收紧 | 安全/审批/预算/路径等治理类配置只能被下层收紧 | P0 |
| CF-004 | Pydantic 配置校验 | 启动时校验完整性，错误提示清晰 | P0 |
| CF-005 | CLI 参数覆盖 | 通过命令行参数覆盖部分配置 | P0 |
| CF-006 | 环境变量覆盖 | AGENT_TEAM_* 前缀环境变量 | P0 |
| CF-007 | Agent 配置热加载 | 修改 Agent YAML 文件后自动生效 | P1 |
| CF-008 | Skill 热加载 | 修改 Skill 文件后自动生效 | P1 |
| CF-009 | config validate 命令 | 手动校验配置合法性 | P1 |

---

## 十七、Git 集成

| ID | 功能 | 描述 | 优先级 |
|----|------|------|--------|
| G-001 | Git 只读状态检查 | 支持获取当前工作树状态和差异信息 | P1 |
| G-002 | 自动 Commit | 每完成一步自动 commit | P2 |
| G-003 | Session 分支 | 每个 Session 创建独立分支 | P2 |
| G-004 | 保护分支 | 禁止直接 push 到 main/master | P2 |
| G-005 | 禁止 force push | 禁止 git push --force | P2 |
| G-006 | Agent 间冲突处理 | Leader 仲裁指定 Agent 负责合并 | P2 |
| G-007 | 与人类冲突处理 | 暂停 Agent，通知用户手动解决 | P2 |

---

## 十八、用户体系

| ID | 功能 | 描述 | 优先级 |
|----|------|------|--------|
| U-001 | 用户级 LLM Key | 每个用户配置自己的 API Key | P1 |
| U-002 | 账号密码认证 | 基础认证方式 | P2 |
| U-003 | API Key 认证 | 方便程序调用 | P2 |
| U-004 | 数据隔离 | 每个用户数据完全独立 | P2 |
| U-005 | 团队模式 | 多用户共享项目空间 | P2 |
| U-006 | OAuth 登录 | 第三方登录 | P2 |

---

## 十九、多模态

| ID | 功能 | 描述 | 优先级 |
|----|------|------|--------|
| MM-001 | 图像输入 | 通过视觉模型处理图片 | P2 |
| MM-002 | 文档解析 | PDF/Word/Excel 提取文本 | P2 |
| MM-003 | 音频转写 | 音频转文字 | P2 |
| MM-004 | Mermaid 图表输出 | 生成架构图/流程图 | P1 |
| MM-005 | PDF/Excel 报告输出 | 生成文件报告 | P2 |

---

## 二十、外部集成

| ID | 功能 | 描述 | 优先级 |
|----|------|------|--------|
| EX-001 | Webhook 通知 | 任务完成/失败时回调 | P2 |
| EX-002 | Slack/飞书/钉钉通知 | IM 通知 | P2 |
| EX-003 | 邮件通知 | SMTP 邮件 | P2 |
| EX-004 | MCP 协议兼容 | 接入外部 MCP 工具服务器 | P2 |
| EX-005 | CI/CD 集成 | 触发 GitHub Actions 等 | P2 |
| EX-006 | VSCode 扩展 | IDE 内与 Agent 交互 | P2 |

---

## 二十一、CLI 交互

| ID | 功能 | 描述 | 优先级 |
|----|------|------|--------|
| CLI-001 | 交互式 REPL | 类似 Claude Code 的会话模式 | P0 |
| CLI-002 | 命令模式 | `agent-team run "任务描述"` 非交互执行 | P0 |
| CLI-003 | 基础状态展示 | 展示当前 Agent 状态和整体进度 | P0 |
| CLI-004 | init 初始化向导 | 交互式选择项目类型/Provider/模板 | P1 |
| CLI-005 | 团队模板 | software-dev / data-analysis / content-creation / minimal | P1 |
| CLI-006 | debug prompt | 查看 Agent 发给 LLM 的完整 Prompt | P1 |
| CLI-007 | debug messages | 查看 Session 内消息记录 | P1 |
| CLI-008 | 单步执行 | `--step-by-step` 每步暂停 | P1 |
| CLI-009 | 多 Agent 分色展示 | 按 Agent 分色显示对话、时间戳和标签 | P1 |
| CLI-010 | debug replay | 用相同输入重新调用 LLM | P2 |
| CLI-011 | 消息时序图 | 自动生成 Mermaid 时序图 | P2 |
| CLI-012 | monitor TUI | 实时监控面板 | P2 |

---

## 功能统计（修订后建议）

| 优先级 | 说明 |
|--------|------|
| **P0** | MVP 闭环必须具备，优先保证状态、边界和可执行性 |
| **P1** | 提升可用性、可调试性和工程体验 |
| **P2** | 第二阶段扩展能力，不进入 MVP 默认路径 |