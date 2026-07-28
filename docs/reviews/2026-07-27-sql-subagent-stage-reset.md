# SQL SubAgent 阶段状态修复 Review

## 1. 问题

DataAgent 在已有线程中重新发起 SQL 查询时，页面一直显示 SQL SubAgent 运行中。模型同时提示需要标签审核，但页面没有标签确认卡。

## 2. 根因

1. 正式 `DataAgentServiceAbility` 没有注册用户轮次重置 middleware。
2. 上一轮 `data_query` 的 `needs_refinement` 快照继续作为活动状态，本轮成功的 TableRAG 结果不能通过 reducer 替换跨轮次状态。
3. `publish_query_labels` 在错误阶段被拒绝，没有产生 `data_query_labels` artifact，前端没有可渲染的审核卡。
4. `SqlStageMiddleware` 拒绝未批准的 task 时只返回普通 JSON ToolMessage，没有子代理终态元数据。
5. delegation ledger 和前端将没有 `subagent_status` 的旧 task 结果保留为 `in_progress`。
6. 部分旧任务的 ToolMessage 已不在当前 checkpoint，但 delegation 仍带旧 `run_id` 和 `in_progress`，继续污染后续模型上下文。

## 3. 修复

- 新增并注册 `DataAgentTurnResetMiddleware`，新可见用户轮次先写入 `idle` 快照。
- human-input 隐藏确认继续使用原可见用户轮次，不触发重置。
- SQL 阶段错误 ToolMessage 写入 `subagent_status=failed` 和 `subagent_error`。
- delegation ledger 在后续模型调用前，把历史 `SQL_*` JSON 阶段错误恢复为 failed。
- 新运行开始时，把带旧 `run_id` 且未产生终态结果的 delegation 收敛为 failed。
- 前端对相同旧格式做只读兼容，历史任务卡可以立即停止转圈。

## 4. 验证

- Backend DataAgent、SQL Executor、Gateway、task 和 delegation 相关测试：`213 passed`。
- Frontend `subtask-result` 单元测试：`32 passed`。
- Frontend TypeScript typecheck 和相关 ESLint：通过。
- Backend 相关 Ruff check、format check：通过。

## 5. 行为边界

- 轮次重置只作用于配置了 `data_query` service ability 的 DataAgent。
- 历史消息兼容只识别 `version=1`、`ok=false` 且 `error_code` 以 `SQL_` 开头的 task JSON。
- 未知 task 结果仍保持现有 `in_progress` 默认行为，避免把未识别协议误判为失败。
