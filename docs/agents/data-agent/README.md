# DataAgent Text2SQL 原生启动说明

DataAgent 使用 DeerFlow 原生 custom-agent 机制，不新增独立运行栈。运行时路径为：

```text
.deer-flow/users/default/agents/data-agent/config.yaml
.deer-flow/users/default/agents/data-agent/SOUL.md
```

本目录保存同名模板文件，便于复制到运行时目录。`.deer-flow/` 是本地状态目录，不提交到 Git。

## 1. 写入 DataAgent 文件

在仓库根目录执行：

```powershell
New-Item -ItemType Directory -Force -Path ".deer-flow\users\default\agents\data-agent" | Out-Null
Copy-Item -LiteralPath "docs\agents\data-agent\config.yaml" -Destination ".deer-flow\users\default\agents\data-agent\config.yaml" -Force
Copy-Item -LiteralPath "docs\agents\data-agent\SOUL.md" -Destination ".deer-flow\users\default\agents\data-agent\SOUL.md" -Force
```

## 2. 启用 TableRAG MCP

先确认真实 TableRAG SDK 已位于：

```text
backend/packages/harness/table_rag/
```

`extensions_config.example.json` 已包含默认关闭的 `tablerag` MCP 配置。复制后启用：

```powershell
Copy-Item -LiteralPath "extensions_config.example.json" -Destination "extensions_config.json" -Force
```

然后在 `extensions_config.json` 中把 `mcpServers.tablerag.enabled` 改为 `true`，并通过环境变量注入 TableRAG 配置文件路径。如果 `tabelrag.yaml` 已包含 `TABLERAG_INDEX_DSN` / 源库 DSN，则不需要再额外注入 DSN 环境变量：

```powershell
$env:TABLERAG_CONFIG="D:\path\to\tabelrag.yaml"
```

> 不要把真实 DSN 写入 `extensions_config.json`、`config.yaml`、文档或 Git；本地 `tabelrag.yaml` 已被 Git 忽略，可以保存部署机私有 DSN。
> TableRAG MCP 使用 PostgreSQL DSN；部署环境需要安装后端 postgres extra（例如在 `backend/` 下执行 `uv sync --extra postgres`，或使用等价 Docker 构建配置）。

## 3. 原生启动与测试

1. 按常规方式准备 `config.yaml`。
2. 从仓库根目录执行 `make dev`。
3. 打开 `http://localhost:2026/workspace/agents/data-agent/chats/new`；也可以打开 `http://localhost:2026/workspace/agents/data-agent`，前端会自动跳转到新会话入口。
4. 发送 Text2SQL 问题，例如：`查询 2024 年华东区域销售额最高的前 10 个商品`。
5. 观察运行日志中是否加载 `tablerag_*` MCP 工具，并确认 DataAgent 先检索 TableRAG 上下文再输出 SQL。

## 4. 预期行为

- DataAgent 的 `skills` 白名单只允许 `table-rag-agent`、`data-analysis`、`chart-visualization`。
- DataAgent 只通过 DeerFlow `lead_agent + agent_name=data-agent` 路由启动。
- 普通 SQL 生成优先调用 `tablerag_retrieve`；只有显式管理请求才允许调用索引初始化或字段值同步工具。
