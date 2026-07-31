# Gateway SQL Execution 边界重构 Review

## 1. Review 结论

- [X] SQL 校验、DSN 解析、数据库驱动、只读事务、结果预算、错误分类和执行次数控制已迁移到 `backend/app/gateway/modules/sql_execution/`。
- [X] `deerflow-harness` 不再包含 SQL Executor、SQL 工具实现、数据库 Secret 解析或 `pymysql/sqlglot` 依赖。
- [X] SQL SubAgent 通过 Gateway 注入的 `data_validate_sql` / `data_execute_sql` 工具调用内部路由，不直接调用 `SqlExecutionService` 或数据库。
- [X] 前端只调用公开手动执行 API，合同保持 `{agent_name, sql, source:"manual_ui"}`。
- [X] 旧 `deerflow.agents.service_agent.sql_executor`、`sql_tools`、`binding` 入口直接删除，不保留兼容层。
- [X] 当前变更范围未发现待修复的 P0/P1/P2 代码问题。

## 2. 后端 Review

### 2.1 模块边界

- Gateway 垂直模块按职责拆分为 `contracts.py`、`binding.py`、`validator.py`、`drivers.py`、`result_budget.py`、`error_classifier.py`、`service.py`、`runtime_registry.py`、`run_context.py`、`router.py` 和 `tool_provider.py`。
- 公开手动路由与内部 SQL SubAgent 路由使用不同信任入口，但复用同一 Gateway Service。
- 内部路由重新读取认证用户、线程、Custom Agent、approved Snapshot、retrieval 和 binding；请求体不接受 DSN、Secret、Schema 或 registry。

### 2.2 Secret 与生命周期

- Gateway Run 准备阶段只向 Harness 注入无密钥 `data_query_binding`。
- Harness 的 `data_query_service_ability` 改为 `public_metadata()` 脱敏投影，不包含 `dsn_env` 或 `secret://` 引用。
- SQL 数据库请求级 Secret 会从通用 `context.secrets` 中摘除，仅复制到 Gateway 进程内 Run 能力注册表；其他 Skill Secret 保持原行为。
- Run 完成、失败或取消后通过 Task done callback 删除注册表能力和数据库 Secret；TTL/容量淘汰继续作为异常兜底。

### 2.3 执行约束

- 每次有效 `data_validate_sql` 在 Gateway 注册一个校验代次；`data_execute_sql` 原子消费该代次。
- 执行失败后重复使用旧校验代次返回 `validation_reuse`，必须显式重新调用 `data_validate_sql`。
- 执行次数达到 `max_execution_attempts` 或成功后停止。
- 父流程只从 Gateway ToolMessage artifact 重建 SQL 结果；工具 content 和 SQL SubAgent 自由文本不能成为数据库事实来源。

## 3. 智能体 Review

- Harness 新增通用 `deerflow.subagents.tool_provider` 扩展点，不包含 SQL、Gateway 或数据库专属实现。
- `task_tool` 只向应用层提供器传递可信的 thread/run/user/agent、父上下文和父状态。
- `TableRagStageMiddleware` 只消费 Gateway 注入的无密钥 binding，不解析 DSN 或请求级 Secret。
- `SqlStageMiddleware` 保留 approved Snapshot、`sql_only`、执行结果校验、失败/成功阶段投影和并行调用限制，不再二次调用本地 SQL Service。
- Gateway artifact 的 Snapshot、data source、database type、binding fingerprint、validation digest 和执行结果仍由父流程再次校验。

## 4. 前端 Review

- `core/sql-execution/api.ts` 的公开 URL 和请求字段未改变。
- 新增合同测试，使用带伪造内部字段的输入确认不会转发 `run_id`、`snapshot_id`、DSN、Secret、internal auth 或 `source=subagent`。
- SQL Result Panel 的成功、结构化错误、数据库主错误和选中文本引用单元测试保持通过。

## 5. 验证记录

### 5.1 已通过

- 后端 SQL/DataAgent/Harness 边界定向测试：`163 passed`。
- 后端扩大回归：`530 passed, 1 skipped, 14 deselected`；deselect 仅包含本机 SkillScan 隔离用例和已证明与本次改动无关的 4 个运行生命周期超时。
- 两个陈旧 `load_agent_config` Mock 修复后：`2 passed`。
- 变更范围 Ruff format/check：通过。
- 前端全量单元测试：`761 passed`。
- 前端 `pnpm check`：通过。
- 前端 SQL API/Result Panel 定向测试：`7 passed`。

### 5.2 环境与基线阻塞

- 本机终端安全软件会隔离 `deerflow/skills/skillscan/orchestrator.py`，并在全量收集时进一步影响 `tests/test_skillscan_native.py`；Skill 安装测试因此进入保守阻断策略。提交前已将两个文件从 `dev` 精确恢复，并确认最终差异中不包含这两个文件。
- 全量后端收集仍被历史 `deerflow-dev` 测试导入已删除的 `_load_enabled_skills_for_tool_policy` 阻断，不属于本次生产 SQL 路径。
- 4 个 `test_runtime_lifecycle_e2e.py` 用例在本机 5 秒等待窗口内未进入 fake agent；将 `prepare_sql_execution_run_context` 替换为空实现后仍可复现，排除本次 SQL Run 准备逻辑。
- 后端全量 Ruff 仍报告三个 `dev` 基线文件格式问题：`skillscan/__init__.py`、`subagents/registry.py`、`tools/tools.py`；本次变更文件全部通过。
- Playwright 原始 Chromium revision 未安装；改用系统 Chrome 后，SQL E2E 停留在现有历史加载页面的 `Loading...`，未进入 SQL 按钮断言。前端单元合同和全量静态检查已覆盖本次实际改动。

## 6. Debugger 修复

- [X] 增加 Gateway 校验代次，恢复“失败后必须重新校验”的原行为。
- [X] 将 800 行 Service 拆分，降低单文件职责和测试打桩耦合。
- [X] 删除 Harness SQL 结果对普通 ToolMessage content 的回退信任。
- [X] 将运行能力上下文改为脱敏投影，移除 `dsn_env/secret://` 暴露。
- [X] 将 SQL 数据库 Secret 从 Harness 通用 Secret 载体摘除，并在 Run 结束时释放。
- [X] 修复本次触达的陈旧测试 Mock，使其匹配当前 `load_agent_config(..., user_id=...)` 和 Skill 加载辅助函数。

## 7. 最终建议

本次重构已提交并通过 fast-forward 集成到本地 `dev`。SkillScan 文件的无关改动已经排除；E2E 的历史加载阻塞建议作为独立前端测试环境问题继续处理，不应把 SQL Execution 重新放回 Harness 作为规避方案。

`origin/dev` 推送暂缓：本需求开始前本地 `dev` 已领先远端 149 个既有提交，直接推送会同时发布这些需求外历史，需由用户确认后再执行。
