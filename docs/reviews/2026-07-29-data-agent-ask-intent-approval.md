# DataAgent ask_intent_approval 重构复盘

## 结论

本次将 DataAgent 的“意图审批”从“每轮强制重置 + 临时快照”改成了“专属工具调用 + 历史可读的 ToolMessage”模式。

## 主要问题

- 每次可见对话都被当作新轮次，历史审批结果无法稳定复用。
- 审批与标签分离后，前端容易把内部协议 JSON 原样渲染出来。
- SQL 阶段依赖轮次状态而不是历史工具消息，导致重启后容易误判为未审批。

## 主要修改

- 新增 DataAgent 专属工具 `ask_intent_approval`。
- 审批结果写回为真实 ToolMessage，模型可直接读取历史上下文。
- 去除 DataAgent 的强制 turn reset 门禁。
- SQL 阶段改为基于当前快照审批结果或策略判定。
- 前端合并标签/审批消息展示，避免原始 JSON 泄漏到页面上。

## 验证结果

- 后端：`pytest tests/test_data_agent_service_ability.py tests/test_data_agent_query_flow.py -q`
- 前端：`pnpm -C frontend test tests/unit/core/messages/data-query.test.ts tests/unit/components/workspace/messages/query-intent-card.test.ts`
- 前端：`pnpm -C frontend check`

以上验证均通过。
