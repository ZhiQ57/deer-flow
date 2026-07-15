# DataAgent QueryContext Tool 重构 Review

> 2026-07-15 更新：本 Review 中的强制 QueryContext 前置门禁已经被查询标签工具/
> middleware 方案取代。当前结论见
> `docs/reviews/2026-07-15-data-agent-query-labels-tool.md`。

审查日期：2026-07-14

## 1、结论

同意将实体抽取从强制 middleware 迁移为模型按需调用的 Tool。当前实现已完成
该重构：普通非数据请求不再自动抽取实体；模型一旦准备进入 TableRAG、数据子
代理、数据澄清或 SQL 流程，必须先调用 `data_extract_query_context`。

## 2、实现边界

- `deerflow.tools.builtins.entity_extract_tool.EntityExtractor` 提供黑话归一化、意图识别和实体抽取纯能力。
- `deerflow.tools.builtins.entity_extract_tool.entity_extract_tool` 注册模型可调用工具，模型侧名称保持 `data_extract_query_context`。
- 工具没有模型可填写的业务参数，始终读取状态中最后一条真实可见用户消息，避免模型改写原问题。
- 工具从 runtime context/config 读取 `data_agent_alias_map` 或 `data_agent_aliases`，通过 ToolMessage artifact 返回完整结果并输出 `data_query_context` custom stream event。
- `DataAgentOrchestrationMiddleware` 负责把成功 artifact 写入 `data_query_context` 并重置下游状态，稳定 SDK 工具不导入 `deerflow-dev`。
- `DataAgentTurnResetMiddleware` 只在新真实用户轮次开始时清理旧流程状态，不执行实体抽取。
- `DataAgentOrchestrationMiddleware` 允许非数据请求直接回答，并对 QueryContext、TableRAG、SQL 校验、SQL 执行和 ChartSpec 执行阶段门禁。
- 同一用户轮次只允许完成一次 QueryContext Tool，避免重复抽取时清理已经产生的下游状态。
- 原 `agents.middlewares.query_context_middleware` 已删除，不保留旧导入兼容层。

## 3、测试保护

- 覆盖黑话归一化、实体标签和长短指标去重。
- 覆盖 Tool 无模型参数、忽略隐藏用户消息、读取真实用户问题和写入状态。
- 覆盖轮次 middleware 只重置状态、不抽取实体。
- 覆盖非数据请求提示、TableRAG 前置门禁和单轮重复调用门禁。
- 覆盖 `create_deerflow_agent(...)` 构图时注册新 Tool 和新 middleware。
- 覆盖 SDK 目录边界，确保旧 middleware 文件保持删除。

## 4、验证结果

- DataAgent 完整测试：69 passed。
- Ruff check：通过。
- Ruff format check：29 files already formatted。
- Python `py_compile`：通过。
- `run_data_agent_stream.py --help`：通过。
- `run_data_agent_web.py --help`：通过。

## 5、后续建议

当前 QueryContext 仍是规则型抽取器，适合作为低成本、确定性的第一版。后续若
需要更复杂的业务实体识别，可在保持 `data_extract_query_context` 工具合同不变
的前提下替换内部抽取实现，并使用固定 QueryContext Golden Set 独立评测。
