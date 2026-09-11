# DeerFlow MCP Extensions

`mcp-extensions` 是 DeerFlow 的外部 MCP Server 工作区。这里的服务独立于
Gateway 和 AgentLoop，数据库连接、SQL 执行、TableRAG 配置以及服务启动参数都
放在对应扩展目录中。

当前扩展：

- `sql-execute`：接收一个 SQL 字符串，执行只读查询并返回 `columns`、原始
  `rows`、行数和截断状态。
- `sqltable-rag`：TableRAG MCP 的独立启动包装，开发阶段可单独启动，部署阶段
  可作为 Compose 服务运行。

## 开发阶段手动启动

在仓库根目录执行：

```powershell
uv run --project mcp-extensions/sql-execute python mcp-extensions/sql-execute/server.py `
  --config mcp-extensions/sql-execute/config.yaml `
  --transport streamable-http --host 127.0.0.1 --port 8003
```

然后在 DeerFlow 的 `extensions_config.json` 中使用：

```json
{
  "mcpServers": {
    "sql-execute": {
      "enabled": true,
      "type": "http",
      "url": "http://127.0.0.1:8003/mcp",
      "tool_name_prefix": false
    }
  }
}
```

也可以使用 stdio，直接把 `command` 指向当前 Python 环境和
`mcp-extensions/sql-execute/server.py`。

## 限制 Lead-Agent 或 SubAgent

MCP Server 只负责执行和返回结果，不在 DeerFlow AgentLoop 中增加业务强校验。
工具可见性由正常的 MCP 配置、custom-agent 工具组和 subagent 工具白名单控制。
需要让 SQL SubAgent 使用它时，在对应 `custom_agents` 配置中允许
`sql_execute`；只允许 Lead-Agent 使用时，则不要把它加入 SQL SubAgent 的工具
白名单。

Lead-Agent 也可以在 custom-agent 配置中设置 `mcp_tools`，例如
`mcp_tools: [sqlrag_retrieve]`，把 SQL 执行权只留给 SQL SubAgent 的
`tools: [sql_execute]`。
