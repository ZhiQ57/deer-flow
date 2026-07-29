# Used API Guide

## DataAgent 生产 Text2SQL

- **正式入口**：Agent 运行仍使用 `assistantId=lead_agent`，通过运行上下文的 `agent_name` 加载 custom-agent；不增加 DataAgent 专属 LangGraph、checkpoint 或 SSE。前端手动执行 SQL 单独使用线程级 Gateway REST 接口，不创建 Agent run。
- **能力启用**：`AgentConfig.service_ability` 是唯一扩展入口。`type=data_query`、`version=1`、`enable_sql_rag=true` 时由 `deerflow.agents.service_agent.registry.resolve_service_ability()` 动态提供工具和 middleware；v1 不支持标签-only 半能力，停用时移除整个 `service_ability`。未配置能力的 Agent 维持原工具面和 middleware 链。
- **脱敏 Agents API**：Agents API 的 `service_ability` 只返回能力类型、版本、数据源 ID、确认模式和 SQL 开关，不返回 DSN 环境变量名之外的连接配置或 Secret。
- **TableRAG 登记**：`TableRagStageMiddleware` 只拦截名称严格等于 `sqlrag_retrieve` 的单一 MCP 工具，不兼容 Server 前缀或旧工具名。`operation` 只允许 `hybrid-search`、四类 `search-*` 与 `expand-join-graph`；成功结果生成类型化 ref 和 retrieval digest，补充检索同时登记 operation 与独立关键词。首次失败/空结果进入 `needs_refinement`，同轮补充检索失败只记录安全错误并保留最近成功 Evidence。
- **状态入口**：`ThreadState.service_states` 是唯一业务授权状态。`merge_service_states()` 按 `service_name` 替换活动快照，并拒绝阶段回退、旧 snapshot SQL 结果和旧用户轮次写入；DataAgent 不向 ThreadState 顶层新增 `data_query_labels`、`data_retrieval_context` 或 SQL 平行字段。
- **标签工具**：`publish_query_labels` 只由 DataAgent service ability 提供，不在全局 `BUILTIN_TOOLS`。参数为 `intent`、完整 `labels`、可选中文 `summary` 和显式 `ambiguities`；没有歧义必须传空数组，不能省略或只在最终文本中描述疑问。工具不再接收模型自报 `confidence`，数据库标签必须引用当前 registry 中的 `evidence_refs`。
- **标签 artifact**：`kind=data_query_labels`、`version=1`，携带 `service_name`、`snapshot_id`、`data_source_id`、labels、`ambiguities`、`ambiguity_items` 和 approval。ToolMessage/custom event 仅用于历史与 UI，不是执行授权；实时 `messages-tuple` 也必须保留 artifact。前端只展示标签卡、审批卡和 SQL 结果卡，隐藏实体抽取、SQL 子任务请求/响应等内部协议 JSON。
- **确认协议**：需要确认时复用 human-input v1，request 使用 `source=ask_clarification` 与 `request_id=data-query:<digest>`。DataAgent 不重复创建独立聊天运行时，而是在标签卡内展示逐项审核项；前端将逐项结果编码为 v1 文本响应，后端验证 snapshot、item ID 和最终动作后才允许 SQL 阶段。仍支持 `execute`、`sql_only`、`cancel` 和自由文本修改。
- **SQL 子代理**：父 DataAgent 只通过现有 `task` 调用 `service_ability.sql_subagent_name`，`SqlStageMiddleware` 将 prompt 替换为严格 JSON envelope。custom-agent 的 `allowable_subagents` 必须包含该名称；全局 `config.yaml -> subagents.custom_agents` 还必须把其工具 allowlist 限制为 `data_validate_sql`、`data_execute_sql` 且设置 `skills: []`。两个服务端门禁缺一不可。
- **轮次与任务终态**：`DataAgentTurnResetMiddleware` 在每个新的可见用户消息前建立当前 `data_query` 的 `idle` 快照，旧轮次的 `needs_refinement`、标签授权和 SQL 快照不能阻塞本轮检索。SQL 阶段门禁拒绝委派时，`task` ToolMessage 必须携带 `subagent_status=failed` 和 `subagent_error`；旧版本写入的 `SQL_*` JSON 错误，以及带旧 `run_id` 但没有终态结果的 delegation，也会在 delegation ledger 和前端任务卡中恢复为失败终态，避免模型上下文和界面永久显示 `in_progress`。
- **数据源绑定**：`same_physical_target` 要求 TableRAG 检索目标与 SQL 执行目标 fingerprint 相同；`logical_data_source` 用于服务端明确配置的异构拓扑，例如 PostgreSQL TableRAG 索引元数据对应 MySQL 业务执行库。两种模式都生成不可由模型覆盖的 `binding_fingerprint`。
- **SQL Executor**：`deerflow.agents.service_agent.sql_executor.SqlExecutionService` 是正式共享执行入口，集中处理 typed request/result、sqlglot PostgreSQL/MySQL AST、数据源绑定、只读事务、数据库驱动、timeout、行数/单元格/结果字符预算和安全错误分类。`source=subagent` 必须绑定当前 Query Snapshot 和 TableRAG registry；`source=manual_ui` 使用服务端当前数据源绑定，只执行 AST、只读、方言、Schema 和绑定校验，不伪造 Snapshot。SQL Execution 配置不再维护静态表/字段列表，未来按当前登录用户查询权限的逻辑应接入 Executor 授权层。两个来源的校验摘要不能交叉复用。
- **Gateway SQL API**：`POST /api/threads/{thread_id}/sql/execute` 只接受 `source=manual_ui`。Router 要求当前用户严格拥有非空 owner 的线程，按当前用户和 `agent_name` 加载 custom-agent，拒绝普通 Agent、无效 `service_ability` 和关闭的 `sql_execution`，再调用共享 `SqlExecutionService.validate()` / `aexecute()`。SQL 校验与数据库执行失败使用 HTTP 200 的结构化 SQL 结果；线程、Agent、权限和请求合同错误使用 4xx。数据库驱动的主错误会以最小脱敏后的 `error_message` 返回，去除 DSN、凭据、控制字符和异常堆栈，但不再用泛化文案替换，例如 MySQL 1054 会保留 `Unknown column ... in 'where clause'`。
- **SQL 工具**：SQL 工具由 task 运行时按当前 approved snapshot 动态创建，默认 lead-agent 看不到 schema。`data_execute_sql` 只委托给共享 `SqlExecutionService`。每次执行都会消费最近一次校验；失败后必须重新调用 `data_validate_sql`，才能在 `sql_execution.max_execution_attempts` 预算内再次执行。结构化失败包含 `error_category`、可选脱敏 `error_message`、`retryable` 和 `recommended_action`，SQL SubAgent 可以直接使用主错误修复 SQL。`sql_only` 只装配 `data_validate_sql`，不会装配执行工具。
- **并行调用**：同一 AIMessage 内只保留第一个 TableRAG 调用、标签发布和目标 SQL SubAgent 委派；补充检索、标签修订和 SQL 修复必须在后续模型轮次串行进行。
- **SQL 结果真相源**：父流程只从 SQL SubAgent 捕获的 `data_validate_sql` / `data_execute_sql` ToolMessage 重建结果，拒绝把子代理最终自由文本中的 SQL 行或执行状态当作数据库事实。
- **前端**：`frontend/src/core/messages/data-query.ts` 严格解析 v1 artifact；现有消息分组新增 `assistant:query-intent`，`QueryIntentCard` 复用 `HumanInputCard` 提交确认。启用 `data_query` 且 `sql_execution_enabled=true` 的 custom-agent 会给已完成的 SQL fenced code block 增加执行按钮；`core/sql-execution` 调用 Gateway，`components/workspace/sql-execution` 保存本次请求状态并在 ChatBox 的 `sql-result` 右侧面板显示未经过 LLM 改写的结果或数据库主错误。用户可以在面板中选中 SQL、错误或结果文本，点击“添加到对话”，由 Sidecar 引用机制把选中文本附加到下一次请求上下文。流式 SQL、非 SQL 代码块和普通 Agent 不显示执行入口。
- **Skill**：`table-rag-agent` 只把当前 TableRAG registry 作为数据库事实来源，不允许伪造 snapshot、Evidence ref、validation digest 或执行结果。

## 配置引用

- Custom-agent 模板：`docs/agents/data-agent/config.yaml`。
- SQL SubAgent 示例：`config.example.yaml -> subagents.custom_agents.sql-subagent`。
- TableRAG MCP：`extensions_config.example.json -> mcpServers.tablerag`；必须设置 `tool_name_prefix=false`，确保唯一工具名为 `sqlrag_retrieve`。
- 执行 DSN：`service_ability.sql_execution.dsn_env` 指向的环境变量；当前本地真实拓扑使用 `DATA_AGENT_MYSQL_DSN`。真实 DSN 不进入 Git、artifact、checkpoint 或日志。

## 历史实验路径

`backend/packages/harness/deerflow-dev` 是废弃代码，不属于生产、测试或本方案实现范围。其 MySQL 顶层状态、ChartSpec 和独立图工厂合同不得用于新生产代码。
