# DataAgent 正式查询闭环缺口审查

审查日期：2026-07-15

## 1、结论

当前仓库已经具备大部分 DataAgent 原型能力，但这些能力分布在两条不同链路：

- 正式 custom-agent UI 链路负责加载 `data-agent` 的模型、SOUL、Skill 和工具组；
- `deerflow-dev` 实验链路负责 TableRAG 后标签、阶段门禁、SQL 校验/执行、
  ChartSpec 和工具预算。

因此当前不能仅通过完善 `SOUL.md` 或 `z_sqltable-rag/SKILL.md` 实现用户要求的正式闭环。
需要把实验运行能力迁入稳定 Harness，并让正式 lead-agent 工厂根据 custom-agent
运行 Profile 选择专属状态、工具和 middleware。

## 2、必须修改的核心点

### P0：正式后端链路

1. 正式 DataAgent 注册 `publish_query_labels`。
2. 正式图使用包含 DataAgent 字段的状态 Schema。
3. 正式图注册 SQL 校验、只读执行和可选 ChartSpec 工具。
4. 正式图启用 TableRAG -> 标签 -> 确认 -> 校验 -> 执行门禁。
5. 正式图实施最小权限工具过滤。
6. 正式图复用 Gateway 原有 RunManager、StreamBridge、checkpoint 和 journal。

### P0：前端链路

1. 为查询标签定义稳定 artifact/状态协议。
2. 把 `publish_query_labels` ToolMessage 渲染为独立标签卡。
3. 复用 human-input 协议完成确认、修改和取消。
4. 待确认时禁用普通输入框。
5. 页面刷新后从历史 ToolMessage 恢复标签卡。

### P0：数据一致性

1. TableRAG 检索源与 SQL 执行源必须通过 `data_source_id` 绑定。
2. SQL 校验必须绑定当前标签快照和当前检索摘要。
3. 数据库来源标签必须引用当前轮次 Evidence。
4. 标签变化后旧确认、旧校验和旧执行结果必须失效。

## 3、当前实现中的直接问题

- `QueryLabelsMiddleware` 已追加到正式 DataAgent，但正式工具集中没有
  `publish_query_labels`。
- 正式图仍使用 `ThreadState`，DataAgent 状态只存在于实验图。
- SQL 工具只存在于 `deerflow-dev`，正式 UI 无法调用。
- 前端不处理 `data_query_labels` custom event，也没有标签卡。
- 正式 DataAgent 配置开放了 Bash、写文件、Web 和默认子代理，权限过宽。
- custom-agent `tool_groups` 不能单独构成 MCP/内置工具安全边界。
- 当前 TableRAG 源库为 PostgreSQL，实验执行器固定为 MySQL，缺少数据源一致性绑定。

## 4、推荐决策

- 继续使用 `lead_agent + agent_name`，不要新增平行 Gateway 图路由。
- 增加通用 `runtime_profile`，不要继续按 `agent_name == "data-agent"` 硬编码。
- 将 `deerflow-dev` 中可交付能力迁入稳定 `deerflow.*`，实验脚本反向复用正式实现。
- 查询标签使用带 `snapshot_id` 的完整意图快照。
- 默认确认模式使用 `on_ambiguity`。
- 标签卡以持久化 ToolMessage artifact 为真相源，custom event 只用于低延迟更新。
- SQL 执行必须使用数据库级只读账号，应用层校验只能作为纵深防御。

## 5、实施规模判断

这不是只改一个 Skill 或一个 middleware 的小需求，而是一个跨后端运行时、状态协议、
工具权限、数据库执行和前端交互的完整功能。

建议按三个阶段交付：

1. 正式 MVP：Profile、状态、工具、自动/每次确认、标签卡和 E2E。
2. 完整交互：歧义确认、修改条件、SQL-only、历史恢复和结果表。
3. 生产加固：多数据源、表/Join 强校验、脱敏、审计和评测集。

详细 Todo 见：
`docs/plans/2026-07-15-data-agent-production-query-flow.md`。
