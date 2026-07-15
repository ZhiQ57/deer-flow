# Used API Guide

## DataAgent Text2SQL

- **Custom-agent loader**：`deerflow.config.agents_config.load_agent_config()` 和 `load_agent_soul()` 读取 `.deer-flow/users/{user_id}/agents/{agent_name}/config.yaml` 与 `SOUL.md`。
- **运行路由**：Gateway / IM 渠道仍使用 `lead_agent`，通过 `config.configurable.agent_name` 或 `config.context.agent_name` 注入 `data-agent`。
- **Skill 白名单**：DataAgent 配置使用 Skill frontmatter 名称：`table-rag-agent`、`data-analysis`、`chart-visualization`。
- **TableRAG MCP**：`extensions_config.example.json -> mcpServers.tablerag` 通过 `python -m table_rag.mcp --transport stdio` 暴露 `tablerag_*` 工具。
- **TableRAG SDK 包边界**：`backend/packages/harness/pyproject.toml` 将 `table_rag` 纳入 `deerflow-harness` wheel 包，使 MCP stdio 子进程可通过模块方式启动。
- **实验性 DataAgent 运行层**：`backend/packages/harness/deerflow-dev` 按 DeerFlow SDK 边界拆分；`from agents import build_data_agent, make_data_agent` 是图工厂入口，通过 `deerflow.agents.factory.create_deerflow_agent(...)` 重新创建图；该入口当前未注册 Gateway 路由。
- **DataAgent middleware**：`DataAgentTurnResetMiddleware` 只在新真实用户轮次重置单轮状态；稳定层 `deerflow.agents.middlewares.query_labels_middleware.QueryLabelsMiddleware` 拦截 `publish_query_labels` 并写入标签状态/流事件；`DataAgentOrchestrationMiddleware` 负责 TableRAG、SQL、ChartSpec 阶段门禁和调用预算。标签 middleware 不调用模型，也不终止图执行。
- **DataAgent 状态扩展**：`agents.thread_state.DataAgentState` 继承 `ThreadState`，新增独立的 `data_query_labels`、可选 `data_query_context`、`data_agent_stage`、`data_retrieval_context`、`data_generated_sql`、`data_sql_validation`、`data_sql_execution`、`data_last_successful_sql_execution` 和 `data_chart_spec`。
- **SDK 标签声明工具**：`deerflow.tools.builtins.query_labels_tool.publish_query_labels_tool` 的模型侧名称为 `publish_query_labels`；参数顶层包含 `intent`、`labels` 和可选 `summary`。每个标签包含 `label`、`value`、`source`，并可带 `normalized`、`evidence`。工具本体是占位实现，DataAgent middleware 生成顶层 ToolMessage artifact 和 `data_query_labels` custom stream event。
- **标签 Evidence 约束**：`source=database` 的标签必须在成功 TableRAG 检索后发布，并携带 Evidence 摘要；每次调用提交当前完整标签快照，后一次状态替换前一次。标签仅用于展示和状态记录，不是 TableRAG/SQL 前置门禁。
- **SDK 实体抽取工具**：`deerflow.tools.builtins.entity_extract_tool.entity_extract_tool` 继续保留为可选 DeerFlow Tool，仍可读取最后一条真实用户消息并通过 ToolMessage artifact 返回模型抽取结果；DataAgent 不再要求先调用它。
- **DataAgent 状态适配**：稳定 SDK 工具和 `QueryLabelsMiddleware` 不导入 `deerflow-dev`；标签 middleware 通过约定状态键写入 `data_query_labels`，`DataAgentOrchestrationMiddleware` 仅在可选实体工具成功时保存 `data_query_context`，不会借此重置已产生的检索或 SQL 状态。
- **DataAgent 专用工具**：`tools.builtins.get_data_agent_tools()` 返回可选 `entity_extract_tool`、`publish_query_labels`、`data_validate_sql`、`data_execute_sql` 和 `data_build_chart_spec`。lead-agent 直接组织 TableRAG query/keywords，标签和实体抽取都不是阶段前置条件。
- **DataAgent 工具基础层**：`tools.sql_validation`、`tools.database` 和 `tools.chart_spec` 分别提供 SQL AST 校验、MySQL 只读执行和 ChartSpec 构造；实体抽取基础能力已迁移到稳定 SDK，不再保留 `deerflow-dev/tools/query_context.py`。
- **数据库配置**：TableRAG MCP 使用 `TABLERAG_MCP_INDEX_DSN` / `TABLERAG_MCP_SOURCE_DSN`；业务 SQL 执行使用 `DATA_AGENT_MYSQL_DSN` 或 `DATA_AGENT_MYSQL_*` 分项环境变量。真实凭据不得进入 Git。
- **工具权限**：实验性 DataAgent 仅允许必要本地只读工具、只读 TableRAG MCP 和专用运行工具；索引变更工具、其他 MCP、Bash 和写文件工具不会注册。
- **控制台执行层**：`backend/tests/service_agent/test-data-agent/run_data_agent_stream.py` 直接加载实验性 DataAgent 图并用 LangGraph `stream_mode=["values", "messages", "custom"]` 打印中间流式日志；不新增 Gateway 路由。
- **控制台日志**：执行脚本通过 `--log-path` 接收日志目录或 `log.txt` 模板，生成 `log_YYYYMMDD_HHMMSS_mmm.txt`；记录脱敏运行变量、路径检查、流式回答、工具/阶段事件和 Python root logging。
- **本地可视化调试页**：`backend/tests/service_agent/test-data-agent/run_data_agent_web.py` 创建独立 FastAPI 应用，通过 `POST /api/chat` 返回 `application/x-ndjson` 结构化流，并在浏览器展示对话、用户意图标签、可选 QueryContext、阶段、TableRAG、SQL、结果表、ChartSpec 和工具时间线；默认仅监听 `127.0.0.1`，不注册到正式 Gateway。
- **调试页会话与并发**：页面使用进程内 `InMemorySaver` 按 `thread_id` 保留同一页面会话，进程重启后清空；为避免全局日志 handler 和实验图并发互相污染，每个页面进程只允许一个活动运行。
