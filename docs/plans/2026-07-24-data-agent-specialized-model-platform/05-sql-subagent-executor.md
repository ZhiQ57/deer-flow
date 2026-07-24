# 阶段 4：SQL SubAgent 与 SQL Executor

## 1. 目标

让独立 SQL 模型在 SQL SubAgent 内完成 SQL 生成、校验、执行错误修复和有限重试。

## 2. SQL SubAgent 输入

`SQLTaskV1` 包含：

- Query Snapshot。
- Schema、Table、Column、Value 和 Join Evidence。
- SQL 方言。
- 允许的 Schema、Table 和 Column。
- 用户动作：`execute` 或 `sql_only`。
- 最大尝试次数、超时和结果预算。

SQL SubAgent 不重新调用 Schema-RAG，不读取用户完整对话，不修改已确认业务口径。

## 3. SQL 闭环

```text
SQLTask
  -> SQL Model 生成候选 SQL
  -> data_validate_sql
  -> 校验失败
       -> SQL Model 根据结构化错误修复
       -> 重新校验
  -> 校验成功
       -> data_execute_sql
       -> 执行失败
            -> 可修复：修复并重新校验
            -> Evidence 不足：返回 needs_evidence
            -> 不可修复：返回 failed
       -> 执行成功
            -> 返回 SQLTaskResult
```

## 4. SQL 校验

确定性校验包括：

- 单条 `SELECT` 或 `WITH`。
- SQL 方言匹配。
- 禁止 DDL、DML、锁、文件写入、多语句和危险函数。
- Schema、Table 和 Column 必须来自允许范围和当前 Evidence。
- 自动限制最大行数。
- 生成绑定 Snapshot、Evidence 和数据源的 `validation_digest`。

## 5. SQL Executor

定义统一接口：

```python
class SqlExecutionProvider(Protocol):
    async def execute(self, request: SqlExecutionRequest) -> SqlExecutionResult: ...
```

实现：

- `DirectSqlExecutionProvider`：进程内执行，用于本地开发和首版联调。
- `QueryExecutorClientProvider`：调用内网服务，用于隔离生产数据库身份。

两种实现返回相同 Result Contract，SQL SubAgent 不感知部署方式。

## 6. 数据库安全

- 生产数据库位于 sandbox 外部。
- 使用数据库层只读账号。
- 使用只读事务、查询超时和结果预算。
- DSN 和凭据不进入 Prompt、Checkpoint、ToolMessage、Artifact 或日志。
- Executor 重新验证 SQL 和 `validation_digest`，不完全信任 Agent 传入参数。

## 7. 错误分类

| 类型 | 行为 |
|---|---|
| `syntax_error` | SQL SubAgent 修复 |
| `unknown_column` | Evidence 可证明时修复，否则 `needs_evidence` |
| `unknown_table` | 返回 `needs_evidence` |
| `type_mismatch` | SQL SubAgent 修复 |
| `timeout` | 允许一次降复杂度重试或失败 |
| `permission_denied` | 立即失败，不重试 |
| `connection_error` | 按配置重试，不修改 SQL |
| `result_budget_exceeded` | 收紧查询或失败 |

## 8. Todo

- [ ] 4.1 配置独立 SQL 模型并移除 `model: inherit`。
- [ ] 4.2 定义 SQL SubAgent Prompt、Attempt Contract 和重试预算。
- [ ] 4.3 实现 SQL 校验器和 `validation_digest`。
- [ ] 4.4 定义 `SqlExecutionProvider`、Request 和 Result。
- [ ] 4.5 实现 Direct Provider。
- [ ] 4.6 根据生产要求实现 Query Executor Client。
- [ ] 4.7 实现结构化错误分类和可重试规则。
- [ ] 4.8 实现 SQL 修复后强制重新校验。
- [ ] 4.9 实现 SQL ToolMessage 到 SQLTaskResult 的可信投影。
- [ ] 4.10 增加真实只读数据库集成测试和错误修复测试。

## 9. 阶段退出条件

- [ ] SQL SubAgent 独立完成生成、校验、执行和修复。
- [ ] Lead 不参与 SQL 技术修复。
- [ ] `sql_only` 不装配执行工具。
- [ ] SQL 结果无法由模型自由文本伪造。
- [ ] 切换 Execution Provider 不影响 SQL SubAgent Contract。
