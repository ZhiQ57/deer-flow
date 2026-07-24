# DataAgent 开发计划

| 项目 | 内容 |
|---|---|
| 状态 | SQL Executor 与 SQL SubAgent 阶段完成，Gateway/Frontend 阶段待开发 |
| 已完成任务 | SQL Executor、SQL SubAgent 执行与有限修复重试链路 |
| 下一任务 | Gateway SQL API、前端 SQL 执行入口和结果面板 |
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
    allowed_tables: []
    allowed_columns: []
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
2. `manual_ui`：由用户点击代码块执行；必须执行 AST、只读、方言和配置 allowlist 校验，但不伪造 Query Snapshot。

两个来源最终使用相同数据库执行和结果限制逻辑。

### 4.5 Todo

- [X] 1.1 定义 `SqlValidationRequest/Result`。
- [X] 1.2 定义 `SqlExecutionRequest/Result`。
- [X] 1.3 从 `sql_tools.py` 抽取 PostgreSQL/MySQL 连接和执行逻辑。
- [X] 1.4 抽取结果 JSON 转换、行数限制、单元格限制和总字符限制。
- [X] 1.5 实现安全数据库错误分类：`error_category`、`retryable`、`recommended_action` 和可选脱敏 `error_message`。
- [X] 1.6 实现 `subagent` 校验上下文。
- [ ] 1.7 实现 `manual_ui` 校验上下文。
- [X] 1.8 保证 SQL Executor 不记录或返回 DSN、密码和 Token。
- [X] 1.9 增加 PostgreSQL/MySQL 驱动、结果预算和 SQL 修复重试单元测试。
- [ ] 1.10 在配置真实只读数据库后运行 PostgreSQL/MySQL 集成测试。

### 4.6 阶段验收

- [X] 可以在单元测试中根据 DataAgent 配置解析目标数据库并执行单条只读 `SELECT/WITH`。
- [X] 可以返回结构化列、行数、数据行、截断状态和耗时。
- [X] DDL、DML、多语句、危险函数和越权表列会在驱动调用前被拒绝；超时映射为稳定错误合同。
- [X] 数据库连接和结果处理代码已从 SQL Tool 抽取到 `SqlExecutionService`；Gateway 接入留待阶段二。

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
  "source": "manual_ui",
  "snapshot_id": null
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

- [ ] 2.1 新增 `sql_execution.py` Gateway Router。
- [ ] 2.2 定义 Pydantic Request/Response。
- [ ] 2.3 复用现有用户身份和 thread 所有权校验。
- [ ] 2.4 按 `agent_name` 加载 DataAgent 配置。
- [ ] 2.5 拒绝未启用 `data_query/sql_execution` 的 Agent。
- [ ] 2.6 使用 `asyncio.to_thread` 或现有专用执行器运行同步数据库调用。
- [ ] 2.7 返回稳定 HTTP 状态码和 SQL 错误码。
- [ ] 2.8 增加 Router 鉴权、配置、校验、成功和失败测试。

### 5.4 阶段验收

- [ ] 已登录用户可以执行自己线程中的 DataAgent SQL。
- [ ] 普通 Agent、其他用户线程和无 SQL 配置的 Agent 无法执行。
- [ ] API 不泄露数据库凭据和原始异常堆栈。
- [ ] Gateway 并发请求不会阻塞 Event Loop。

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

- [ ] 3.1 新增 SQL Execution API Client 和 TypeScript 类型。
- [ ] 3.2 新增 SQL Execution Context，保存当前 SQL、Loading、Result 和 Error。
- [ ] 3.3 新增 SQL CodeBlock Renderer。
- [ ] 3.4 在 SQL 代码块 Action 区增加 Execute Button。
- [ ] 3.5 扩展 ChatBox 支持 `sql-result` RightPanel。
- [ ] 3.6 实现结果表格、空结果、截断结果和错误展示。
- [ ] 3.7 增加中英文文案。
- [ ] 3.8 增加按钮显示、执行状态和面板渲染单元测试。
- [ ] 3.9 增加点击 SQL 执行按钮的前端 E2E 测试。

### 6.6 阶段验收

- [ ] SQL 代码块出现执行按钮，位置和交互符合目标图。
- [ ] 点击按钮后可以调用 Gateway API。
- [ ] 成功结果在右侧面板以表格显示。
- [ ] 空结果、错误、超时和截断结果可以正确显示。
- [ ] 非 SQL 代码块和普通 Agent 不受影响。

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
- [ ] Frontend 和 SQL SubAgent 使用同一数据库执行实现（待阶段二 Gateway API 与阶段三前端）。
- [X] Harness 内不存在第二套数据库连接和结果转换逻辑。

## 8. 阶段五：测试、文档与交付

- [X] 5.1 运行 Backend SQL Executor、SQL SubAgent、状态和 Harness 边界测试；Gateway Router 尚未进入本阶段。
- [X] 5.2 运行 Backend Ruff 和格式检查。
- [ ] 5.3 运行 Frontend Unit、Typecheck 和 Lint。
- [ ] 5.4 运行 SQL Execute Button E2E。
- [X] 5.5 更新 `docs/guide/used-api.md`。
- [X] 5.6 更新 `backend/AGENTS.md`；本次未修改前端，因此不更新 `frontend/AGENTS.md`。
- [X] 5.7 更新 DataAgent README 和用户配置说明。
- [X] 5.8 编写 `docs/reviews/` Review，记录验证结果和遗留风险。
- [ ] 5.9 测试通过后合并回 `dev` 并推送 `origin/dev`。

## 9. 后续任务

以下任务在 SQL Executor 完成后单独立项：

1. SQL SubAgent 独立 SQL 模型训练和评测。
2. DataAgent Lead 独立业务模型训练和 Schema-RAG 评测。
3. Agent 间完整 Contract 和 Artifact 版本管理。
4. Analysis SubAgent。
5. Chart SubAgent。
6. 多模型灰度、回滚和独立评测体系。

相关历史规划保留在本目录其他阶段文档中，但不作为当前 SQL Executor 的前置开发任务。
