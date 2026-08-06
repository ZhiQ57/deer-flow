# SQL 子代理成功结果误显示失败复盘

## 根因

`getMessageGroups` 将包含 `task` 调用的 AI 消息创建为 `assistant:subagent` 分组。SQL 子代理执行过程中如果先产生一条独立的思考消息，后续 `ToolMessage` 会被 `lastOpenGroup()` 归入新的 `assistant:processing` 分组，而不是原来的子代理分组。

`MessageList` 只在 `assistant:subagent` 分组内解析 task 结果。由于该分组看不到对应的 ToolMessage，且当前轮已结束，`derivePendingSubtaskStatus` 将任务判定为 `failed`；实际成功结果则以普通工具内容再次渲染，造成截图中的失败状态和布局重复。

## 修复

- 在消息分组阶段按 `tool_call_id` 回查所属的 `assistant:subagent` 分组。
- 匹配到 task 调用时，将 ToolMessage 放回该分组；如果是 DataAgent SQL 结果，另外保留独立结果卡片分组。
- 增加“中间思考后返回成功 task 结果”的单元回归测试。

## 验证

- `pnpm test -- tests/unit/core/messages/utils.test.ts tests/unit/core/tasks/subtask-result.test.ts`
- 75 个测试通过。
