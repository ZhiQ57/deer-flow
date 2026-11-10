# Harness 适配器契约

本目录把不同 Agent Harness 收敛到同一套评测协议。`evals/` 只负责调度、数据、评分和报告；每个 harness 的 CLI 参数、输出格式和 Trace 方式由自己的 YAML 描述，再由一个薄适配器执行。

## 1. 统一契约

每个适配器必须实现以下步骤：

| 步骤 | 输入 | 输出 | 要求 |
| --- | --- | --- | --- |
| `prepare` | DatasetItem、实验配置 | 独立 workspace、prompt、MCP/Skill 配置 | 不把 Gold、隐藏实例、评分规则写入 workspace |
| `invoke` | workspace、prompt、模型/超时/预算 | 原始 stdout/stderr/事件、退出码 | 不静默重试；超时要留下部分证据 |
| `normalize` | 原始输出 | `NormalizedResult` | 统一字段，保留原始证据路径 |
| `trace` | `NormalizedResult`、Experiment 上下文 | Trace ID、覆盖级别 | 不伪造 Trace；无法关联时明确标注 |
| `cleanup` | workspace | — | 释放子进程、临时服务和端口 |

`NormalizedResult` 至少包含：

```json
{
  "harness": "codex",
  "harness_version": "0.144.1",
  "model": "gpt-5-codex",
  "status": "ok",
  "final_output": "最终回答原文",
  "tool_calls": [
    {"name": "sql_execute", "arguments": {}, "result": "..."}
  ],
  "usage": {"input_tokens": 0, "output_tokens": 0, "cost": null},
  "elapsed_seconds": 0,
  "trace_id": "32 位小写十六进制或 null",
  "trace_coverage": "native",
  "artifacts": {
    "stdout": "artifacts/stdout.log",
    "stderr": "artifacts/stderr.log",
    "events": "artifacts/events.jsonl"
  },
  "error": null
}
```

## 2. Trace 覆盖级别

| 模式 | 含义 | 适用 |
| --- | --- | --- |
| `native` | harness 接受 experiment item 的 trace 上下文，Agent 的 LLM/工具 span 直接进入同一 Trace | DeerFlow 进程内调用、支持 `trace_context` 的 harness |
| `bridged` | 适配器在 experiment item 内建根 span，调用 harness，并把解析出的事件写成子 span；harness 自己产生的 Trace 另记到 metadata | Codex、Claude Code、OpenCode、Pi Agent 等独立 CLI |
| `output_only` | 只记录输入/输出和评分，没有 Agent 调用链 | 不支持事件流或 Trace 的 harness；报告中必须标注 |

约束：

- `bridged` 的 Trace 能出现在 Experiments 页面，但其中的 LLM/工具 span 来自适配器解析，不是 harness 原生上报；报告必须写明覆盖级别。
- `native` 必须验证 `trace_id` 与 experiment item 的 Dataset Run Item 一致；只记录一个 `trace_id` 不算关联。
- 同一对照实验里，如果各 harness 的 Trace 覆盖级别不同，结论只能比较最终输出和指标，不能比较调用链细节。

## 3. 能力矩阵

`harness.yaml` 的 `capabilities` 是跳过/分组的依据，不是成绩推断依据。

| 能力 | 说明 | 缺失时 |
| --- | --- | --- |
| `mcp` | 能加载 MCP Server 和工具 | 依赖 MCP 的评测记为 `skipped`，不记 0 分 |
| `skills` | 能加载 Skill/插件 | Skill 相关评测跳过 |
| `subagents` | 支持子代理 | 过程指标标注不适用 |
| `json_events` | 输出结构化事件流 | 只能做输出级评分 |
| `native_trace` | 能原生上报 Langfuse/OTel Trace | 使用 `bridged` 或 `output_only` |
| `resume` | 支持会话续跑 | 多轮样本按单轮重建 |
| `non_interactive` | 支持无交互执行 | 不进入自动对照实验 |

## 4. 公平性

同一对照实验必须满足：

- 同一数据集版本、同一题目、同一 prompt 模板、同一超时和并发。
- 模型能统一就统一；不能统一时在报告中写明差异，不能把模型差异归因给 harness。
- MCP/Skill 配置尽量一致；harness 不支持的配置记为能力缺失，不偷偷删题。
- 每轮只改一个变量；重复运行用 `rep01`、`rep02` 区分，不覆盖已有结果。
- 报告按 harness、指标、难度分组，禁止合成一个“总排名”。

## 5. 新增 Harness

1. 复制一个现有 YAML，填写 `name`、`display_name`、`type`、`version_command`。
2. 用该工具的 `--help` 核对参数，不要凭记忆写 flag。
3. 填写 `command.args` 或 `http`，明确 prompt 传输方式（stdin/argv/file）。
4. 填写 `output.format` 和 `event_parser`；先在 1 条样本上验证解析。
5. 声明 `capabilities` 和 `trace.mode`；跑通 trace 关联验证。
6. 在 `evals.yml` 的 `harnesses` 注册，并用 `sql-end-to-end-baseline` 做三题联调。
7. 更新本文件的矩阵和 `evals/AGENTS.md` 的 harness 表。

## 6. 安全

- 不在 YAML 中写明文凭据；使用环境变量引用（如 `$VFSSQL_OAUTH_CLIENT_SECRET`）。
- 每个 harness 使用独立 `harness_home` 和 workspace，避免读取用户的全局配置、历史会话和凭据。
- 不把生产数据库、真实患者数据、完整原始 Trace 写入仓库。
