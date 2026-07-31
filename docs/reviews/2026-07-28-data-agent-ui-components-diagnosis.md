# DataAgent 标签与审批组件不显示：诊断结论

## 背景

这次不是 `QueryIntentCard` 不会渲染，而是上游没有把消息推进到它能吃的合同上。

## 直接结论

真正的断点在后端中间层，不在前端卡片：

1. `sqlrag_retrieve` 的真实返回是 `ToolMessage.content` 的列表形态，像 `[{type:"text", text:"{...json...}"}]`。
2. `backend/packages/harness/deerflow/agents/service_agent/table_rag_middleware.py::_json_payload()` 只接受 `str` 或 `Mapping`，所以这类内容会被判成 `None`。
3. 于是 `TableRagStageMiddleware` 把 `service_states.stage` 写成 `needs_refinement`，并落一个 `TABLERAG_EMPTY_OR_FAILED`。
4. 接着 `query_labels_middleware.py` 拦住 `publish_query_labels`，因为它只允许在 `retrieving` / `labels_published` 阶段发布。

所以标签和审批组件没出来，根因是“检索结果没被当前中间件解析成有效 retrieval”，后续发布标签的门直接被关上了。

## 关键证据

- 前端解析入口在 `frontend/src/core/messages/data-query.ts`，`parseDataQueryLabelsArtifact()` 对 `kind/service_name/snapshot_id/retrieval_digest/approval` 等字段要求很严格。
- 前端分组入口在 `frontend/src/core/messages/utils.ts`，只有 `isDataQueryLabelsToolMessage()` 命中才会生成 `assistant:query-intent`。
- 前端渲染入口在 `frontend/src/components/workspace/messages/message-list.tsx`，`QueryIntentCard` 只会在上述分组存在时渲染。
- 但这次真实运行里，问题先发生在 `backend/packages/harness/deerflow/agents/service_agent/table_rag_middleware.py`。

## 本地数据证据

我检查了本地运行库：

- `publish_query_labels`：`checkpoints=105`，`writes=32`
- `data_query_labels`：`checkpoints=0`，`writes=0`
- `sqlrag_retrieve`：`checkpoints=202`，`writes=87`
- `tablerag_tablerag_retrieve`：`checkpoints=1005`，`writes=230`

这说明真实落库的运行里，检索工具很多，但没有形成可进入标签发布阶段的有效检索上下文。

我还解出了一条真实样本：

- `sqlrag_retrieve` 的 `ToolMessage.content` 是 `[{ "type": "text", "text": "{...json...}" }]`
- 随后 `service_states` 变成 `stage = needs_refinement`
- `publish_query_labels` 再被拒绝，返回 `当前查询阶段不允许重复发布标签。`

## 根因判断

根因不是“卡片组件写错了”，而是“TableRAG 检索结果 → DataAgent service_state → 标签发布”这条链路在中间断了：

- `sqlrag_retrieve` 返回了 list 形态的 content；
- `TableRagStageMiddleware._json_payload()` 没解析这种形态；
- `build_retrieval_context()` 没拿到有效 payload；
- `service_states` 被写成 `needs_refinement`；
- `publish_query_labels` 因阶段不合法被拦截。

## 最小修复建议

1. 后端先让 `_json_payload()` 支持 `ToolMessage.content` 的 list/text-block 形态，或让 `sqlrag_retrieve` 直接返回可被解析的字符串/映射。
2. 再补一个集成测试，把“`sqlrag_retrieve` → `needs_refinement`/`retrieving` → `publish_query_labels`”的阶段切换跑通。
3. 前端 custom event 兜底可以做，但它是次要增强，不是这次的主断点。

## 已验证

- 前端单测：`tests/unit/core/messages/data-query.test.ts`
- 前端单测：`tests/unit/core/messages/utils.test.ts`
- 前端单测：`tests/unit/components/workspace/messages/query-intent-card.test.ts`
- 后端回归：`tests/test_data_agent_query_flow.py`
- 后端回归：`tests/test_client_message_serialization.py`
- 后端回归：`tests/test_thread_messages_page.py`

## 当前修复

- 已在 `backend/packages/harness/deerflow/agents/service_agent/table_rag_middleware.py` 兼容 `ToolMessage.content` 的 list/text-block 形态。
- 已补回归：`backend/tests/test_data_agent_sqlrag_adapter.py` 现在同时覆盖字符串内容和 text-block 列表内容。

## 本轮重新调试结论

用户重新调试后出现的 3 个现象，根因分别是：

1. **原始字符串外露**：前端消息分组对未知 ToolMessage 有兜底渲染逻辑，`entity_extract_tool` 和 SQL 子任务内部 JSON 没有被标记为隐藏/摘要，导致机器协议被当作普通消息显示。
2. **标签参数抖动**：`publish_query_labels` 的模型可见 schema 暴露了 `confidence`，中间件还会解析并下发该字段。它不参与 SQL 真相源，却会诱导模型多次调整参数，浪费 Token。
3. **意图卡显示内部值**：`QueryIntentCard` 直接展示 `artifact.intent` 的内部枚举值，并把 TableRAG Evidence 摘要列表完整展开，造成 `aggregation`、`[table] ...` 等内部提示进入用户界面。

## 本轮修复

- 前端新增 DataAgent 内部协议摘要识别：实体抽取 JSON、`data_query_sql_response`、`data_query_sql_result`、`SQL_*` 错误 JSON 不再原样渲染。
- `isHiddenFromUIMessage()` 隐藏 `entity_extract_tool` 结果和非 `task` 的内部 DataAgent JSON；`task` 消息保留给子任务状态机，但展示前会被摘要化。
- 子任务步骤、子任务结果和子任务错误统一经过摘要处理，避免失败态展开时显示内部 SQL 合同。
- `publish_query_labels` 移除 `confidence` 参数；`QueryLabelsMiddleware` 忽略旧调用里混入的 `confidence`，artifact 不再输出该字段。
- 审批自动通过逻辑不再依赖模型自报置信度；在检索绑定完整、标签完整、显式无歧义时可自动通过，缺失 `ambiguities` 仍转人工确认。
- `QueryIntentCard` 将 `aggregation/ranking/...` 映射为中文展示名，TableRAG Evidence 改为“已绑定 N 条依据”的摘要，不再展示原始 `[table]` 列表。
