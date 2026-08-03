# 移除 DataAgent TableRAG 中间件计划

## 1. 需求与影响面

- [X] 1.1 阅读 DataAgent service ability、TableRAG 中间件、标签中间件、审批中间件、SQL 阶段中间件和 ThreadState reducer。
- [X] 1.2 确认旧 `TableRagStageMiddleware` 负责拦截 `sqlrag_retrieve`、写入 `service_states.payload.retrieval`、限制检索次数和清理旧授权。
- [X] 1.3 确认移除后 MCP 检索结果只走 DeerFlow 原生 ToolMessage 上下文，不再写 DataAgent 专属检索状态。

## 2. 代码修改

- [X] 2.1 从 `dev` 创建需求分支 `refactor/remove-table-rag-middleware`。
- [X] 2.2 删除 `table_rag_middleware.py` 及旧 `service_agent/data_agent/*` 兼容包壳。
- [X] 2.3 删除废弃的 `backend/packages/harness/deerflow-dev/` 实验 DataAgent 图、中间件、顶层状态、原型工具和本地调试脚本。
- [X] 2.4 调整 DataAgent service ability 注册，只保留 `QueryLabelsMiddleware`、`QueryIntentApprovalMiddleware` 和 `SqlStageMiddleware`。
- [X] 2.5 调整 `QueryLabelsMiddleware`，不再依赖 retrieval state、`require_retrieval` 或 `stage_name`，标签发布只写标签快照。
- [X] 2.6 调整 Gateway SQL 与 SQL SubAgent 链路，统一使用 `data_query_binding`，不读取 TableRAG registry。

## 3. 文档与测试

- [X] 3.1 删除依赖废弃实验入口的测试和调试脚本。
- [X] 3.2 更新 `docs/agents/data-agent/README.md`、`docs/guide/used-api.md`、`docs/guide/data-agent 数据流走向.md` 和 `backend/AGENTS.md`。
- [X] 3.3 运行相关 backend 测试：`51 passed, 1 skipped`；另补 `tests/test_data_agent_state_persistence.py`，`2 passed`。
- [X] 3.4 运行本次改动 Python 文件 `ruff check`：通过。全量 `uv run ruff check` 当前仍有既有 `table_rag` / `skillscan` lint 问题，未纳入本次中间件移除范围。
