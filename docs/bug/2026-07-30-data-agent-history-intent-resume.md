# DataAgent 取消后重新执行的历史意图重入修复

## 问题

用户第一次查询时，DataAgent 已经完成 TableRAG 检索并展示查询意图标签/审批卡。用户点击“取消查询”后，`service_states.stage` 进入 `cancelled`。随后用户在同一对话中发送“重新执行”，模型本应读取历史消息，自行判断是直接 SQL、重新发布标签、再次请求审批，还是重新检索。

旧实现把 `cancelled` 当成当前快照的硬终态，导致模型再次调用 `publish_query_labels` 时被拒绝，前端无法重新出现标签/审批组件，流程卡死。

## 正确边界

- 历史 `publish_query_labels`、`ask_intent_approval` 请求/结果、SQL 结果必须继续留在 ToolMessage 历史中，供模型和前端读取。
- 不写死“重新执行”关键词流程；系统不替模型判断本轮是否一定延续上一轮。
- `service_states` 是执行安全锚点，不是语义裁判。旧 `cancelled` 不能直接进入 SQL，但可以提供历史检索上下文，让模型重新发布标签并重新审批。
- SQL 阶段只接受当前持久化快照中已 `approved` 或策略放行的状态；不能从取消态或纯文本历史中伪造授权。

## 修复

- 撤回 `get_active_service_state()` 的最新可见 turn 过滤，保留当前持久化 DataAgent 快照作为历史重入锚点。
- `publish_query_labels` 允许从 `cancelled` / `succeeded` / `failed` 等仍带有效 retrieval 的快照重新发布标签。
- 当模型在新可见用户消息后重发标签时，新标签快照使用最新可见用户消息作为 `turn_id`，并写入 `payload.resumed_from` 记录来源快照。
- `merge_service_states()` 只在 `resumed_from` 精确匹配当前历史快照时，允许新 turn 的 `labels_published` 替换旧状态；旧 SQL/审批结果仍不能乱序覆盖当前轮。
- `ask_intent_approval` 遇到已取消快照时提示模型先重新发布标签，而不是沉默地停止。
- SQL 阶段撤回基于 `service_states` 列表扫描历史授权的错误方案，只使用当前持久化快照作为执行门禁。

## 验证

- 新增/更新回归覆盖：
  - 新可见消息后，旧取消快照仍可作为历史重入锚点。
  - 旧取消后再次调用 `publish_query_labels` 成功生成新标签快照，并可被 reducer 接受。
  - 图级重入后旧标签/审批 ToolMessage 仍留在 `messages` 历史中。
  - 旧 approved 快照仍可直接进入 SQL。
  - 旧 cancelled 快照直接 SQL 会被 `SQL_STAGE_NOT_APPROVED` 拒绝。
