# 阶段 2：Contract、Artifact 与状态

## 1. 目标

冻结 Agent 之间的数据接口，使模型替换不影响 Runtime 和其他 Agent。

## 2. Contract 列表

```text
QuerySnapshotV1
SQLTaskV1
SQLTaskResultV1
AnalysisTaskV1
AnalysisResultV1
ChartTaskV1
ChartResultV1
```

## 3. 公共字段

所有任务和结果包含：

```json
{
  "contract_version": 1,
  "task_id": "task-...",
  "parent_task_id": null,
  "snapshot_id": "sha256:...",
  "data_source_id": "source-...",
  "source_artifact_id": null,
  "model_id": "sql-model",
  "model_version": "2026-07-24",
  "prompt_version": "v1",
  "status": "running",
  "error_code": null
}
```

## 4. QuerySnapshotV1

包含：

- 原始问题和规范化问题。
- 指标、维度、过滤条件、时间范围、粒度和排序。
- 已确认的业务口径。
- 未解决歧义。
- Schema、Table、Column、Value 和 Join Evidence 引用。
- Evidence digest。
- 用户动作：`execute`、`sql_only` 或 `cancel`。

## 5. SQLTaskResultV1

状态：

- `succeeded`
- `needs_evidence`
- `validation_failed`
- `execution_failed`
- `budget_exhausted`
- `failed`

可信字段必须从 SQL ToolMessage 重建：

- 最终 SQL。
- Validation Result。
- Execution Result。
- Attempt 列表。
- 行数、列、截断状态和耗时。
- 结构化错误码。

## 6. Artifact

- 大结果写入 Artifact Store，状态只保存引用和摘要。
- Artifact 包含 content hash、schema、row count、size 和 producer task。
- 下游 Agent 只能读取当前任务授权的 Artifact ID。
- Artifact 不保存 DSN、密码、Token 或数据库连接对象。

## 7. State

继续使用 `ThreadState.service_states` 保存 DataAgent 业务状态：

```text
idle
retrieving
awaiting_confirmation
approved
sql_running
sql_succeeded
analysis_running
chart_running
completed
failed
```

新用户轮次、用户修改条件或新 Snapshot 创建后，旧授权和旧执行结果不能继续使用。

## 8. Todo

- [ ] 2.1 为全部 Contract 建立 Pydantic 模型和 JSON Schema。
- [ ] 2.2 定义状态枚举、错误码和可重试属性。
- [ ] 2.3 实现 QuerySnapshot digest 和版本校验。
- [ ] 2.4 实现 Artifact metadata 和授权引用。
- [ ] 2.5 实现 SQL ToolMessage 到 SQLTaskResult 的可信投影。
- [ ] 2.6 实现 State reducer 的阶段前进、旧 Snapshot 拒绝和新轮次重置。
- [ ] 2.7 为前端建立对应 TypeScript 类型和运行时解析器。
- [ ] 2.8 增加 Contract、Reducer、Artifact 和序列化测试。

## 9. 阶段退出条件

- [ ] Contract 可以被后端和前端共同解析。
- [ ] 旧 Contract、未知版本和错误字段 fail closed。
- [ ] SubAgent 自由文本不能覆盖 Tool Artifact。
- [ ] 大结果不进入 Checkpoint 消息正文。
