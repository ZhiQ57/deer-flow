# DataAgent 历史意图重入与自由决策修复计划

## 任务背景

用户明确指出：上一版“按最新可见 turn 过滤 active state / 从系统侧替模型选择历史状态”的修改方向是错误的。正确目标不是让系统把每次“重新执行”硬路由到某个固定流程，也不是把历史对话或历史 ToolMessage 从模型上下文里丢掉。

正确理解如下：

- 初始场景：用户正常发送查询，DataAgent 输出查询意图标签与审批组件；用户在意图选择时点击“取消查询”，于是系统停止本次任务。
- 再次对话：用户只发送“重新执行”四个字，真实意图是让模型读取历史对话，自行判断是否延续上一次查询任务。
- 如果历史里已经有人类确认过同一查询意图，模型可以直接进入 SQL 阶段。
- 如果历史里没有确认，或者上一次是取消，模型应该可以再次调用 `publish_query_labels` / `ask_intent_approval`，重新展示标签/审批组件，让用户再次确认。
- 系统不能因为 `service_states.stage=cancelled` 就强制阻塞 `publish_query_labels`，否则模型无法再次弹出标签选择框。
- 系统不能写死关键词流程，例如“用户说重新执行就必须 sqlrag_retrieve -> publish_query_labels -> ask_intent_approval -> SQL”。下一步应由模型基于历史消息自行决定。
- 系统职责是提供安全边界和可恢复反馈：SQL 必须有 approved 快照；取消态不能直接绕过审批执行 SQL；标签发布不能被旧取消态死锁。
- 历史消息必须继续存在于模型上下文与前端历史中，尤其是 `publish_query_labels`、`ask_intent_approval` 请求/结果、SQL 结果这些 ToolMessage artifact。

上一版错误点需要撤回：

- 不应把 `get_active_service_state()` 改成只返回最新可见用户消息对应的状态；这会让历史任务锚点从执行上下文中消失。
- 不应要求“延续历史意图也必须先重新 TableRAG”；模型可以选择重新检索，也可以基于历史检索快照重新发布标签/审批。
- 不应把 SQL 历史续用理解成扫描 `service_states` 列表；当前 reducer 每个 service 只保留一个活动快照，真正的语义判断来自消息历史，执行安全锚点来自当前持久化 service state。

## Todo

1. 现状核查
   - [X] 1.1 读取 DataAgent `state`、`query_labels`、`approval`、`sql_stage`、`table_rag` 中间件现状。
   - [X] 1.2 找出上一版错误改动残留，标记需要撤回的逻辑。
   - [X] 1.3 明确不丢历史消息、不新增关键词固定流程的实现边界。

2. 状态与标签重入修复
   - [X] 2.1 撤回 `get_active_service_state()` 的最新可见 turn 过滤，保留历史 active snapshot。
   - [X] 2.2 修改 `publish_query_labels`：旧 `cancelled/succeeded/failed` 不再死锁标签发布；模型新一轮可基于历史检索快照重新发布标签。
   - [X] 2.3 新一轮重发标签时使用最新可见用户消息作为新 `turn_id`，并在 payload 记录来源快照，避免历史语义被静默覆盖。
   - [X] 2.4 修改 reducer：允许“新可见 turn 基于旧终态快照重发 labels_published”替换旧终态，但不放开旧 SQL/审批结果乱序覆盖当前状态。

3. 审批与 SQL 边界
   - [X] 3.1 保持 `ask_intent_approval` 只消费有效标签快照；取消后应通过重新发布标签进入新的审批，而不是复活旧取消。
   - [X] 3.2 SQL 阶段只允许当前持久化快照中已 approved 或策略放行的状态；旧 cancelled 不得直接 SQL。
   - [X] 3.3 SQL 阶段拒绝时返回模型可理解的安全错误，不把流程卡成无反馈死锁。

4. 回归测试
   - [X] 4.1 覆盖“历史取消后，用户说重新执行，模型再次 publish_query_labels 成功并生成新标签卡”。
   - [X] 4.2 覆盖“历史取消后，模型直接 SQL 被拒，但再次发标签不被拒”。
   - [X] 4.3 覆盖“历史已 approved，用户追问重新生成 SQL 时可以直接 SQL”。
   - [X] 4.4 覆盖“重入后旧 ToolMessage 历史仍在 messages 中，没有被清空”。

5. 文档与验证
   - [X] 5.1 更新 DataAgent 使用 API、数据流走向、README/SOUL、backend/AGENTS 说明。
   - [X] 5.2 更新 bug 记录，避免保留上一版错误设计描述。
   - [X] 5.3 运行 ruff 与 DataAgent 定向测试。
