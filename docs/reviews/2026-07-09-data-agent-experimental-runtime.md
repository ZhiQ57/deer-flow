# DataAgent 实验性运行层生产级 Review

审查日期：2026-07-10

## 1、结论

DataAgent 已从“主要依赖 Prompt 的实验图”补强为具备最小权限工具面、确定性阶段门禁、SQL AST 安全校验、只读真实执行、结果预算、状态保留和真实数据库联调的实验性生产基线。

当前适合在受控环境通过控制台脚本或本地可视化调试页试运行，但仍不建议直接标记为正式生产发布，主要原因是：

1. 实验图尚未注册 Gateway 路由，原生 custom-agent UI 使用的仍是普通 lead-agent 图。
2. CSV 参考 SQL 尚未形成自动语义评测，TableRAG Evidence 也未完整覆盖业务标准口径。
3. TableRAG/NL2SQL 仍主要在主代理内通过工具编排，尚未拆成可独立训练和评测的专用子代理。
4. ChartSpec 尚未接入前端渲染协议。

## 2、已交付架构

- 实验性运行层位于 `backend/packages/harness/deerflow-dev/`，并已按 DeerFlow SDK 边界拆分为 `agents/data_agent`、`agents/middlewares`、`agents/thread_state.py`、`tools/builtins`、工具基础层和 `subagents/builtins`。
- 新增入口：`build_data_agent()` / `make_data_agent(config)`。
- 通过 `deerflow.agents.factory.create_deerflow_agent(...)` 重新创建图，复用 lead-agent 模型、prompt、Skill、MCP、deferred tool 和多数 middleware。
- 新增稳定 SDK `deerflow.tools.builtins.entity_extract_tool.EntityExtractor` 与 `data_extract_query_context`：
  - 黑话/别名归一化；
  - 意图、时间、指标、维度、地区、排序、数量和关键词抽取；
  - 最长匹配消除“病例数/例数”等重叠指标；
  - 直接读取最后一条真实用户消息，不接收模型改写后的问题；
  - 通过 ToolMessage artifact 返回完整结构化结果，并通过 custom stream 输出实体标签。
- 新增 `DataAgentTurnResetMiddleware`：
  - 只在新真实用户消息进入时清空上一轮 QueryContext、检索、SQL、执行和图表状态；
  - 不再在每次请求时强制执行实体抽取。
- 新增 `DataAgentOrchestrationMiddleware`：
  - 将 SDK 实体抽取 artifact 适配为 `data_query_context` 状态；
  - 普通非数据请求允许直接回答；
  - 进入数据流程时强制 QueryContext -> TableRAG -> SQL 校验 -> SQL 执行 -> ChartSpec 顺序；
  - 同一用户轮次只允许完成一次 QueryContext Tool；
  - 空召回和 `tablerag_validate_index` 不能推进业务检索阶段；
  - 追问前必须先检索；
  - 图表意图在 SQL 成功后明确要求调用 ChartSpec；
  - 同一 `thread_id` 的 TableRAG MCP 调用串行化；
  - 单轮调用预算和预算耗尽后的收敛门禁。
- 新增状态：
  - `data_agent_stage`
  - `data_query_context`
  - `data_retrieval_context`
  - `data_generated_sql`
  - `data_sql_validation`
  - `data_sql_execution`
  - `data_last_successful_sql_execution`
  - `data_chart_spec`

## 3、安全与可靠性 Review

### 3.1 工具权限

- 默认工具组仅 `file:read`。
- 二次白名单只允许必要框架工具、只读 TableRAG MCP 和 DataAgent 专用工具。
- Bash、写文件、其他 MCP、TableRAG 索引初始化/字段值同步工具不会注册。
- custom-agent 配置中的 Skill 和工具组不能扩大固定白名单。
- TableRAG 缺失，或被 Skill allowed-tools 策略过滤为空时，DataAgent 默认拒绝构建。
- 通用子代理默认关闭；显式启用时，子代理必须声明只读 TableRAG 工具白名单。

### 3.2 SQL 校验

- 仅允许单条 MySQL `SELECT/WITH`。
- 拒绝 DDL、DML、事务、锁、文件写出、多语句、占位符、会话变量赋值。
- 拒绝 `SLEEP`、`BENCHMARK`、`LOAD_FILE`、锁函数等危险函数。
- 拒绝 MySQL 优化器 Hint 和可执行注释，避免绕过执行超时/资源约束。
- 禁止访问 MySQL 系统库和配置业务库之外的其他数据库。
- 自动添加或收紧固定整数 `LIMIT`。
- 执行 SQL 必须与最近成功校验返回的 `executable_sql` SHA-256 摘要一致。

### 3.3 MySQL 执行

- 使用 `SET SESSION TRANSACTION READ ONLY` 和 `START TRANSACTION READ ONLY`。
- 设置连接、读取、写入和 `MAX_EXECUTION_TIME` 超时。
- 禁用 `local_infile`，关闭自动提交，结束时回滚并关闭连接。
- 限制最大行数、单元格字符数和结果总字符数。
- 第一行超过结果总预算时也不会突破硬上限。
- 连接描述和返回模型的异常消息会脱敏明文、URL 编码密码和 DSN 凭据。
- MySQL 配置解析失败会返回受控工具错误，不再触发未赋值局部变量异常。

### 3.4 并发与状态

- 同一 thread 的 TableRAG 调用使用跨线程锁串行化，避免 Windows stdio MCP 并行初始化取消。
- 异步锁获取改为非阻塞轮询，协程取消不会留下后台线程最终持锁。
- 锁表使用弱引用，空闲 thread 键不会无限增长。
- 并行检索时，成功召回不会被另一条空结果覆盖。
- 新 SQL 仅完成校验时不会清除上一条成功执行结果。
- 替代 SQL 执行失败时保留 `data_last_successful_sql_execution`。
- 执行预算耗尽后阻止继续检索/校验，避免最终状态被新尝试覆盖。

### 3.5 ChartSpec

- 支持模型将 `y` 编码为 JSON 字符串或逗号分隔字符串。
- bar/line/pie/scatter/kpi 校验必要字段。
- 非表格图表的 Y 轴必须是数值列。
- NaN/Infinity 不会被视为可绘图数值。
- 图表意图真实联调已到达 `chart_ready`。

## 4、真实联调

联调数据库由运行命令临时注入环境变量，未写入受 Git 管理文件。

### 4.1 CSV 样例

命令模式：

```powershell
backend\.venv\Scripts\python.exe backend\tests\service_agent\test-data-agent\run_data_agent_stream.py `
  --dataset "指标设计sql.csv" `
  --sample-count 2
```

结果：

- `移植胚胎总数`：QueryContext、TableRAG、SQL 校验、MySQL 执行均成功，退出码 0。
- `原因不明(例)`：归一化为“原因不明病例数”，TableRAG 多路检索、SQL 校验、MySQL 执行均成功，退出码 0。
- 调用预算会在多次候选 SQL 尝试后强制收敛，避免无限换表。

### 4.2 ChartSpec

问题：`查询原因不明病例数，并生成 KPI 图表`

实际阶段：

```text
query_context
-> retrieval_completed
-> sql_validated
-> sql_executed
-> chart_ready
```

生成 ChartSpec：`type=kpi`，退出码 0。

### 4.3 环境噪声

本地首次运行时，`tiktoken` 下载 `cl100k_base` 资源遇到网络重置，会打印 traceback 并回退到字符估算；DataAgent 流程仍成功。该日志来自现有 DeerFlow token 估算回退，不是 TableRAG/MySQL 故障。生产镜像应预热 tokenizer 缓存或保证依赖资源可访问，以减少误报警日志。

### 4.4 控制台日志

- `--log-path` 可传目录或 `log.txt` 模板。
- 日志文件名自动使用 `log_YYYYMMDD_HHMMSS_mmm.txt`。
- 控制台继续实时输出模型增量文本；文件按标准 logging 格式记录完整文本行。
- 日志头记录命令行参数、相关环境变量、配置路径及文件存在性。
- DSN 密码和 `PASSWORD/TOKEN/SECRET/API_KEY` 变量只记录脱敏状态。
- DeerFlow 和依赖库通过 Python root logging 输出的日志也会写入同一文件。

### 4.5 本地可视化调试页

- 新增 `backend/tests/service_agent/test-data-agent/run_data_agent_web.py`。
- 默认启动 `http://127.0.0.1:8765`，并自动打开浏览器；非回环地址必须显式传入 `--allow-remote`。
- 页面通过 NDJSON 流增量接收 AI 回答和结构化事件，不需要等待整轮完成。
- 页面分区展示 QueryContext/实体标签、阶段进度、TableRAG 检索、生成 SQL、SQL 校验、结果表格、ChartSpec、工具调用和原始事件时间线。
- 同一浏览器标签页复用 `thread_id` 和进程内 `InMemorySaver`，支持连续追问；服务重启后会话状态清空。
- 每轮仍生成独立时间戳日志，并在页面显示日志文件路径。
- 页面限制问题、别名、请求体和 recursion limit；默认单运行串行，防止实验图和全局日志 handler 交叉污染。
- 页面不加载外部 CDN，动态内容只通过 `textContent` 写入 DOM，并设置 CSP、禁止 frame、禁用缓存等本地调试安全响应头。

## 5、自动验证

- `uv run pytest tests\service_agent\test-data-agent -q`：69 passed。
- `uv run ruff check ...`：All checks passed。
- `uv run ruff format --check ...`：通过。
- `python -m py_compile ...`：通过。
- `backend\.venv\Scripts\python.exe backend\tests\service_agent\test-data-agent\run_data_agent_stream.py --help`（从仓库根目录执行）：通过。
- `backend\.venv\Scripts\python.exe backend\tests\service_agent\test-data-agent\run_data_agent_web.py --help`：通过。
- 本地随机端口启动、`GET /`、`GET /api/health`、浏览器页面渲染、新会话交互和浏览器控制台错误检查：通过。
- `git diff --check`：通过。
- 受 Git 管理及待提交文件未发现用户提供的数据库密码/DSN。
- `__pycache__`、`.pyc` 和本地 CSV 未进入 Git 变更。

## 6、剩余生产风险

### P1：业务口径 Evidence 不完整

当前 CSV 中的指标定义和参考 SQL 没有自动写入 TableRAG Evidence。Schema/列注释检索可以跑通，但不能保证生成 SQL 与标准 SQL 业务等价。第一条指标存在多个相近表/字段候选，模型可能执行多个可运行但口径不同的 SQL。

建议：

1. 将指标名称、参数、统计口径、参考 SQL、适用表、Join 规则加工为 Evidence。
2. 增加生成 SQL 与标准 SQL 的结果等价性评测，而不只比较 SQL 文本。
3. 建立固定测试库快照和指标 Golden Set，纳入 CI 或离线评测。

### P1：正式运行入口未接入

当前只有控制台脚本和独立本地调试页使用实验图。若后续要求前端/Gateway 生产使用，应注册明确的 graph/assistant 入口，并补充 Gateway 流式协议、认证、用户隔离、审计和回归测试；不能误认为现有 custom-agent UI 已自动获得这些门禁。

### P2：数据库账号最小权限

本地联调使用用户提供的账号完成验证。生产部署必须改用仅具备目标业务库 `SELECT` 权限的独立账号，并配置网络白名单、TLS、连接池和数据库侧审计。

### P2：子代理与可训练优化

当前 TableRAG/NL2SQL 主要由主代理调用工具完成。后续若拆成专用子代理，应分别定义输入/输出合同、工具白名单、训练数据、评测集、超时和预算，并确保子代理结果不能绕过主图 SQL 校验与执行摘要绑定。
