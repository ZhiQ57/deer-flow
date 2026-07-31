# DataAgent Text2SQL 运行说明

DataAgent 当前包含两条用途不同的路径：正式生产闭环与历史实验路径。两者必须区分，不能把历史实验配置当作生产入口。

1. `docs/agents/data-agent/config.yaml` 与 `SOUL.md` 是 DeerFlow 原生 custom-agent 模板，可复制到 `.deer-flow/users/{user_id}/agents/data-agent/`；配置中的 `service_ability.type=data_query` 会让正式 `lead_agent` 动态装配 DataAgent middleware。
2. `backend/packages/harness/deerflow-dev/` 是废弃代码，不作为生产、测试或迁移入口；Agent 运行仍复用 `lead_agent + agent_name`，不新增 DataAgent 图或 checkpoint。前端手动执行 SQL 使用独立线程级 Gateway REST 接口，但不会创建 Agent run。

正式能力需要在根 `config.yaml -> subagents.custom_agents` 显式注册 `sql-subagent`，并只允许
`data_validate_sql`、`data_execute_sql` 两个工具；SQL 子代理不能加载 `table-rag-agent` Skill。
这两个工具由 Gateway 按 Run 和 approved Snapshot 动态注入，工具实现调用 Gateway 内部路由；
SQL SubAgent 和可独立发布的 `deerflow-harness` 都不直接连接业务数据库。
真实本地拓扑由 PostgreSQL 保存 TableRAG 索引元数据、MySQL 保存业务表，执行库通过
`DATA_AGENT_MYSQL_DSN` 环境变量提供。SQL Executor 不在 custom-agent 配置中维护静态表/字段列表；表和字段由 Schema-RAG 检索证据约束，未来由 SQL Executor 权限层按当前用户实时判断。
同库部署仍可使用 PostgreSQL 执行源，但必须选择 `same_physical_target` 并让检索/执行 fingerprint 一致。

第 2 条实验性路径曾包含本文早期版本中的**检索后标签门禁**、可选 QueryContext Tool、
只读 SQL 校验/执行、ChartSpec 和确定性收敛预算；这些内容仅用于历史迁移对照。当前正式
custom-agent 路径已经通过 `service_ability` 在原生 `lead-agent` 上提供本方案的检索、标签、确认、
SQL SubAgent 和结果卡闭环，不会切换到实验图，也不会新增独立运行时。

## 1. 执行步骤

测试 create_deerflow_agent(...) 作为入口的智能体时, 按照下面的步骤执行.

在每次运行脚本的 PowerShell 会话中执行：
```powershell
Set-Location "D:\A-PythonWork\AOpenGithub\deer-flow"

$env:DEER_FLOW_CONFIG_PATH = "D:\A-PythonWork\AOpenGithub\deer-flow\config.yaml"
$env:DEER_FLOW_EXTENSIONS_CONFIG_PATH = "D:\A-PythonWork\AOpenGithub\deer-flow\extensions_config.json"
$env:TABLERAG_CONFIG = "D:\A-PythonWork\AOpenGithub\deer-flow\tablerag.yaml"

$env:TABLERAG_MCP_INDEX_DSN = "postgresql://postgres:postgres@127.0.0.1:55433/text2sql"

$env:DATA_AGENT_MYSQL_DSN = "mysql+pymysql://readonly:<password>@127.0.0.1:3308/text2sql"
```

密码中的 `@` 等保留字符必须先做 URL 编码，例如 `@` 编码为 `%40`。

当前已有配置, 检查确认：
- config.yaml 中存在 Qwen3.6-plus
- 本地 DataAgent 配置存在：`backend\.deer-flow\agents\data-agent\config.yaml`；用户隔离部署使用
  `backend\.deer-flow\users\{user_id}\agents\data-agent\config.yaml`。
- extensions_config.json 中 tablerag.enabled=true
- pymysql、sqlglot、psycopg 均已安装
- 数据库环境变量。

如果上述配置未成功，请看下文将配置注册.


1. 执行单个问题
```powershell
& "D:\A-PythonWork\AOpenGithub\deer-flow\backend\.venv\Scripts\python.exe" `
  "D:\A-PythonWork\AOpenGithub\deer-flow\backend\tests\service_agent\test-data-agent\run_data_agent_stream.py" `
  "查询原因不明病例数，并生成 KPI 图表"
```

执行 CSV 前两条
```powershell
& "D:\A-PythonWork\AOpenGithub\deer-flow\backend\.venv\Scripts\python.exe" `
  "D:\A-PythonWork\AOpenGithub\deer-flow\backend\tests\service_agent\test-data-agent\run_data_agent_stream.py" `
  --dataset "D:\A-PythonWork\AOpenGithub\deer-flow\backend\tests\service_agent\test-data-agent\指标设计sql.csv" `
  --sample-count 2
```

执行单元测试
```powershell
Set-Location "D:\A-PythonWork\AOpenGithub\deer-flow\backend"

uv run pytest "D:\A-PythonWork\AOpenGithub\deer-flow\backend\tests\service_agent\test-data-agent" -q
```

这些环境变量只对当前 PowerShell 会话有效；

## 2. 配置 PostgreSQL TableRAG 索引与 MySQL 业务执行源（正式路径）

2026 年 7 月 16 日真实环境核验确认：当前 `tablerag.yaml` 的 PostgreSQL 库保存 TableRAG 索引，
检索返回的业务表实际位于 MySQL `text2sql`。因此正式路径使用
`source_binding_mode: logical_data_source`，由服务端把 PostgreSQL 检索目标与 MySQL 执行目标共同写入
`binding_fingerprint`。这不是让模型任意跨库；两个 DSN、逻辑 `data_source_id` 和允许访问的 Schema 都必须由服务端配置。

`extensions_config.example.json` 已包含默认关闭的 `tablerag` stdio MCP。复制为本地配置后，将 `mcpServers.tablerag.enabled` 改为 `true`：

```powershell
Copy-Item -LiteralPath "extensions_config.example.json" -Destination "extensions_config.json" -Force
```

该 Server 必须保留 `"tool_name_prefix": false`，这样 DataAgent 看到的唯一工具名严格为
`sqlrag_retrieve`，不会变成 `tablerag_sqlrag_retrieve`。

在启动 DataAgent 的同一个 PowerShell 会话中注入配置。密码含 `@`、`:` 等字符时必须先做 URL 编码：

```powershell
$env:TABLERAG_CONFIG = "D:\A-PythonWork\AOpenGithub\deer-flow\tablerag.yaml"
$env:TABLERAG_MCP_CONFIG = $env:TABLERAG_CONFIG
$env:TABLERAG_MCP_INDEX_DSN = "postgresql://postgres:postgres@127.0.0.1:55433/text2sql"

$env:DATA_AGENT_MYSQL_DSN = "mysql+pymysql://root:root%40123456@127.0.0.1:3308/text2sql"
```

历史实验入口也支持分别设置以下 MySQL 环境变量；正式 `service_ability` 路径只读取
`sql_execution.dsn_env` 指向的完整 DSN，不会读取这些拆分字段：

```powershell
$env:DATA_AGENT_MYSQL_HOST="<host>"
$env:DATA_AGENT_MYSQL_PORT="<port>"
$env:DATA_AGENT_MYSQL_USER="<user>"
$env:DATA_AGENT_MYSQL_PASSWORD="<password>"
$env:DATA_AGENT_MYSQL_DATABASE="<business_database>"
```

不要把真实 DSN、密码或令牌写入受 Git 管理的配置、测试和文档。`tablerag.yaml`、`extensions_config.json` 和 `config.yaml` 是本地文件；部署环境应优先使用 Secret/环境变量注入。启动前必须确认 `TABLERAG_MCP_CONFIG/TABLERAG_CONFIG` 指向的文件真实存在；否则 MCP 不能按该配置启动。

建议执行库账号同时在 MySQL 权限层配置为只读。应用层只读事务和 SQL AST 校验是纵深防御，不能替代数据库最小权限账号。

正式 custom-agent 配置的关键片段：

```yaml
service_ability:
  type: data_query
  version: 1
  # v1 固定为 true；停用 DataAgent 查询闭环时移除整个 service_ability。
  enable_sql_rag: true
  table_rag_config: tablerag.yaml
  data_source_id: text2sql-mysql-local
  source_binding_mode: logical_data_source
  confirmation_mode: on_ambiguity
  min_auto_confidence: 0.85
  sql_subagent_name: sql-subagent
  sql_execution:
    enabled: true
    database_type: mysql
    dsn_env: DATA_AGENT_MYSQL_DSN
    readonly: true
    max_execution_attempts: 3
    allowed_schemas: [text2sql]
```

正式 SQL 校验同时支持 PostgreSQL/MySQL AST。`sql_only` 快照只向 SQL SubAgent 提供
`data_validate_sql`；只有 `execute` 快照才提供 `data_execute_sql`。父 Agent 不信任 SQL SubAgent
最终自由文本，而是从真实 SQL 工具 ToolMessage 重建并再次校验结果。

正式执行逻辑集中在 Gateway 的
`app.gateway.modules.sql_execution.service.SqlExecutionService`，其垂直模块同时包含 contracts、
binding、validator、drivers、result budget、error classifier、Run capability、Router 和工具提供器。
`deerflow-harness` 只保留 DataAgent 编排、通用子代理工具提供器扩展点和权威 artifact 重建，不包含
数据库驱动、sqlglot、执行 DSN 或请求级 Secret。SQL SubAgent 每次调用 `data_validate_sql` /
`data_execute_sql` 都会经过 Gateway 内部认证路由，Gateway 重新读取当前线程、Agent、approved
Snapshot、retrieval 和 binding。每次执行都会消费当前校验代次；执行失败后，SQL SubAgent 必须根据
`error_category`、可选的安全 `error_message`、`retryable` 和 `recommended_action` 修复或简化 SQL，
再次调用 `data_validate_sql` 后才能重新执行。单个 SQL 子任务最多执行
`max_execution_attempts` 次，默认 3 次，成功后不能继续执行。

前端手动执行 SQL 使用：

```text
POST /api/threads/{thread_id}/sql/execute
```

该接口只接受 `source=manual_ui`，要求当前认证用户严格拥有该线程，并按当前用户加载请求中的
`agent_name`。只有 `service_ability.type=data_query` 且 `sql_execution.enabled=true` 时允许执行。
前端请求体只包含 `agent_name`、`sql` 和 `source=manual_ui`，不会携带 internal auth、`run_id`、
`snapshot_id`、SubAgent 来源、DSN、Secret、Schema 或 retrieval。
手动执行不伪造 Query Snapshot 或 TableRAG registry，而是由服务端解析当前数据源绑定，并继续执行
SQL AST、只读、数据库方言、Schema、超时和结果预算校验。SQL Execution 配置不维护静态表/字段列表；
未来按当前登录用户查询权限的检查应接入 SQL Executor 授权层。返回结果不会经过 LLM
总结；Schema 或数据源绑定校验失败会返回直接原因，数据库执行失败会返回驱动主错误的最小脱敏文本。
DSN、密码、数据库驱动对象和异常堆栈不会返回前端。

Web UI 仅在上述能力开启的 custom-agent 对话中，给已完成的 `sql` fenced code block 显示“执行”按钮。
点击后结果显示在 ChatBox 右侧 SQL Result Panel；流式 SQL、非 SQL 代码块和普通 Agent 不显示该按钮。
用户可以选中面板中的 SQL、错误或结果文本，点击“添加到对话”，将选中文本作为引用上下文附加到下一次请求。

`data-agent.allowable_subagents` 必须显式包含 `sql-subagent`。服务端会把该判定写入本次运行上下文并在
`SqlStageMiddleware`、Harness 通用工具提供器扩展点和 Gateway Run 能力注册表中逐层校验；客户端手动设置
`subagent_enabled=true`、模型自行填写 `subagent_type=sql-subagent` 或普通会话调用 internal route 都不能
替代此授权。同一模型响应中的多个 TableRAG、标签或 SQL SubAgent 调用只保留第一个，补充检索必须串行执行，
避免产生冲突快照或重复 SQL 执行。

## 4. 正式流程与安全边界

正式 `service_ability.type=data_query` 路径复用 lead-agent 的模型、prompt、Skill、MCP 和多数 middleware，并额外增加：

- `TableRagStageMiddleware`：登记无前缀 `sqlrag_retrieve` 的检索结果，生成 retrieval digest、registry、data_source_id 与 binding_fingerprint。新可见用户问题可以由本 middleware 直接创建新的 `retrieving` 快照，不再依赖每轮强制 reset。
- `publish_query_labels`：DataAgent 专属标签声明工具，只接收 lead-agent 已经确认的 `intent`、`labels`、可选 `summary` 和显式 `ambiguities`，不再接收模型自报 `confidence`，也不调用额外模型。没有歧义时必须传 `ambiguities: []`，不能省略或只在最终回答中描述待确认项。
- `QueryLabelsMiddleware`：稳定实现位于 `deerflow.agents.middlewares.query_labels_middleware`；正式 DataAgent 使用 `require_retrieval=True`，因此任何标签都必须绑定有效 TableRAG 检索上下文。用户在旧轮取消、失败或完成后再次表达“重新执行”时，模型可以基于历史检索快照重新调用 `publish_query_labels`，middleware 会用最新可见用户消息生成新的 `labels_published` 快照并记录 `resumed_from`，但不会直接创建人工确认请求。
- `ask_intent_approval`：DataAgent 专属查询意图审批工具。模型在标签结果显示 `approval_policy.required=true` 或自行判断需要用户确认时调用它；后端生成 human-input v1 审批卡并暂停运行。
- `QueryIntentApprovalMiddleware`：拦截 `ask_intent_approval`，把人类审批结果以同一工具调用的 ToolMessage 结果写回对话历史；`approved`、`cancelled` 都保留给模型阅读。若当前快照已经取消，直接再次调用审批工具只会得到“先重新发布标签”的提示，避免复活旧取消态。
- `SqlStageMiddleware`：父 DataAgent 只通过 `task` 委派配置中的 SQL SubAgent；只有当前持久化快照已 `approved`，或 `approval_policy.required=false` 时才允许进入 SQL 阶段。模型可读取历史审批工具结果判断追问是否仍相关，但后端不会从 `cancelled` 快照直接生成 SQL 授权；取消后重新执行应先重新发布标签并按需审批。
- `data_validate_sql` / `data_execute_sql`：SQL SubAgent 专属工具，支持 PostgreSQL/MySQL 只读 AST 校验、数据源绑定、预算、超时和安全错误分类。`sql_only` 只提供校验工具；`execute` 才提供执行工具。

lead-agent 可以直接从用户问题中组织 TableRAG 检索关键词。标签展示由
`publish_query_labels` 完成，但必须绑定有效检索上下文：`source=user` 和
`source=derived` 也不能在没有检索锚点时提前发布，`source=database` 还必须引用当前可执行
检索快照中的 TableRAG Evidence。后续再次调用会替换当前完整标签快照，并生成可逐项审核的
`ambiguity_items`。标签工具不会额外请求模型，也不能把历史对话、memory 或旧 SQL
当成当前数据库 Schema 的证明。实时消息和历史 values 都必须携带 artifact，否则前端会退化为普通工具轨迹。

当前代码目录：

```text
deerflow/
├── agents/
│   ├── middlewares/query_labels_middleware.py
│   └── service_agent/
│       ├── table_rag_middleware.py
│       ├── approval_middleware.py
│       ├── sql_stage_middleware.py
│       └── turn_context.py
└── tools/builtins/
    ├── query_labels_tool.py
    └── ask_intent_approval_tool.py

deerflow-dev/
├── agents/
│   ├── data_agent/                 # 图工厂、prompt、Agent 常量
│   ├── middlewares/                # 轮次重置、标签发布与流程编排 middleware
│   └── thread_state.py             # DataAgentState 与 reducer
├── tools/
│   ├── builtins/                   # 标签/实体、SQL 和 ChartSpec 工具注册
│   ├── sql_validation.py           # SQL AST 校验
│   ├── database.py                 # MySQL 只读执行
│   └── chart_spec.py               # ChartSpec 构造
└── subagents/
    └── builtins/                   # 后续内置垂直子代理配置边界
```

图入口使用 `from agents import build_data_agent, make_data_agent`。旧的
`agents.data_agent.middleware`、`agents.data_agent.state`、
`agents.data_agent.tools`、`tools.query_context` 和
`tools.builtins.query_context_tool` 等导入路径已经删除，不提供兼容层。

默认工具面只保留：

- `read_file` 等必要 DeerFlow 框架工具；
- 唯一且无前缀的只读 MCP 工具 `sqlrag_retrieve`；
- DataAgent 标签工具、意图审批工具和 SQL SubAgent 动态工具。

不会暴露 Bash、写文件、其他 MCP，也不会兼容旧的多工具 TableRAG 名称。通用子代理默认关闭；如显式启用，只接受配置了明确工具白名单、且工具名严格为 `sqlrag_retrieve` 的自定义子代理。

单轮默认调用预算：

| 阶段 | 默认上限 |
|---|---:|
| 可选 QueryContext 抽取 | 1 |
| TableRAG 检索 | 6 |
| SQL 校验 | 4 |
| SQL 执行 | 2 |
| ChartSpec | 2 |
| 所有工具结果合计 | 10 |

总预算通过 `config.configurable.data_agent_max_total_tool_calls` 配置，默认 `10`，
允许范围 `1..50`。达到总预算、分阶段硬预算、成功 SQL 结果或完成 ChartSpec 后，
下一次模型调用会收到 `tools=[]`，只能基于当前轮次已有 Evidence 和执行结果生成最终回答。
状态通过 `data_force_final_answer` 暴露停止原因，同时保留最后一次成功执行快照，供失败后的
最终解释使用。不要仅提高 `recursion_limit` 来掩盖工具循环。

## 5. 控制台运行

直接提问：

```powershell
backend\.venv\Scripts\python.exe backend\tests\service_agent\test-data-agent\run_data_agent_stream.py "查询 2024 年华东 GMV 最高的前 10 个商品"
```

默认日志写入脚本同目录的 `logs/`，实际文件名为：

```text
log_YYYYMMDD_HHMMSS_mmm.txt
```

可以用 `--log-path` 指定日志目录，或传入 `log.txt` 作为文件名模板：

```powershell
backend\.venv\Scripts\python.exe backend\tests\service_agent\test-data-agent\run_data_agent_stream.py `
  "查询 2024 年华东 GMV 最高的前 10 个商品" `
  --log-path "D:\data-agent-logs\log.txt"
```

日志采用 `时间 | 级别 | logger | 消息` 格式，同时记录：

- 命令行参数、工作目录、Python/平台信息；
- `config.yaml`、`extensions_config.json`、TableRAG 配置和 DataAgent 模板路径；
- TableRAG/PostgreSQL、MySQL 和 SQL 预算相关环境变量；
- 查询标签、可选 QueryContext、工具调用、工具结果、阶段变化、SQL 校验/执行和 ChartSpec；
- 模型流式回答文本以及 DeerFlow/依赖库通过 Python logging 输出的日志。

DSN 中的密码和 `PASSWORD/TOKEN/SECRET/API_KEY` 环境变量会自动脱敏，不会明文写入日志。

要求图表：

```powershell
backend\.venv\Scripts\python.exe backend\tests\service_agent\test-data-agent\run_data_agent_stream.py "查询原因不明病例数，并生成 KPI 图表"
```

追加黑话映射：

```powershell
backend\.venv\Scripts\python.exe backend\tests\service_agent\test-data-agent\run_data_agent_stream.py "统计黑金用户 GMV" --alias "黑金=高价值会员"
```

从本地 CSV 只抽取一到两条指标问题：

```powershell
backend\.venv\Scripts\python.exe backend\tests\service_agent\test-data-agent\run_data_agent_stream.py `
  --dataset "指标设计sql.csv" `
  --sample-count 2 `
  --dataset-question-column "指标名称" `
  --log-path "D:\data-agent-logs"
```

脚本启动时默认执行 MySQL `SELECT 1` 预检，并流式打印 `values`、`messages`、`custom`。仅在已单独确认数据库可用时，才使用 `--skip-db-preflight`。

## 6. 本地可视化调试页

在已经配置好同一 PowerShell 会话环境变量后，从仓库根目录执行：

```powershell
backend\.venv\Scripts\python.exe backend\tests\service_agent\test-data-agent\run_data_agent_web.py
```

默认会打开：

```text
http://127.0.0.1:8765
```

指定端口、默认模型和日志目录：

```powershell
backend\.venv\Scripts\python.exe backend\tests\service_agent\test-data-agent\run_data_agent_web.py `
  --port 8765 `
  --model "Qwen3.6-plus" `
  --log-path "D:\data-agent-logs"
```

页面提供：

- 同一 `thread_id` 下的简单连续对话；
- 首次有效 TableRAG 检索后由 lead-agent 发布的用户意图标签及其 user/database/derived 来源；
- 可选 QueryContext Tool 产生的归一化问题和实体结果；
- DataAgent 阶段进度；
- TableRAG 检索摘要；
- 生成 SQL 与 SQL 校验数据；
- SQL 结果表格及最后一次成功结果；
- KPI、bar、line 等基础 ChartSpec 预览；
- 工具调用、工具结果和结构化事件时间线；
- 工具总预算或阶段预算触发后的收敛保护状态；
- 当前运行的时间戳日志路径。

页面使用进程内 `InMemorySaver` 保存会话，关闭服务后状态清空。为避免实验图和 Python root logging 在多个请求间交叉污染，页面同一时间只执行一个任务。

默认仅监听 `127.0.0.1`。该页面没有正式生产认证，不要暴露到公网；如确需在受控网络监听其他地址，必须显式传入 `--allow-remote`。

常用参数：

```text
--no-open-browser
--skip-db-preflight
--no-thinking
--recursion-limit 150
--config <config.yaml>
--extensions-config <extensions_config.json>
```

## 7. 关键状态

流式 `values` 中可观察：

- `data_agent_stage`
- `data_query_labels`
- `data_query_context`
- `data_retrieval_context`
- `data_generated_sql`
- `data_sql_validation`
- `data_sql_execution`
- `data_last_successful_sql_execution`
- `data_chart_spec`
- `data_force_final_answer`

成功主路径为：

```text
retrieval_completed
-> labels_published
-> sql_validated
-> sql_executed
-> chart_ready（用户要求图表时）
-> FinalAnswer
```

普通非数据请求可以不进入上述状态链，直接输出 FinalAnswer。

失败阶段会标记为 `sql_validation_failed`、`sql_execution_failed` 或 `chart_failed`。

## 8. 废弃实验路径说明

- 上述实验运行层位于已废弃的 `deerflow-dev`，不是稳定 `deerflow.*` 公共 API，不进入当前生产链路或测试范围。
- 正式路径已经交付可独立配置模型的 SQL SubAgent；TableRAG 当前仍以 MCP 检索工具接入 DataAgent Lead，不是独立子代理。
- Analysis SubAgent、Chart SubAgent 和前端图表渲染协议属于后续任务，不是当前 SQL Executor 前置依赖。
- CSV 中的参考 SQL 目前只作为人工对照数据，未自动参与生成 SQL 的等价性评测。
- 当前 TableRAG 索引以 Schema/字段值召回为主；若要稳定复现业务标准 SQL，应把指标定义、统计口径、Join 规则和参考 SQL 加工为 Evidence 并写入索引，不能只依赖字段注释猜测。
