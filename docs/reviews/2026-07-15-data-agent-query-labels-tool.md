# DataAgent 查询标签工具与中间件 Review

审查日期：2026-07-15

## 1、结论

本次实现将“实体抽取结果展示”与“数据流程编排”解耦：

- lead-agent 直接分析用户问题并组织 TableRAG 的 query/keywords；
- `publish_query_labels` 只声明当前完整标签快照，不请求额外模型；
- `QueryLabelsMiddleware` 拦截执行、写入状态并发送用户侧事件；
- 标签和实体抽取都不再是 TableRAG、SQL 校验或 SQL 执行的前置门禁；
- 原有 `entity_extract_tool` 文件、导出、注册和可选调用能力完整保留。

## 2、实现边界

- 稳定工具位于 `deerflow.tools.builtins.query_labels_tool`，模型侧名称为
  `publish_query_labels`。
- 工具参数顶层为 `intent`、`labels`、可选 `summary`；标签项包含
  `label`、`value`、`source`，可选 `normalized`、`evidence`。
- middleware 不调用占位函数，成功后返回没有 `goto=END` 的
  `Command(update=...)`，因此标签展示后 DataAgent 会继续检索、SQL 执行或回答。
- ToolMessage artifact 顶层直接保存 `intent`、`labels` 和可选 `summary`，
  同一 payload 写入 `data_query_labels`。
- 每次调用提交完整快照，`replace_value` reducer 使后一次标签替换前一次，支持
  TableRAG 或 SQL 获得真实值后的多次修正。
- `source=database` 必须已有成功 TableRAG 检索状态并填写 Evidence 摘要；
  未经确认的猜测不能伪装为数据库真实标签。
- custom stream 使用 `data_query_labels` 事件；控制台和完整 Web 调试脚本均支持
  custom/values 去重展示。

## 3、流程 Review

- DataAgent prompt 明确要求模型直接组织检索关键词，不再为了进入数据流程额外调用
  实体抽取模型。
- `DataAgentOrchestrationMiddleware` 保留 TableRAG -> SQL 校验 -> SQL 执行 ->
  ChartSpec 的原安全门禁和预算。
- 图表意图优先读取 `data_query_labels.intent`，同时兼容可选实体工具返回的旧
  `data_query_context.intent`。
- 可选实体工具成功时只保存 `data_query_context`，不会回退阶段或清空已经产生的
  检索、SQL 和图表状态。
- `run_data_agent_web.py` 仍使用完整 `build_data_agent`、TableRAG、SQL 校验/
  执行、ChartSpec、MySQL 预检和 NDJSON 事件流程，仅新增标签事件与展示面板。

## 4、测试结果

- DataAgent 测试目录：`76 passed`。
- 稳定实体/标签工具专项测试：`5 passed`。
- Harness/App 边界测试：`1 passed`。
- Ruff check、Ruff format check、Python 编译、控制台/Web 脚本 `--help` 和
  `git diff --check`：通过。
- 覆盖标签工具 Schema、同步/异步拦截、handler 不执行、artifact、状态更新、
  custom stream、数据库 Evidence 约束、无关工具透传、多次快照替换、工具注册、
  轮次重置、非前置门禁、控制台输出和 Web 去重展示。

## 5、风险与后续

- Evidence 当前校验“已有成功 TableRAG 检索 + 非空摘要”，不对摘要文本做字符串
  包含匹配，避免不同 MCP 返回格式造成脆弱耦合。
- 标签是展示状态，不参与 SQL 安全决策；真实执行仍以 TableRAG 状态、SQL AST 校验、
  校验摘要绑定和数据库只读权限为准。
