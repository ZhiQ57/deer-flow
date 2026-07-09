# Used API Guide

## DataAgent Text2SQL

- **Custom-agent loader**：`deerflow.config.agents_config.load_agent_config()` 和 `load_agent_soul()` 读取 `.deer-flow/users/{user_id}/agents/{agent_name}/config.yaml` 与 `SOUL.md`。
- **运行路由**：Gateway / IM 渠道仍使用 `lead_agent`，通过 `config.configurable.agent_name` 或 `config.context.agent_name` 注入 `data-agent`。
- **Skill 白名单**：DataAgent 配置使用 Skill frontmatter 名称：`table-rag-agent`、`data-analysis`、`chart-visualization`。
- **TableRAG MCP**：`extensions_config.example.json -> mcpServers.tablerag` 通过 `python -m table_rag.mcp --transport stdio` 暴露 `tablerag_*` 工具。
- **TableRAG SDK 包边界**：`backend/packages/harness/pyproject.toml` 将 `table_rag` 纳入 `deerflow-harness` wheel 包，使 MCP stdio 子进程可通过模块方式启动。
