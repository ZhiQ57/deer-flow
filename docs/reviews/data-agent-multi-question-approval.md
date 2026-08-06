# DataAgent 多问题审批前端改造 Review

## 结论

本次前端改造已完成，未发现阻塞上线的问题。

## 改动检查

- `HumanInputMode` 已增加 `multi_question_choice`。
- `HumanInputRequest` 已增加 `flow_id` 和 `questions`，并校验问题 ID、选项 ID 与选项内容。
- 多问题审批必须逐题选择，提交时外层 `human_input_response` 携带 `flow_id`。
- `response.value` 使用 JSON 保存 `intent_approval_answers`、`flow_id`、`answers` 和 `final_action`。
- 已按 `flow_id` 关联 `publish_query_labels` 与 `ask_intent_approval` 消息。
- `QueryIntentCard` 已适配精简后的标签 artifact，不再依赖已删除的快照、歧义项和旧审批结果字段。
- DataAgent 输入框已增加 `always`、`on_ambiguity`、`auto` 三种审批模式占位控件；当前切换仅保存在组件本地，不写回后端。
- 中英文文案和前端架构说明已同步更新。

## 验证结果

- `pnpm check`：通过。
- `pnpm test`：通过，共 88 个测试文件、768 个测试。
- 本次修改文件的 Prettier 检查：通过。
- `git diff --check`：通过。

## 已知非本次问题

仓库级 `pnpm format` 仍会报告以下三个既有文件未通过 Prettier，本次未修改它们：

- `frontend/src/components/workspace/messages/message-list-item.tsx`
- `frontend/src/components/workspace/messages/subtask-card.tsx`
- `frontend/tests/unit/core/tasks/subtask-result.test.ts`
