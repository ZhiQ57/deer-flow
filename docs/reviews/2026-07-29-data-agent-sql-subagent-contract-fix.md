# DataAgent SQL SubAgent 合同错误修复 Review

## 结论

本次 `SQL_SUBAGENT_CONTRACT_INVALID` 的核心风险点不是 SQL 校验器本身，而是父流程与 SQL 子任务之间过度依赖 `ToolMessage.content` 文本。`content` 可能被通用工具输出预算外部化/截断，也可能只是 task 失败时的展示文本；父级继续把它当作唯一合同来源，就会把真实上游原因误判成“SQL 子任务返回格式不符合 DataAgent 合同”。

## 根因

- SQL SubAgent 的 `data_validate_sql` / `data_execute_sql` 旧输出只有 JSON 文本 content，没有结构化 artifact。
- `task_tool._build_data_query_sql_result_from_steps()` 旧逻辑只解析捕获步骤里的 content；一旦 content 被预算中间件改写成 preview，权威 SQL 校验/执行结果就丢失。
- `SqlStageMiddleware._merge_result()` 旧逻辑只解析 task 结果 content；如果 task 本身已经失败并带 `SQL_SUBAGENT_TOOL_RESULT_INVALID`，父级也会继续按 JSON 合同解析，最终统一误报 `SQL_SUBAGENT_CONTRACT_INVALID`。

## 修改

- `backend/packages/harness/deerflow/agents/service_agent/sql_tools.py`
  - SQL 校验/执行工具改为 `response_format="content_and_artifact"`。
  - content 保留 JSON 文本，artifact 保留同一份结构化结果。
- `backend/packages/harness/deerflow/tools/builtins/task_tool.py`
  - SQL 子任务步骤解析改为 artifact 优先，旧 content JSON 仅作兼容。
  - DataAgent SQL task 完成时把 `data_query_sql_result` 放入最终 task ToolMessage artifact。
- `backend/packages/harness/deerflow/agents/service_agent/sql_stage_middleware.py`
  - 父级 SQL 阶段解析 task 结果时优先读取 artifact。
  - task 已携带 failed metadata 时透传安全的 `SQL_*` 错误码，避免二次包装成合同 invalid。
- `frontend/src/core/messages/data-query.ts`
  - 增加 `SQL_SUBAGENT_FAILED` 的用户可读文案。
- 文档同步更新 `docs/guide/used-api.md` 与 `backend/AGENTS.md`。

## 验证

- `uv run pytest tests/test_data_agent_query_flow.py tests/test_sql_executor.py tests/test_task_tool_core_logic.py -q`
- `uv run ruff check packages/harness/deerflow/agents/service_agent/sql_tools.py packages/harness/deerflow/agents/service_agent/sql_stage_middleware.py packages/harness/deerflow/tools/builtins/task_tool.py tests/test_data_agent_query_flow.py tests/test_sql_executor.py tests/test_task_tool_core_logic.py`
- `pnpm test tests/unit/core/messages/data-query.test.ts`
