# DataAgent SQL MCP 外置化 Review

## 结论

模型和子代理的 SQL 执行已迁移到仓库根目录 `mcp-extensions/` 下的独立 MCP
服务。前端“执行 SQL”按钮继续使用 Gateway 的
`POST /api/threads/{thread_id}/sql/execute` 路由，因此手动执行能力没有被移除。

Gateway 不再注册旧的 SQL SubAgent Tool Provider；没有恢复
`SqlStageMiddleware`，也没有在 AgentLoop 中加入 DataAgent 专属 SQL 强校验。

## 已完成内容

- `sql-execute` MCP：
  - 工具名为 `sql_execute`，只接收 SQL 字符串。
  - 在 MCP 服务边界执行只读、单语句、Schema、LIMIT 和结果预算检查。
  - 返回真实 `columns`、`rows`、行数和截断状态。
  - `content` 包装“当前执行 SQL”和“SQL 结果”，供 Lead-Agent 阅读。
  - DSN 只从 MCP 进程环境变量读取。
- `sqltable-rag` MCP：
  - 提供独立启动包装和配置。
  - 复用现有 TableRAG MCP 实现。
- DeerFlow：
  - `AgentConfig.mcp_tools` 支持 Lead-Agent MCP 工具白名单。
  - DataAgent Lead 默认只保留 `sqlrag_retrieve`。
  - `sql-subagent.tools` 只配置 `sql_execute`。
  - `service_ability.sql_execution` 保留给前端 Gateway 手动执行路由，用于读取
    数据库类型、只读限制、Schema、结果预算和 DSN 引用。
- Docker：
  - 开发、生产和源码映射 Compose 均加入两个 MCP 服务及健康检查。
  - Gateway 手动路由与 MCP 服务分别接收所需 DSN 环境变量。

## 验证结果

已通过：

```text
Gateway SQL 路由、运行上下文和服务回归：140 passed
MCP 扩展测试：10 passed
MCP 工具白名单测试：2 passed
配置与 extensions_config.example.json 解析通过
ruff check / format check 通过
git diff --check 通过
```

旧的 `test_gateway_sql_tool_provider.py` 已迁移为边界回归：确认旧提供器不在
Gateway App 注册，并且没有能力上下文时不会向子代理暴露任何工具。

## 端到端验证边界

当前本地环境没有用户部署服务器上的真实业务数据库轨迹。部署环境需要自行确认：

1. 启动 `sql-execute` 和 `sqltable-rag`，并在实际 `extensions_config.json` 中启用。
2. 确认 Lead-Agent / SQL SubAgent 的 MCP 工具白名单符合预期。
3. 确认 `sql_execute` 返回真实业务 `rows`，而不是只有行数或列数。
4. 确认 Lead-Agent 收到的 `content` 包含执行 SQL 和实际 SQL 结果。
5. 确认 Gateway 前端手动路由使用正确的 `DATA_AGENT_MYSQL_DSN` 或
   `DATA_AGENT_POSTGRES_DSN`。
