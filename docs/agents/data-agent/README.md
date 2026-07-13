# DataAgent Text2SQL 运行说明

DataAgent 当前包含两条用途不同的路径：

1. `docs/agents/data-agent/config.yaml` 与 `SOUL.md` 是 DeerFlow 原生 custom-agent 模板，可复制到 `.deer-flow/users/{user_id}/agents/data-agent/`。
2. `backend/packages/harness/deerflow-dev/` 是实验性、阶段门禁化的 DataAgent 运行层，按照 DeerFlow SDK 的 `agents`、`agents/middlewares`、`tools/builtins`、`subagents` 边界组织，并通过 `create_deerflow_agent(...)` 重新创建图；当前只由测试执行脚本直接启动，不新增 Gateway 路由。

只有第 2 条实验性路径包含本文所述的 QueryContext、只读 SQL 校验/执行、ChartSpec 和调用预算门禁。原生 UI custom-agent 路径仍使用 lead-agent，不会自动切换到该实验图。

## 1. 执行步骤

测试 create_deerflow_agent(...) 作为入口的智能体时, 按照下面的步骤执行.

在每次运行脚本的 PowerShell 会话中执行：
```powershell
Set-Location "D:\A-PythonWork\AOpenGithub\deer-flow"

$env:DEER_FLOW_CONFIG_PATH = "D:\A-PythonWork\AOpenGithub\deer-flow\config.yaml"
$env:DEER_FLOW_EXTENSIONS_CONFIG_PATH = "D:\A-PythonWork\AOpenGithub\deer-flow\extensions_config.json"
$env:TABLERAG_CONFIG = "D:\A-PythonWork\AOpenGithub\deer-flow\tabelrag.yaml"

$env:TABLERAG_MCP_INDEX_DSN = "postgresql://postgres:postgres@127.0.0.1:55433/text2sql"
$env:TABLERAG_MCP_SOURCE_DSN = "postgresql://postgres:postgres@127.0.0.1:55433/text2sql"

$env:DATA_AGENT_MYSQL_DSN = "mysql+pymysql://root:root%40123456@127.0.0.1:3308/text2sql"
```

密码中的 `@` 等保留字符必须先做 URL 编码，例如 `@` 编码为 `%40`。

当前已有配置, 检查确认：
- config.yaml 中存在 Qwen3.6-plus
- 本地 DataAgent 配置存在：
- D:\A-PythonWork\AOpenGithub\deer-flow\.deer-flow\users\default\agents\data-agent\config.yaml
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

## 2. 配置 TableRAG MCP 与 MySQL

`extensions_config.example.json` 已包含默认关闭的 `tablerag` stdio MCP。复制为本地配置后，将 `mcpServers.tablerag.enabled` 改为 `true`：

```powershell
Copy-Item -LiteralPath "extensions_config.example.json" -Destination "extensions_config.json" -Force
```

在启动 DataAgent 的同一个 PowerShell 会话中注入配置。密码含 `@`、`:` 等字符时必须先做 URL 编码：

```powershell
$env:TABLERAG_CONFIG="D:\path\to\tabelrag.yaml"
$env:TABLERAG_MCP_INDEX_DSN="postgresql://<user>:<password>@<host>:<port>/<index_database>"
$env:TABLERAG_MCP_SOURCE_DSN="postgresql://<user>:<password>@<host>:<port>/<source_database>"
$env:DATA_AGENT_MYSQL_DSN="mysql+pymysql://<user>:<url-encoded-password>@<host>:<port>/<business_database>"
```

也可以不用 MySQL DSN，改为分别设置：

```powershell
$env:DATA_AGENT_MYSQL_HOST="<host>"
$env:DATA_AGENT_MYSQL_PORT="<port>"
$env:DATA_AGENT_MYSQL_USER="<user>"
$env:DATA_AGENT_MYSQL_PASSWORD="<password>"
$env:DATA_AGENT_MYSQL_DATABASE="<business_database>"
```

不要把真实 DSN、密码或令牌写入受 Git 管理的配置、测试和文档。`tabelrag.yaml`、`extensions_config.json` 和 `config.yaml` 是本地文件；部署环境应优先使用 Secret/环境变量注入。

建议执行库账号同时在 MySQL 权限层配置为只读。应用层只读事务和 SQL AST 校验是纵深防御，不能替代数据库最小权限账号。

## 4. 实验性流程与安全边界

实验性运行层复用 lead-agent 的模型、prompt、Skill、MCP 和多数 middleware，并额外增加：

- `QueryContextMiddleware`：黑话归一化、意图识别、实体抽取、每轮状态重置和 `data_query_context` 流事件。
- `DataAgentOrchestrationMiddleware`：强制 TableRAG -> SQL 校验 -> SQL 执行 -> 可选 ChartSpec 的阶段顺序。
- `data_validate_sql`：只允许单条 MySQL `SELECT/WITH`，拒绝 DDL/DML、多语句、锁、文件写出、危险函数、优化器 Hint、占位符、跨业务库和系统库访问，并自动收紧 `LIMIT`。
- `data_execute_sql`：只执行最近校验返回的同一条 `executable_sql`；使用只读事务、连接/读取/查询超时、行数、单元格和结果总字符预算。
- `data_build_chart_spec`：只消费成功 SQL 结果，并校验图表字段和数值轴。

当前代码目录：

```text
deerflow-dev/
├── agents/
│   ├── data_agent/                 # 图工厂、prompt、Agent 常量
│   ├── middlewares/                # QueryContext 与流程编排 middleware
│   └── thread_state.py             # DataAgentState 与 reducer
├── tools/
│   ├── builtins/                   # 三个模型可调用工具
│   ├── sql_validation.py           # SQL AST 校验
│   ├── database.py                 # MySQL 只读执行
│   └── chart_spec.py               # ChartSpec 构造
└── subagents/
    └── builtins/                   # 后续内置垂直子代理配置边界
```

图入口使用 `from agents import build_data_agent, make_data_agent`。旧的
`agents.data_agent.middleware`、`agents.data_agent.state`、
`agents.data_agent.tools` 等导入路径已经删除，不提供兼容层。

默认工具面只保留：

- `read_file` 等必要 DeerFlow 框架工具；
- 只读 `tablerag_*` MCP 工具；
- DataAgent 专用 SQL/ChartSpec 工具。

不会暴露 Bash、写文件、其他 MCP、`tablerag_initialize_indexes` 或 `tablerag_sync_field_values`。通用子代理默认关闭；如显式启用，只接受配置了明确工具白名单、且工具全部属于只读 TableRAG 的自定义子代理。

单轮默认调用预算：

| 阶段 | 默认上限 |
|---|---:|
| TableRAG 检索 | 6 |
| SQL 校验 | 4 |
| SQL 执行 | 2 |
| ChartSpec | 2 |

达到 SQL 执行上限后，不再允许继续检索或校验新 SQL，避免覆盖已有执行结果。状态同时保留最后一次成功执行快照，供失败后的最终解释使用。

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
- QueryContext、工具调用、工具结果、阶段变化、SQL 校验/执行和 ChartSpec；
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
- QueryContext、归一化问题和实体标签；
- DataAgent 阶段进度；
- TableRAG 检索摘要；
- 生成 SQL 与 SQL 校验数据；
- SQL 结果表格及最后一次成功结果；
- KPI、bar、line 等基础 ChartSpec 预览；
- 工具调用、工具结果和结构化事件时间线；
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
- `data_query_context`
- `data_retrieval_context`
- `data_generated_sql`
- `data_sql_validation`
- `data_sql_execution`
- `data_last_successful_sql_execution`
- `data_chart_spec`

成功主路径为：

```text
query_context
-> retrieval_completed
-> sql_validated
-> sql_executed
-> chart_ready（用户要求图表时）
-> FinalAnswer
```

失败阶段会标记为 `sql_validation_failed`、`sql_execution_failed` 或 `chart_failed`。

## 8. 当前限制

- 该运行层位于 `deerflow-dev`，不是稳定 `deerflow.*` 公共 API，也没有注册独立 Gateway 图路由。
- 当前主要是主代理内的工具编排，尚未交付可独立训练的 TableRAG 子代理和 NL2SQL 子代理。
- ChartSpec 已进入状态和控制台流，但前端图表渲染协议尚未接入。
- CSV 中的参考 SQL 目前只作为人工对照数据，未自动参与生成 SQL 的等价性评测。
- 当前 TableRAG 索引以 Schema/字段值召回为主；若要稳定复现业务标准 SQL，应把指标定义、统计口径、Join 规则和参考 SQL 加工为 Evidence 并写入索引，不能只依赖字段注释猜测。
