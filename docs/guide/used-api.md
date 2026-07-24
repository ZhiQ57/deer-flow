# Used API Guide

## DataAgent 生产 Text2SQL

- **正式入口**：Gateway、Web 和 IM 均继续使用 `assistantId=lead_agent`，通过运行上下文的 `agent_name` 加载 custom-agent；没有 DataAgent 专属路由、LangGraph、checkpoint 或 SSE。
- **能力启用**：`AgentConfig.service_ability` 是唯一扩展入口。`type=data_query`、`version=1`、`enable_sql_rag=true` 时由 `deerflow.agents.service_agent.registry.resolve_service_ability()` 动态提供工具和 middleware；v1 不支持标签-only 半能力，停用时移除整个 `service_ability`。未配置能力的 Agent 维持原工具面和 middleware 链。
- **脱敏 Agents API**：Agents API 的 `service_ability` 只返回能力类型、版本、数据源 ID、确认模式和 SQL 开关，不返回 DSN 环境变量名之外的连接配置或 Secret。
- **TableRAG 登记**：`TableRagStageMiddleware` 只拦截名称严格等于 `sqlrag_retrieve` 的单一 MCP 工具，不兼容 Server 前缀或旧工具名。`operation` 只允许 `hybrid-search`、四类 `search-*` 与 `expand-join-graph`；成功结果生成类型化 ref 和 retrieval digest，补充检索同时登记 operation 与独立关键词。首次失败/空结果进入 `needs_refinement`，同轮补充检索失败只记录安全错误并保留最近成功 Evidence。
- **状态入口**：`ThreadState.service_states` 是唯一业务授权状态。`merge_service_states()` 按 `service_name` 替换活动快照，并拒绝阶段回退、旧 snapshot SQL 结果和旧用户轮次写入；DataAgent 不向 ThreadState 顶层新增 `data_query_labels`、`data_retrieval_context` 或 SQL 平行字段。
- **标签工具**：`publish_query_labels` 只由 DataAgent service ability 提供，不在全局 `BUILTIN_TOOLS`。参数为 `intent`、完整 `labels`、`confidence` 和显式 `ambiguities`；没有歧义必须传空数组，不能省略或只在最终文本中描述疑问。数据库标签必须引用当前 registry 中的 `evidence_refs`。
- **标签 artifact**：`kind=data_query_labels`、`version=1`，携带 `service_name`、`snapshot_id`、`data_source_id`、labels、`ambiguities`、`ambiguity_items`、confidence 和 approval。ToolMessage/custom event 仅用于历史与 UI，不是执行授权；实时 `messages-tuple` 也必须保留 artifact。
- **确认协议**：需要确认时复用 human-input v1，request 使用 `source=ask_clarification` 与 `request_id=data-query:<digest>`。DataAgent 不重复创建独立聊天运行时，而是在标签卡内展示逐项审核项；前端将逐项结果编码为 v1 文本响应，后端验证 snapshot、item ID 和最终动作后才允许 SQL 阶段。仍支持 `execute`、`sql_only`、`cancel` 和自由文本修改。
- **SQL 子代理**：父 DataAgent 只通过现有 `task` 调用 `service_ability.sql_subagent_name`，`SqlStageMiddleware` 将 prompt 替换为严格 JSON envelope。custom-agent 的 `allowable_subagents` 必须包含该名称；全局 `config.yaml -> subagents.custom_agents` 还必须把其工具 allowlist 限制为 `data_validate_sql`、`data_execute_sql` 且设置 `skills: []`。两个服务端门禁缺一不可。
- **数据源绑定**：`same_physical_target` 要求 TableRAG 检索目标与 SQL 执行目标 fingerprint 相同；`logical_data_source` 用于服务端明确配置的异构拓扑，例如 PostgreSQL TableRAG 索引元数据对应 MySQL 业务执行库。两种模式都生成不可由模型覆盖的 `binding_fingerprint`。
- **SQL 工具**：SQL 工具由 task 运行时按当前 approved snapshot 动态创建，默认 lead-agent 看不到 schema。校验使用 sqlglot PostgreSQL/MySQL AST，拒绝无业务表查询、会话信息、可写 CTE、嵌套 DML/DDL 和危险函数；执行使用对应驱动的只读事务、allowlist、timeout、行数、单元格与结果字符预算。`sql_only` 只装配 `data_validate_sql`，不会装配执行工具。
- **并行调用**：同一 AIMessage 内只保留第一个 TableRAG 调用、标签发布和目标 SQL SubAgent 委派；补充检索、标签修订和 SQL 修复必须在后续模型轮次串行进行。
- **SQL 结果真相源**：父流程只从 SQL SubAgent 捕获的 `data_validate_sql` / `data_execute_sql` ToolMessage 重建结果，拒绝把子代理最终自由文本中的 SQL 行或执行状态当作数据库事实。
- **前端**：`frontend/src/core/messages/data-query.ts` 严格解析 v1 artifact；现有消息分组新增 `assistant:query-intent`，`QueryIntentCard` 复用 `HumanInputCard` 提交确认，不创建 DataAgent 专属聊天运行时。
- **Skill**：`table-rag-agent` 只把当前 TableRAG registry 作为数据库事实来源，不允许伪造 snapshot、Evidence ref、validation digest 或执行结果。

## 配置引用

- Custom-agent 模板：`docs/agents/data-agent/config.yaml`。
- SQL SubAgent 示例：`config.example.yaml -> subagents.custom_agents.sql-subagent`。
- TableRAG MCP：`extensions_config.example.json -> mcpServers.tablerag`；必须设置 `tool_name_prefix=false`，确保唯一工具名为 `sqlrag_retrieve`。
- 执行 DSN：`service_ability.sql_execution.dsn_env` 指向的环境变量；当前本地真实拓扑使用 `DATA_AGENT_MYSQL_DSN`。真实 DSN 不进入 Git、artifact、checkpoint 或日志。

## 历史实验路径

`backend/packages/harness/deerflow-dev` 与 `backend/tests/service_agent/test-data-agent` 仍用于历史实验和对照测试，不是生产 Gateway 路径。其 MySQL 顶层状态、ChartSpec 和独立图工厂合同不得用于新生产代码。
