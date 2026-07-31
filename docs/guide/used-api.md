# Used API Guide

## DataAgent 生产 Text2SQL

- **正式入口**：Agent 运行仍使用 `assistantId=lead_agent`，通过运行上下文的 `agent_name` 加载 custom-agent；不增加 DataAgent 专属 LangGraph、checkpoint 或 SSE。前端手动执行 SQL 单独使用线程级 Gateway REST 接口，不创建 Agent run。
- **能力启用**：`AgentConfig.service_ability` 是唯一扩展入口。`type=data_query`、`version=1`、`enable_sql_rag=true` 时由 `deerflow.agents.service_agent.registry.resolve_service_ability()` 动态提供工具和 middleware；v1 不支持标签-only 半能力，停用时移除整个 `service_ability`。未配置能力的 Agent 维持原工具面和 middleware 链。
- **脱敏 Agents API**：Agents API 的 `service_ability` 只返回能力类型、版本、数据源 ID、确认模式和 SQL 开关，不返回 DSN 环境变量名之外的连接配置或 Secret。
- **TableRAG 登记**：`TableRagStageMiddleware` 只拦截名称严格等于 `sqlrag_retrieve` 的单一 MCP 工具，不兼容 Server 前缀或旧工具名。`operation` 只允许 `hybrid-search`、四类 `search-*` 与 `expand-join-graph`；成功结果生成类型化 ref 和 retrieval digest，补充检索同时登记 operation 与独立关键词。首次失败/空结果进入 `needs_refinement`，同轮补充检索失败只记录安全错误并保留最近成功 Evidence。
- **状态入口**：`ThreadState.service_states` 是唯一业务授权状态。`merge_service_states()` 按 `service_name` 替换活动快照，并拒绝阶段回退、旧 snapshot SQL 结果和旧用户轮次写入；DataAgent 不向 ThreadState 顶层新增 `data_query_labels`、`data_retrieval_context` 或 SQL 平行字段。
- **标签工具**：`publish_query_labels` 只由 DataAgent service ability 提供，不在全局 `BUILTIN_TOOLS`。参数为 `intent`、完整 `labels`、`confidence` 和显式 `ambiguities`；没有歧义必须传空数组，不能省略或只在最终文本中描述疑问。数据库标签必须引用当前 registry 中的 `evidence_refs`。
- **标签 artifact**：`kind=data_query_labels`、`version=1`，携带 `service_name`、`snapshot_id`、`data_source_id`、labels、`ambiguities`、`ambiguity_items`、confidence 和 approval。ToolMessage/custom event 仅用于历史与 UI，不是执行授权；实时 `messages-tuple` 也必须保留 artifact。
- **确认协议**：需要确认时复用 human-input v1，request 使用 `source=ask_clarification` 与 `request_id=data-query:<digest>`。DataAgent 不重复创建独立聊天运行时，而是在标签卡内展示逐项审核项；前端将逐项结果编码为 v1 文本响应，后端验证 snapshot、item ID 和最终动作后才允许 SQL 阶段。仍支持 `execute`、`sql_only`、`cancel` 和自由文本修改。
- **SQL 子代理**：父 DataAgent 只通过现有 `task` 调用 `service_ability.sql_subagent_name`，`SqlStageMiddleware` 将 prompt 替换为严格 JSON envelope。custom-agent 的 `allowable_subagents` 必须包含该名称；全局 `config.yaml -> subagents.custom_agents` 还必须把其工具 allowlist 限制为 `data_validate_sql`、`data_execute_sql` 且设置 `skills: []`。两个服务端门禁缺一不可。
- **轮次与任务终态**：`DataAgentTurnResetMiddleware` 在每个新的可见用户消息前建立当前 `data_query` 的 `idle` 快照，旧轮次的 `needs_refinement`、标签授权和 SQL 快照不能阻塞本轮检索。SQL 阶段门禁拒绝委派时，`task` ToolMessage 必须携带 `subagent_status=failed` 和 `subagent_error`；旧版本写入的 `SQL_*` JSON 错误，以及带旧 `run_id` 但没有终态结果的 delegation，也会在 delegation ledger 和前端任务卡中恢复为失败终态，避免模型上下文和界面永久显示 `in_progress`。
- **数据源绑定**：`same_physical_target` 要求 TableRAG 检索目标与 SQL 执行目标 fingerprint 相同；`logical_data_source` 用于服务端明确配置的异构拓扑，例如 PostgreSQL TableRAG 索引元数据对应 MySQL 业务执行库。Gateway 生成不可由模型覆盖的 `binding_fingerprint`，只把无密钥 binding 注入 Harness；DSN、`dsn_env`、`secret://` 引用和请求级 Secret 都只留在 Gateway。
- **Gateway SQL 模块**：正式共享执行入口是 `app.gateway.modules.sql_execution.service.SqlExecutionService`。同目录下 `contracts.py`、`binding.py`、`validator.py`、`drivers.py`、`result_budget.py`、`error_classifier.py`、`runtime_registry.py`、`run_context.py`、`router.py` 和 `tool_provider.py` 分别承担合同、绑定、AST 校验、只读驱动、结果预算、错误脱敏、Run 能力、上下文注入、路由和 SQL SubAgent 工具适配。`deerflow-harness` 不包含 SQL Executor、数据库驱动或 sqlglot 依赖，也不解析 DSN/Secret。`source=subagent` 必须绑定当前 Query Snapshot 和 TableRAG registry；`source=manual_ui` 使用 Gateway 当前数据源绑定，不伪造 Snapshot。SQL Execution 配置不维护静态表/字段列表；未来按当前登录用户查询权限的逻辑应接入 Gateway 授权层。两个来源的校验摘要不能交叉复用。
- **Gateway SQL API**：公开的 `POST /api/threads/{thread_id}/sql/execute` 只接受 `source=manual_ui`。Router 要求当前用户严格拥有非空 owner 的线程，按当前用户和 `agent_name` 加载 custom-agent，拒绝普通 Agent、无效 `service_ability` 和关闭的 `sql_execution`，再调用 Gateway `SqlExecutionService.validate()` / `aexecute()`。SQL 校验与数据库执行失败使用 HTTP 200 的结构化 SQL 结果；线程、Agent、权限和请求合同错误使用 4xx。数据库驱动的主错误会以最小脱敏后的 `error_message` 返回，去除 DSN、凭据、控制字符和异常堆栈，但不再用泛化文案替换。
- **SQL SubAgent 内部 API**：Gateway 为已认证 Run 登记进程内 SQL 能力，只保存无密钥 binding 和不持久化的请求级 Secret。`POST /api/internal/threads/{thread_id}/sql/validate` 与 `/execute` 仅接受 trusted internal auth，并重新读取 owner、agent、approved Snapshot、retrieval 和 binding；请求体不接受 DSN、Secret、Schema 或 registry。普通登录会话不能伪造内部来源。每次有效校验登记一个新代次，每次执行原子消费一个代次；失败后必须显式再次调用 `data_validate_sql`，执行次数达到 `max_execution_attempts` 或成功后永久停止。
- **SQL 工具**：Harness 只提供通用 `deerflow.subagents.tool_provider` 扩展点。Gateway 注册的工具提供器按当前 approved Snapshot 动态注入 `data_validate_sql` / `data_execute_sql`，工具模型参数保持 `sql` 与 `sql + validation_digest`，而 thread、run、user、agent 和 internal auth 由闭包注入。工具通过内部 Gateway 路由获取权威 artifact，不直接调用 Service 或数据库。结构化失败包含 `error_category`、可选脱敏 `error_message`、`retryable` 和 `recommended_action`；`sql_only` 只装配校验工具。
- **并行调用**：同一 AIMessage 内只保留第一个 TableRAG 调用、标签发布和目标 SQL SubAgent 委派；补充检索、标签修订和 SQL 修复必须在后续模型轮次串行进行。
- **SQL 结果真相源**：父流程只从 SQL SubAgent 捕获的 Gateway `data_query_sql_result` ToolMessage artifact 重建结果，优先读取 ToolMessage artifact，才兼容旧 content JSON；`task` 的最终 SQL 结果也携带 `data_query_sql_result` artifact。父流程拒绝把子代理最终自由文本或可伪造的普通 content 当作数据库事实；若 task 已以 `SQL_*` 错误码失败，SQL 阶段直接透传真实错误码，不再二次包装成 `SQL_SUBAGENT_CONTRACT_INVALID`。
- **前端**：`frontend/src/core/messages/data-query.ts` 严格解析 v1 artifact；现有消息分组新增 `assistant:query-intent`，并把同一 snapshot 的 `publish_query_labels` 与 `ask_intent_approval` ToolMessage 合并到同一张 `QueryIntentCard`。`QueryIntentCard` 复用 `HumanInputCard` 提交确认，只在 `ask_intent_approval` 提供 request 时展示按钮；纯标签快照不会再渲染原始 JSON。启用 `data_query` 且 `sql_execution_enabled=true` 的 custom-agent 会给已完成的 SQL fenced code block 增加执行按钮；`core/sql-execution` 只向公开手动 API 发送 `agent_name`、`sql`、`source=manual_ui`，不得发送内部认证头、`run_id`、`snapshot_id`、SubAgent 来源、DSN、Secret、Schema 或 retrieval。`components/workspace/sql-execution` 保存本次请求状态并在 ChatBox 的 `sql-result` 右侧面板显示未经过 LLM 改写的结果或数据库主错误。用户可以在面板中选中 SQL、错误或结果文本，点击“添加到对话”，由 Sidecar 引用机制把选中文本附加到下一次请求上下文。流式 SQL、非 SQL 代码块和普通 Agent 不显示执行入口。
- **Skill**：`table-rag-agent` 只把当前 TableRAG registry 作为数据库事实来源，不允许伪造 snapshot、Evidence ref、validation digest 或执行结果。

## 配置引用

- Custom-agent 模板：`docs/agents/data-agent/config.yaml`。
- SQL SubAgent 示例：`config.example.yaml -> subagents.custom_agents.sql-subagent`。
- TableRAG MCP：`extensions_config.example.json -> mcpServers.tablerag`；必须设置 `tool_name_prefix=false`，确保唯一工具名为 `sqlrag_retrieve`。
- 执行 DSN：`service_ability.sql_execution.dsn_env` 指向的环境变量；当前本地真实拓扑使用 `DATA_AGENT_MYSQL_DSN`。真实 DSN 不进入 Git、artifact、checkpoint 或日志。

## 历史实验路径

`backend/packages/harness/deerflow-dev` 是废弃代码，不属于生产、测试或本方案实现范围。其 MySQL 顶层状态、ChartSpec 和独立图工厂合同不得用于新生产代码。
