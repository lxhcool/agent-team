# Team Agent

一个支持多模型、多 Skill、独立配置的团队协作 Agent 框架。

## 特性

- 🤖 **多模型支持** — OpenAI、Claude、本地模型，可插拔切换
- 🧠 **Claude Code 风格记忆** — 项目级 `.agent/memory.md` 持久化知识
- 🛠 **Skill 系统** — 每个 Agent 独立配备多个 Skill
- 📝 **独立提示词** — 每个 Agent 自定义 System Prompt
- 🔄 **异步消息总线** — Agent 间点对点与广播通信
- 📋 **可扩展** — 用户可自定义创建 Agent

## 快速开始

```bash
pip install -e ".[dev]"

# 初始化项目
team-agent init

# 运行
team-agent run "帮我设计一个REST API"
```

## 配置

在 `agent_config.yaml` 中定义 Agent：

```yaml
agents:
  - name: researcher
    model: openai/gpt-4o
    system_prompt: "你是一个研究专家..."
    skills:
      - web_search
      - code_search

  - name: coder
    model: anthropic/claude-sonnet-4-20250514
    system_prompt: "你是一个高级程序员..."
    skills:
      - code_execute
      - file_read
      - file_write
```

## 记忆系统

参考 Claude Code 的 `CLAUDE.md` 机制：

- `memory/project.md` — 项目级共享知识
- `memory/{agent_name}.md` — Agent 专属记忆
- Agent 在运行时自动读写记忆文件，持久化关键信息

## 架构

```
用户请求 → Orchestrator(规划+分配) → Agent 团队(协作执行)
                ↑                           ↓
              Monitor ←←←←←←← 结果/状态反馈
```
