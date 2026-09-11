# DataAgent Text2SQL 运行说明

DataAgent 使用 DeerFlow 的 custom-agent 机制运行。模型和子代理的数据库能力外置到
仓库根目录 `mcp-extensions/`；前端手动“执行 SQL”按钮继续使用 Gateway SQL 路由，
但该路由不会注入 AgentLoop 或模型工具。

## 入口与 MCP 服务

- 模板：`docs/agents/data-agent/config.yaml`、`docs/agents/data-agent/SOUL.md`
- 运行：前端/SDK 仍使用 `assistantId=lead_agent`，并通过
  `agent_name=data-agent` 加载 custom-agent。
- `sqltable-rag` 提供 `sqlrag_retrieve`，用于表、字段、业务口径、字段值和 Join
  Graph 检索。
- `sql-execute` 提供 `sql_execute`，只接收 SQL 字符串并返回真实
  `columns` / `rows`。返回的 `content` 还会包装“当前执行SQL为 / SQL结果为”
  摘要，方便 Lead-Agent 阅读。
- 手动前端路由 `POST /api/threads/{thread_id}/sql/execute` 继续读取
  `service_ability.sql_execution`，独立返回结果表格给 SQL Result Panel。

开发阶段可手动启动：

```powershell
uv run --project mcp-extensions/sql-execute python mcp-extensions/sql-execute/server.py `
  --config mcp-extensions/sql-execute/config.yaml `
  --transport streamable-http --host 127.0.0.1 --port 8003
```

然后在 `extensions_config.json` 启用 `sql-execute` / `sqltable-rag`，并将
`type` 设为 `http`、`url` 指向对应 MCP `/mcp` 地址。部署时 Docker Compose 会
启动两个独立服务；模型侧数据库 DSN 通过 MCP 服务环境变量注入，前端手动路由使用
Gateway 同名 DSN 环境变量。

## Lead-Agent 与 SubAgent 限制

MCP Server 负责数据库边界、只读检查、结果预算和错误脱敏。谁能看见
`sql_execute` 由 MCP 配置、custom-agent 工具组和 `custom_agents.sql-subagent`
工具白名单决定：

- 只允许 SQL SubAgent：仅在 `sql-subagent.tools` 中保留 `sql_execute`。
- 只允许 Lead-Agent：不要把 `sql_execute` 放入 SQL SubAgent 白名单。
- 需要两者都能执行：同时在两处显式配置。

Lead-Agent 自身可通过 custom-agent 配置中的 `mcp_tools` 精确限制 MCP 工具，例如
`mcp_tools: [sqlrag_retrieve]` 表示 Lead-Agent 只能检索表结构，SQL SubAgent
再通过 `tools: [sql_execute]` 获得执行权。

不需要恢复 `SqlStageMiddleware`，也不要在 AgentLoop 增加 DataAgent 专属 SQL
强校验。

## 验证

```powershell
uv run --project mcp-extensions/sql-execute pytest `
  mcp-extensions/sql-execute/tests/test_server.py -q
uv run --project mcp-extensions/sql-execute python -m py_compile `
  mcp-extensions/sql-execute/server.py `
  mcp-extensions/sqltable-rag/server.py
```

本地单元测试不连接真实业务数据库；真实 SQL 行数据和 Lead/SubAgent 权限请在
部署环境启动 MCP 服务后进行端到端验证。
