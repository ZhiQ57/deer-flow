# DataAgent QueryContext Tool 重构计划

> 2026-07-15 更新：本计划描述的“进入数据流程前强制 QueryContext Tool”已由
> `docs/plans/2026-07-15-data-agent-query-labels-tool.md` 取代。现有实体抽取工具
> 继续保留，但 DataAgent 改为 lead-agent 直接组织检索关键词，并通过标签工具展示意图。

## 1、现状与目标

- [X] 1.1 确认原 `QueryContextMiddleware` 会在每次图请求开始时执行实体抽取。
- [X] 1.2 确认用户期望由模型判断是否需要实体抽取，而不是 middleware 无条件执行。
- [X] 1.3 确认 DataAgent 仍需在每个新用户轮次重置旧检索、SQL、执行和图表状态，避免跨轮复用旧门禁状态。

## 2、目标设计

- [X] 2.1 将黑话归一化、意图识别和实体抽取迁移到稳定 SDK 的纯工具基础能力。
- [X] 2.2 在 `deerflow.tools.builtins.entity_extract_tool` 新增 `data_extract_query_context` built-in tool，由模型按需调用。
- [X] 2.3 工具从运行状态读取最后一条真实用户消息，不接收模型自由改写的问题文本。
- [X] 2.4 工具从 runtime context/config 读取黑话映射，通过 ToolMessage artifact 返回结果并输出结构化流事件。
- [X] 2.5 middleware 仅保留轮次状态重置、artifact 状态适配和 DataAgent 流程门禁，不再执行实体抽取或隐藏上下文注入。

## 3、流程约束

- [X] 3.1 非数据类请求允许模型直接回答，不强制调用实体抽取工具。
- [X] 3.2 模型进入 TableRAG、数据子代理、数据澄清或 SQL 流程前，必须先调用 `data_extract_query_context`。
- [X] 3.3 单轮只允许完成一次实体抽取工具调用，避免重复清理下游状态。
- [X] 3.4 TableRAG、SQL 校验、SQL 执行和 ChartSpec 继续沿用现有阶段门禁与预算。

## 4、调用方与文档

- [X] 4.1 更新 DataAgent 图工厂、prompt、工具注册和状态说明。
- [X] 4.2 更新控制台、本地调试页和单元测试中的阶段与工具预期。
- [X] 4.3 删除 `QueryContextMiddleware` 模块，不保留旧导入兼容层。
- [X] 4.4 更新 README、`backend/AGENTS.md`、API 指南、DataAgent 文档和 review。

## 5、验证

- [X] 5.1 运行 DataAgent 完整 pytest。
- [X] 5.2 运行 Ruff check 和 format check。
- [X] 5.3 运行 Python 编译检查和执行脚本 `--help`。
- [X] 5.4 清理缓存并执行任务范围 `git diff --check`。
