# agent-team CLI

在本地执行 AI 生成的执行计划，由 LLM 驱动代码生成。

## 安装

```bash
pip install agent-team
```

## 快速开始

```bash
# 1. 初始化（验证服务器 + 配置 LLM API Key）
agent-team init --server http://localhost:8200

# 2. 一键拉取并执行计划
agent-team execute --plan-id plan_xxxxx --server http://localhost:8200
```

## 命令

| 命令 | 说明 |
|------|------|
| `agent-team execute` | 一键拉取并执行计划（推荐） |
| `agent-team init` | 初始化工作区并配置 LLM |
| `agent-team pull-plan` | 从服务器拉取执行计划到本地 |
| `agent-team apply` | 从本地 JSON 文件执行计划 |
| `agent-team push-result` | 推送执行结果到服务器 |
| `agent-team show-result` | 查看执行结果文件 |
| `agent-team run-validation` | 运行验证命令 |

## 支持的 LLM

- OpenAI (gpt-4o-mini)
- DeepSeek (deepseek-chat)
- 硅基流动 (Qwen/Qwen2.5-7B-Instruct)
- Moonshot (moonshot-v1-8k)
- 本地 Ollama

## 选项

- `--step-by-step` — 逐步执行（每个任务前需确认）
- `--safe-mode` — 安全模式（仅执行只读/验证操作）
- `--project ./path` — 指定项目目录

## License

MIT
