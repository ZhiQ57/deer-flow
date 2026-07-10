# dev 长期二次开发分支调整 Review

## 变更内容

- 将仓库开发规范调整为：`main` 追踪 `upstream/main`，`dev` 承载长期二次开发，具体需求从 `dev` 新建 `feat/*` 或 `fix/*` 分支。
- 更新 `docs/FORK_UPSTREAM_WORKFLOW_ZH.md`，记录同步上游、合并到 `dev`、新功能开发和推送 `origin/dev` 的 PowerShell 命令。
- 将本地 `tabelrag.yaml` 加入 `.gitignore`，避免提交包含 DSN 的本地 TableRAG 配置。

## 验证记录

- 已通过：`cd backend; uv run pytest tests/service_agent/test-data-agent/test_data_agent_config.py -q`，结果 `4 passed`。
- 已通过：后端 venv 中 `psycopg` 使用 `binary` 实现；`deerflow.mcp.tools.get_mcp_tools()` 加载 10 个 TableRAG MCP 工具，包含 `tablerag_tablerag_search_tables`。
- 已通过：`cd frontend; pnpm exec tsc --noEmit --pretty false` 无输出报错。
- 已完成：功能分支提交 `08719fbe`。`r`n- 已完成：功能分支通过合并提交 `562f5a06` 合并到 `dev`；随后推送 `origin/dev`。



