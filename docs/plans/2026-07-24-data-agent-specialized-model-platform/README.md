# DataAgent 开发计划

| 项目 | 内容 |
|---|---|
| 状态 | SQL Executor、SQL SubAgent、Gateway、前端手动执行和错误上下文链路完成 |
| 已完成任务 | SQL Executor、SQL SubAgent 修复重试、Gateway SQL API、前端执行按钮、原始错误展示和结果引用 |
| 下一任务 | 配置真实只读 PostgreSQL，并补充成功查询的跨数据库集成验收 |
| 后续任务 | SQL SubAgent 模型闭环、Analysis SubAgent、Chart SubAgent、Agent Contract |
| 实现范围 | 正式 `deerflow.*`、Gateway、Frontend |
| 排除范围 | `backend/packages/harness/deerflow-dev/` |

## 1. 项目目标

DataAgent 最终使用多个独立模型完成数据查询和数据分析：

- DataAgent Lead 绑定独立训练的业务模型，负责用户意图、业务口径、Schema-RAG、任务路由和最终回答。
- SQL SubAgent 绑定独立训练的 SQL 模型，负责 SQL 生成、SQL 错误修复和 SQL 任务闭环。
- Analysis SubAgent 负责统计分析和机器学习任务。
- Chart SubAgent 负责图表配置和图表生成。

Analysis SubAgent、Chart SubAgent 和完整的 Agent 间数据合同属于后续任务，不进入当前 SQL Executor 开发范围。

## 2. 当前首要任务

当前只开发以下功能：

1. SQL Executor。
2. Gateway SQL 执行接口。
3. 前端 SQL 代码块“执行”按钮。
4. SQL 执行结果右侧面板。
5. 现有 `sql-subagent` 接入新的 SQL Executor。

当前目标流程：

```text
前端 SQL 代码块
  -> 点击“执行”
  -> Gateway SQL API
  -> SQL 校验
  -> SQL Executor
  -> 外部 MySQL/PostgreSQL
  -> 返回未经过 LLM 处理的结构化数据库结果
  -> 前端右侧面板显示结果
```

现有 SQL SubAgent 目标流程：

```text
sql-subagent
  -> data_validate_sql
  -> data_execute_sql
  -> SQL Executor
  -> 外部数据库
  -> ToolMessage
  -> sql-subagent
```

生产数据库不放入 sandbox。SQL Executor 从 DataAgent custom-agent 的 `service_ability.sql_execution` 读取执行配置，并使用配置引用的数据库凭据连接外部数据库。

## 3. 当前架构决策

### 3.1 SQL Executor 放置位置

SQL Executor 核心放在 Harness 正式包中，例如：

```text
backend/packages/harness/deerflow/agents/service_agent/sql_executor.py
```

原因：

- Gateway 可以导入 `deerflow.*`。
- `data_execute_sql` 位于 Harness，也需要调用同一 Executor。
- Harness 不能反向导入 `app.*`。
- Gateway API 和 SQL SubAgent 必须复用相同执行逻辑，不能维护两套数据库执行代码。

### 3.2 Gateway 与 SQL SubAgent 的调用方式

首版使用共享 Service，不让 SQL SubAgent 通过 HTTP 调用同进程 Gateway：

```text
Gateway Router -> SqlExecutionService
data_execute_sql -> SqlExecutionService
```

如果以后将 SQL Executor 拆成独立内网服务，再把 Service 实现替换为 HTTP/RPC Client。Gateway Router 和 `data_execute_sql` 的上层调用方式保持不变。

### 3.3 原始结果定义

“原始数据库结果”表示结果未经过 LLM 总结、改写或解释。API 返回 JSON 可序列化结构：

```json
{
  "ok": true,
  "database_type": "mysql",
  "columns": ["name", "count"],
  "rows": [
    {"name": "A", "count": 10}
  ],
  "row_count": 1,
  "returned_row_count": 1,
  "truncated": false,
  "duration_ms": 18.4
}
```

数据库驱动对象、连接对象、DSN、密码和内部异常堆栈不能返回前端。

## 4. 阶段一：SQL Executor 核心

### 4.1 目标

将当前 `sql_tools.py` 中的数据库连接、查询执行、结果限制和错误映射抽取为可复用 SQL Executor。

### 4.2 配置来源

Executor 从指定 DataAgent 配置读取：

```yaml
service_ability:
  type: data_query
  version: 1
  sql_execution:
    enabled: true
    database_type: mysql
    dsn_env: DATA_AGENT_MYSQL_DSN
    readonly: true
    statement_timeout_seconds: 10
    max_execution_attempts: 3
    max_rows: 100
    max_cell_chars: 2000
    max_result_chars: 100000
    allowed_schemas: []
```

### 4.3 Executor 接口

```python
class SqlExecutionService:
    def validate(self, request: SqlValidationRequest) -> SqlValidationResult: ...

    def execute(
        self,
        request: SqlExecutionRequest,
        validation: SqlValidationResult,
    ) -> SqlExecutionResult: ...
```

Gateway 和 SQL SubAgent 使用同一个 Service。

### 4.4 两种校验上下文

SQL Executor 需要支持两个调用来源：

1. `subagent`：必须绑定当前 Query Snapshot、Evidence、`validation_digest` 和执行授权。
2. `manual_ui`：由用户点击代码块执行；必须执行 AST、只读、方言、Schema 和数据源绑定校验，但不伪造 Query Snapshot。

两个来源最终使用相同数据库执行和结果限制逻辑。

### 4.5 Todo

- [X] 1.1 定义 `SqlValidationRequest/Result`。
- [X] 1.2 定义 `SqlExecutionRequest/Result`。
- [X] 1.3 从 `sql_tools.py` 抽取 PostgreSQL/MySQL 连接和执行逻辑。
- [X] 1.4 抽取结果 JSON 转换、行数限制、单元格限制和总字符限制。
- [X] 1.5 实现安全数据库错误分类：`error_category`、`retryable`、`recommended_action` 和可选脱敏 `error_message`。
- [X] 1.6 实现 `subagent` 校验上下文。
- [X] 1.7 实现 `manual_ui` 校验上下文。
- [X] 1.8 保证 SQL Executor 不记录或返回 DSN、密码和 Token。
- [X] 1.9 增加 PostgreSQL/MySQL 驱动、结果预算和 SQL 修复重试单元测试。
- [ ] 1.10 在配置真实只读数据库后运行 PostgreSQL/MySQL 集成测试。

### 4.6 阶段验收

- [X] 可以在单元测试中根据 DataAgent 配置解析目标数据库并执行单条只读 `SELECT/WITH`。
- [X] 可以返回结构化列、行数、数据行、截断状态和耗时。
- [X] DDL、DML、多语句、危险函数和越权表列会在驱动调用前被拒绝；超时映射为稳定错误合同。
- [X] 数据库连接和结果处理代码已从 SQL Tool 抽取到 `SqlExecutionService`，并由阶段二 Gateway API 复用。

## 5. 阶段二：Gateway SQL API

### 5.1 API

新增正式 Router，例如：

```text
POST /api/threads/{thread_id}/sql/execute
```

请求：

```json
{
  "agent_name": "data-agent",
  "sql": "SELECT ...",
  "source": "manual_ui"
}
```

响应使用 `SqlExecutionResult`。

### 5.2 Gateway 处理顺序

```text
认证用户
  -> 校验 thread 所有权
  -> 加载当前用户的 custom-agent 配置
  -> 解析 service_ability
  -> 检查 sql_execution.enabled
  -> 校验 SQL 长度和请求字段
  -> SqlExecutionService.validate()
  -> SqlExecutionService.execute()
  -> 返回安全结果
```

同步数据库驱动调用必须移出 ASGI Event Loop。

### 5.3 Todo

- [X] 2.1 新增 `sql_execution.py` Gateway Router。
- [X] 2.2 定义 Pydantic Request/Response。
- [X] 2.3 复用现有用户身份和 thread 所有权校验。
- [X] 2.4 按 `agent_name` 加载 DataAgent 配置。
- [X] 2.5 拒绝未启用 `data_query/sql_execution` 的 Agent。
- [X] 2.6 使用共享 Service 的 `aexecute()` 把同步数据库调用移出 Event Loop。
- [X] 2.7 返回稳定 HTTP 状态码和 SQL 错误码。
- [X] 2.8 增加 Router 鉴权、配置、校验、成功和失败测试。

### 5.4 阶段验收

- [X] 已登录用户可以执行自己线程中的 DataAgent SQL。
- [X] 普通 Agent、其他用户线程和无 SQL 配置的 Agent 无法执行。
- [X] API 不泄露数据库凭据和原始异常堆栈。
- [X] Gateway 数据库驱动调用通过 `aexecute()` 在线程池运行，不阻塞 Event Loop。

## 6. 阶段三：前端 SQL 执行按钮和结果面板

### 6.1 交互

SQL Markdown 代码块头部增加“执行”按钮，位置在下载和复制按钮之前：

```text
sql                       [执行] [下载] [复制]
------------------------------------------------
SELECT ...
```

按钮规则：

- 仅对语言为 `sql` 的 fenced code block 显示。
- 消息仍在流式输出时禁用。
- 非 DataAgent 或 `sql_execution_enabled=false` 时不显示。
- 执行中显示 Loading，并禁止重复提交。
- 点击后自动打开右侧 SQL Result Panel。

### 6.2 Streamdown 接入

当前消息 Markdown 使用 Streamdown。实现 SQL CodeBlock Renderer，复用 Streamdown 导出的：

- `CodeBlock`
- `CodeBlockDownloadButton`
- `CodeBlockCopyButton`

SQL CodeBlock 在现有 Action 区增加 Execute Button；非 SQL CodeBlock 保持原有行为。

不要修改 `frontend/src/components/ai-elements/` 下的生成组件。

### 6.3 前端模块

建议新增：

```text
frontend/src/core/sql-execution/
  api.ts
  types.ts

frontend/src/components/workspace/sql-execution/
  context.tsx
  sql-code-block.tsx
  sql-result-panel.tsx
```

### 6.4 右侧面板

扩展 `ChatBox` 的 `RightPanelKind`：

```text
"sidecar" | "artifacts" | "browser" | "sql-result"
```

SQL Result Panel 显示：

- SQL 文本。
- 执行状态。
- 数据库类型。
- 列名和数据表格。
- 行数、截断状态和耗时。
- 错误码。
- 关闭和重新执行操作。

移动端使用现有 Sheet，桌面端使用现有右侧 Panel。

### 6.5 Todo

- [X] 3.1 新增 SQL Execution API Client 和 TypeScript 类型。
- [X] 3.2 新增 SQL Execution Context，保存当前 SQL、Loading、Result 和 Error。
- [X] 3.3 新增 SQL CodeBlock Renderer。
- [X] 3.4 在 SQL 代码块 Action 区增加 Execute Button。
- [X] 3.5 扩展 ChatBox 支持 `sql-result` RightPanel。
- [X] 3.6 实现结果表格、空结果、截断结果和错误展示。
- [X] 3.7 增加中英文文案。
- [X] 3.8 增加按钮显示、执行状态和面板渲染单元测试。
- [X] 3.9 增加点击 SQL 执行按钮的前端 E2E 测试。

### 6.6 阶段验收

- [X] SQL 代码块出现执行按钮，位于下载和复制操作之前。
- [X] 点击按钮后可以调用 Gateway API。
- [X] 成功结果在右侧面板以表格显示。
- [X] 空结果、错误、超时和截断结果可以正确显示。
- [X] 非 SQL 代码块和普通 Agent 不受影响。

## 7. 阶段四：接入现有 SQL SubAgent

### 7.1 接入方式

开发完 SQL Executor 后，现有 SQL SubAgent不会自动使用它。必须修改 `data_execute_sql` 的实现，使其委托给 `SqlExecutionService`：

```text
data_execute_sql
  -> 验证最近一次 data_validate_sql 结果
  -> SqlExecutionService.execute(source="subagent")
  -> ToolMessage
```

同进程运行时直接调用 Service，不通过 HTTP 调用 Gateway。将来 Executor 独立部署后，由 Service 内部切换为 HTTP/RPC Client。

### 7.2 保持现有约束

- `sql_only` 不装配 `data_execute_sql`。
- 必须先调用 `data_validate_sql`。
- 修复 SQL 后必须重新校验。
- SQL SubAgent 只能执行当前 Snapshot 的 SQL。
- 父 DataAgent 只信任 SQL ToolMessage，不信任 SubAgent 自由文本。

### 7.3 Todo

- [X] 4.1 修改 `data_execute_sql` 调用 `SqlExecutionService`。
- [X] 4.2 保留 `validation_digest`、Snapshot 和 Binding 校验。
- [X] 4.3 保留 SQL Result Artifact 和 ToolMessage 格式。
- [X] 4.4 增加 SQL SubAgent 成功执行测试。
- [X] 4.5 增加字段错误、超时、连接和通用数据库错误的结构化分类测试。
- [X] 4.6 验证 SQL 工具会话可读取错误、重新校验修复 SQL，并在执行预算内再次执行。

### 7.4 阶段验收

- [X] SQL SubAgent 使用新的 SQL Executor。
- [X] SQL SubAgent 可以完成生成、校验、执行和有限错误修复闭环。
- [X] Frontend 和 SQL SubAgent 使用同一 `SqlExecutionService` 数据库执行实现。
- [X] Harness 内不存在第二套数据库连接和结果转换逻辑。

## 8. 阶段五：测试、文档与交付

- [X] 5.1 运行 Backend SQL Executor、SQL SubAgent 和 Gateway Router 相关测试。
- [X] 5.2 运行 Backend Ruff 和格式检查。
- [X] 5.3 运行 Frontend Unit、Typecheck、Lint 和生产构建。
- [X] 5.4 运行 SQL Execute Button E2E。
- [X] 5.5 更新 `docs/guide/used-api.md`。
- [X] 5.6 更新 `backend/AGENTS.md` 和 `frontend/AGENTS.md`。
- [X] 5.7 更新 DataAgent README 和用户配置说明。
- [X] 5.8 编写 `docs/reviews/` Review，记录验证结果和遗留风险。
- [ ] 5.9 测试通过后合并回 `dev` 并推送 `origin/dev`。

## 9. 阶段六：SQL 错误明细与执行结果上下文

### 9.1 问题

数据库访问权限不应由 custom-agent 配置中的静态表/字段列表承担。Schema-RAG 已经负责检索和 SQL 生成阶段的数据范围约束，未来 SQL Executor 还会根据当前登录用户查询实时权限。静态 `allowed_tables`、`allowed_columns` 会造成配置重复、Schema 变更后失效，并且可能在数据库执行前屏蔽真正的数据库错误。

### 9.2 目标

- SQL Executor 不再读取或校验 `allowed_tables`、`allowed_columns`。
- `allowed_schemas` 继续用于限制 SQL 访问的数据库 Schema。
- `source=subagent` 继续要求当前 TableRAG registry 中存在 SQL 使用的表和字段。
- `source=manual_ui` 不伪造 TableRAG registry，只执行 AST、只读、方言、Schema 和数据源绑定校验。
- 数据库执行失败返回数据库驱动的主错误信息，只移除 DSN、凭据、控制字符和异常堆栈。
- SQL Result Panel 同时展示错误码和错误信息，不用二次文案替换数据库主错误。
- 用户可选中 SQL、错误信息或查询结果文本，点击“添加到对话”，作为下一次请求的引用上下文。
- DataAgent SQL Result Artifact 保留相同错误信息，使消息卡和后续模型上下文使用同一份执行事实。

### 9.3 Todo

- [X] 6.1 从 SQL Execution 配置模型删除 `allowed_tables`、`allowed_columns`。
- [X] 6.2 从数据源绑定和 Query Snapshot 完整性合同删除静态表/字段 allowlist。
- [X] 6.3 删除 manual UI 的表/字段静态 allowlist 校验。
- [X] 6.4 保留 subagent 的 TableRAG registry 表/字段证据校验。
- [X] 6.5 SQL Result Panel 展示数据库主错误。
- [X] 6.6 SQL Result Artifact 保留执行错误明细。
- [X] 6.7 SQL Result Panel 支持选中文本并“添加到对话”。
- [X] 6.8 迁移 Backend 测试、配置示例和开发文档。

### 9.4 验收

- [X] 配置模型不再接受 `allowed_tables`、`allowed_columns` 字段。
- [X] manual UI 不因静态表/字段列表缺失而拒绝合法只读 SQL。
- [X] subagent 仍不能执行不在当前 TableRAG registry 的表或字段。
- [X] MySQL 1054 等可修复错误在页面显示数据库主错误，例如 `Unknown column '...' in 'where clause'`。
- [X] API 和页面不显示 DSN、密码、驱动对象或异常堆栈。
- [X] 用户选中 SQL 执行结果文本后，可以把引用内容加入下一次对话。
- [X] SQL SubAgent 和前端手动执行继续复用同一 `SqlExecutionService`。

## 10. SQL SubAgent 卡住问题修复

### 10.1 问题定位

- 旧的 `data_query` `needs_refinement` 快照会跨越新的可见用户消息继续存在。
- `publish_query_labels` 因旧阶段状态被拒绝，前端没有成功的标签 artifact，因此不会显示审核卡。
- SQL 阶段门禁返回普通错误 ToolMessage，没有 `subagent_status`，前端和 delegation ledger 无法把 task 标记为失败。
- 历史 checkpoint 中的旧 `SQL_*` JSON 错误会持续被当作 `in_progress`，影响模型上下文和页面状态。

### 10.2 实现

- [X] 10.1 在正式 `deerflow.agents.service_agent` 增加 `DataAgentTurnResetMiddleware`，并在 TableRAG middleware 前注册。
- [X] 10.2 新可见用户轮次先写入当前数据源的 `idle` 快照；隐藏 human-input 确认不创建新轮次。
- [X] 10.3 SQL 阶段拒绝委派时返回带 `subagent_status=failed`、`subagent_error` 的 task ToolMessage。
- [X] 10.4 delegation ledger 和前端任务卡兼容历史 `SQL_*` JSON 错误并恢复为终态失败。
- [X] 10.5 新运行开始时将带旧 `run_id` 且没有终态结果的 delegation 收敛为失败。
- [X] 10.6 增加轮次隔离、任务终态、历史状态迁移和前端解析回归测试。

### 10.3 验收

- [X] 新用户消息不会复用上一轮 `needs_refinement`、标签或 SQL approval 快照。
- [X] 标签发布成功后可以生成现有 Query Intent / human-input 审核卡。
- [X] SQL SubAgent 被门禁拒绝时，任务卡显示失败，不再永久显示“子任务运行中”。
- [X] 旧 checkpoint 的 SQL 阶段错误不会继续向模型声明存在一个 `in_progress` SQL SubAgent。

## 11. 后续任务

以下任务在 SQL Executor 完成后单独立项：

1. SQL SubAgent 独立 SQL 模型训练和评测。
2. DataAgent Lead 独立业务模型训练和 Schema-RAG 评测。
3. Agent 间完整 Contract 和 Artifact 版本管理。
4. Analysis SubAgent。
5. Chart SubAgent。
6. 多模型灰度、回滚和独立评测体系。

相关历史规划保留在本目录其他阶段文档中，但不作为当前 SQL Executor 的前置开发任务。
