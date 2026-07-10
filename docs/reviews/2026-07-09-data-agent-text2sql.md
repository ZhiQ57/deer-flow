# DataAgent Text2SQL Review

## 1、变更摘要

- 新增 DataAgent 模板：`docs/agents/data-agent/config.yaml`、`docs/agents/data-agent/SOUL.md`、`docs/agents/data-agent/README.md`。
- 已把模板复制到本地 DeerFlow 原生运行时目录：`.deer-flow/users/default/agents/data-agent/`。
- 在 `extensions_config.example.json` 中新增默认关闭的 `tablerag` stdio MCP Server 示例。
- `backend/packages/harness/pyproject.toml` 已声明 `table_rag` 需要进入 `deerflow-harness` wheel packages；该声明需要真实 `backend/packages/harness/table_rag` SDK 文件存在后才能完成端到端打包验证。
- 新增 `backend/tests/service_agent/test-data-agent/test_data_agent_config.py`，覆盖 DataAgent 配置、Skill 白名单、TableRAG MCP 示例和打包边界。
- 更新 `README.md`、`README_zh.md`、`backend/AGENTS.md`、`docs/guide/used-api.md`。

## 2、验证结果

- [X] `cd backend; uvx ruff check tests/service_agent/test-data-agent/test_data_agent_config.py`：All checks passed。
- [X] `cd backend; uv run python -m py_compile tests/service_agent/test-data-agent/test_data_agent_config.py`：编译通过。
- [X] 安全复核后，直接执行不导入 `deerflow.skills.skillscan` 的 DataAgent 配置校验脚本：输出 `SAFE_DATA_AGENT_VALIDATION_OK`。
- [X] 使用 `DEER_FLOW_PROJECT_ROOT` 指向仓库根目录后，`load_agent_config('data-agent', user_id='default')` 与 `load_agent_soul('data-agent', user_id='default')` 可读取本地运行时文件，输出 `LOCAL_DATA_AGENT_RUNTIME_OK`。
- [ ] 待恢复真实 `backend/packages/harness/table_rag` SDK 文件后，再执行 `cd backend; uv run python -m table_rag.mcp --help` 和 TableRAG MCP 端到端检索验证。

## 3、原生启动检查清单

- [ ] 准备 `config.yaml`，确保包含 `Qwen3.6-plus` 或把 DataAgent 模板中的 `model` 改为本地已配置模型。
- [ ] 确认真实 TableRAG SDK 文件已恢复到 `backend/packages/harness/table_rag/`。
- [ ] 复制 `extensions_config.example.json` 为 `extensions_config.json`，并启用 `mcpServers.tablerag.enabled=true`。
- [X] 当前本地 `extensions_config.json` 已把 `mcpServers.tablerag.env.TABLERAG_MCP_CONFIG` 指向 `tabelrag.yaml`；由于 DSN 已写在该 YAML 内，不再额外注入 `TABLERAG_INDEX_DSN`、`TABLERAG_SOURCE_DSN`。
- [ ] 在 `backend/` 安装 postgres extra，或确保 Docker 镜像包含 `psycopg`。
- [ ] 从仓库根目录执行 `make dev`，访问 `http://localhost:2026/workspace/agents/data-agent/chats/new` 测试 Text2SQL；`/workspace/agents/data-agent` 会重定向到该入口。

## 4、注意事项

- `tablerag_initialize_indexes` 与 `tablerag_sync_field_values` 默认关闭；只有显式运维任务才应开启环境开关。
- `.deer-flow/` 是本地运行时目录，已被 `.gitignore` 忽略；可追踪模板保存在 `docs/agents/data-agent/`。
- 当前检测到 `backend/packages/harness/table_rag/` 只有空目录、没有 Python 文件；需要恢复真实 SDK 文件后，才能执行 `python -m table_rag.mcp` 和端到端 SQL 生成或 TableRAG 检索业务验证。

## 5、安全处置

- 电脑管家误删的 `backend/packages/harness/deerflow/skills/skillscan/orchestrator.py` 已用 Git 恢复；该文件不是本次 DataAgent 变更内容。
- 后续验证避免再次导入 SkillScan 扫描器，只做 DataAgent 配置、MCP 配置、打包边界和 CLI 帮助的安全检查。
- 清理缓存时本地未跟踪的 `backend/packages/harness/table_rag` SDK 文件已不在当前工作区；需要从你的 SDK 源或备份重新放回该目录后，才能继续 MCP 实测。
## TableRAG MCP 导入修复

- 问题：本地 extensions_config.json 中 mcpServers.tablerag.command 使用 python，在当前 Windows 环境解析到 D:\miniconda3\python.exe，该解释器未安装/未挂载 ackend/packages/harness/table_rag，因此 MCP 子进程报 No module named 'table_rag'。
- 修复：将本地运行配置改为 D:\A-PythonWork\AOpenGithub\deer-flow\backend\.venv\Scripts\python.exe，保证 MCP 子进程使用 DeerFlow 后端虚拟环境。
- 同步：将 extensions_config.example.json 的 mcpInterceptors 示例占位项清空，避免复制示例后加载 my_package.mcp.auth:build_auth_interceptor 产生误导性告警。
- 验证：ackend\.venv\Scripts\python.exe -m table_rag.mcp --help 通过；直接调用 deerflow.mcp.tools.get_mcp_tools() 成功加载 10 个 	ablerag_* 工具。
