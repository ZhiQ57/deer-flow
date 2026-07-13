# Used API Guide

## DataAgent Text2SQL

- **Custom-agent loader**：`deerflow.config.agents_config.load_agent_config()` 和 `load_agent_soul()` 读取 `.deer-flow/users/{user_id}/agents/{agent_name}/config.yaml` 与 `SOUL.md`。
- **运行路由**：Gateway / IM 渠道仍使用 `lead_agent`，通过 `config.configurable.agent_name` 或 `config.context.agent_name` 注入 `data-agent`。
- **Skill 白名单**：DataAgent 配置使用 Skill frontmatter 名称：`table-rag-agent`、`data-analysis`、`chart-visualization`。
- **TableRAG MCP**：`extensions_config.example.json -> mcpServers.tablerag` 通过 `python -m table_rag.mcp --transport stdio` 暴露 `tablerag_*` 工具。
- **TableRAG SDK 包边界**：`backend/packages/harness/pyproject.toml` 将 `table_rag` 纳入 `deerflow-harness` wheel 包，使 MCP stdio 子进程可通过模块方式启动。
- **实验性 DataAgent 运行层**：`backend/packages/harness/deerflow-dev` 按 DeerFlow SDK 边界拆分；`from agents import build_data_agent, make_data_agent` 是图工厂入口，通过 `deerflow.agents.factory.create_deerflow_agent(...)` 重新创建图；该入口当前未注册 Gateway 路由。
- **DataAgent middleware**：`agents.middlewares.query_context_middleware.QueryContextMiddleware` 负责查询上下文，`agents.middlewares.data_agent_orchestration_middleware.DataAgentOrchestrationMiddleware` 负责阶段提示、工具门禁和调用预算；middleware 不再放在 `agents/data_agent`。
- **DataAgent 状态扩展**：`agents.thread_state.DataAgentState` 继承 `ThreadState`，新增 `data_agent_stage`、`data_query_context`、`data_retrieval_context`、`data_generated_sql`、`data_sql_validation`、`data_sql_execution`、`data_last_successful_sql_execution` 和 `data_chart_spec`。
- **DataAgent 专用工具**：`tools.builtins.get_data_agent_tools()` 返回 `data_validate_sql`、`data_execute_sql`、`data_build_chart_spec`；工具分别位于独立的 `*_tool.py` 模块，前两者通过 SQL 摘要绑定，禁止执行未经过最近一次校验的 SQL。
- **DataAgent 工具基础层**：`tools.sql_validation`、`tools.database` 和 `tools.chart_spec` 分别提供 SQL AST 校验、MySQL 只读执行和 ChartSpec 构造，不再与图工厂放在同一业务包。
- **数据库配置**：TableRAG MCP 使用 `TABLERAG_MCP_INDEX_DSN` / `TABLERAG_MCP_SOURCE_DSN`；业务 SQL 执行使用 `DATA_AGENT_MYSQL_DSN` 或 `DATA_AGENT_MYSQL_*` 分项环境变量。真实凭据不得进入 Git。
- **工具权限**：实验性 DataAgent 仅允许必要本地只读工具、只读 TableRAG MCP 和专用运行工具；索引变更工具、其他 MCP、Bash 和写文件工具不会注册。
- **控制台执行层**：`backend/tests/service_agent/test-data-agent/run_data_agent_stream.py` 直接加载实验性 DataAgent 图并用 LangGraph `stream_mode=["values", "messages", "custom"]` 打印中间流式日志；不新增 Gateway 路由。
- **控制台日志**：执行脚本通过 `--log-path` 接收日志目录或 `log.txt` 模板，生成 `log_YYYYMMDD_HHMMSS_mmm.txt`；记录脱敏运行变量、路径检查、流式回答、工具/阶段事件和 Python root logging。
- **本地可视化调试页**：`backend/tests/service_agent/test-data-agent/run_data_agent_web.py` 创建独立 FastAPI 应用，通过 `POST /api/chat` 返回 `application/x-ndjson` 结构化流，并在浏览器展示对话、QueryContext、阶段、TableRAG、SQL、结果表、ChartSpec 和工具时间线；默认仅监听 `127.0.0.1`，不注册到正式 Gateway。
- **调试页会话与并发**：页面使用进程内 `InMemorySaver` 按 `thread_id` 保留同一页面会话，进程重启后清空；为避免全局日志 handler 和实验图并发互相污染，每个页面进程只允许一个活动运行。
