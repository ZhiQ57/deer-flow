# SQL 错误明细与执行结果上下文 Review

## 1. 问题定位

前一版实现曾在 SQL Executor 中读取 custom-agent 的静态表/字段列表，导致当前 SQL 在数据库执行前出现 `SQL_TABLE_NOT_ALLOWED`。这套配置与 Schema-RAG 的检索约束重复，也无法承担未来按当前登录用户实时查询权限的职责。本次已删除 `allowed_tables`、`allowed_columns` 配置和对应的 Executor 校验。`manual_ui` 现在只保留 Schema、AST、只读、方言和数据源绑定校验；`subagent` 仍要求 SQL 使用的表和字段存在于当前 TableRAG registry。

删除静态表/字段 allowlist 后，使用当前本地 MySQL DSN 执行用户示例 SQL，Executor 直接得到数据库主错误：

```text
Unknown column 'fd.FemaleInfertilityDiagnosis' in 'where clause'
```

## 2. 修改结果

### 2.1 Backend

- `SqlValidationResult` 增加 `error_message`。
- 删除 `allowed_tables`、`allowed_columns` 配置字段。
- 删除 `SQL_TABLE_NOT_ALLOWED`、`SQL_COLUMN_NOT_ALLOWED` 静态配置校验。
- 保留 `SQL_SCHEMA_NOT_ALLOWED`，防止跨越服务端配置的 Schema。
- Gateway 保留校验结果中的 `error_message`。
- 数据库驱动异常统一保留最多 500 字符的主错误信息。
- 错误文本只执行 DSN、URI 凭据、控制字符和长度处理，不使用泛化文案替换数据库主错误。
- `data_execute_sql` 继续直接返回 Executor 结果，SQL SubAgent 可以从 ToolMessage JSON 读取相同的 `error_message`。

### 2.2 Frontend

- SQL Result Panel 同时显示 `error_code` 和数据库主错误。
- DataAgent SQL Result Artifact 解析器保留 `error_category`、`error_message`、`retryable` 和 `recommended_action`。
- Query Result Card 显示相同错误明细。
- SQL Result Panel 支持选中 SQL、错误或结果文本。
- 点击“添加到对话”后，选中文本通过现有 Sidecar 引用机制加入主输入框，随下一次请求发送给模型。

## 3. 验证

- Backend SQL Executor 和 Gateway Router：21 passed。
- Frontend SQL Result Panel、Query Result Card 和 artifact parser：13 passed。
- Frontend 全量单元测试：759 passed。
- Frontend ESLint 和 TypeScript：通过。
- Frontend 生产构建：通过。
- Backend Ruff check 和 format check：通过。
- 使用当前本地 MySQL DSN 和用户示例 SQL 进行只读集成检查，Executor 直接返回 `error_category=unknown_column` 和数据库主错误 `Unknown column 'fd.FemaleInfertilityDiagnosis' in 'where clause'`。
- SQL 代码块成功执行，以及错误选择和“添加到对话”的 Playwright E2E：2 passed。默认 Playwright revision 在本机缺少对应 Headless Shell，验证时使用已安装的 Google Chrome channel，不修改项目默认 E2E 配置。

## 4. 配置要求

DataAgent custom-agent 只保留 Schema 约束：

```yaml
service_ability:
  sql_execution:
    allowed_schemas:
      - text2sql
```

Schema-RAG 负责 DataAgent/SQL SubAgent 的表字段证据约束；SQL Executor 不再从配置文件读取表字段列表。未来新增用户权限校验时，应在 SQL Executor 的执行前授权层接入，不需要恢复这两个配置字段。
