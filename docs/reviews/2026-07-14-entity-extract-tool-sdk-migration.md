# 实体抽取工具 SDK 迁移 Review

审查日期：2026-07-14

## 1、结论

实体抽取已从 `deerflow-dev` 迁移到 DeerFlow 稳定 SDK 的标准 built-in tool
目录。实现遵循现有 `Runtime`、`ToolMessage`、artifact 和 `Command` 写法，
新增代码的注释与 docstring 全部使用中文。

## 2、标准工具结构

- 文件：`deerflow/tools/builtins/entity_extract_tool.py`。
- `ExtractedEntity` 与 `EntityExtractionResult` 定义结构化输出合同。
- `EntityExtractor` 封装别名归一化、意图识别、实体抽取、去重和缺口提示。
- `entity_extract_tool` 的模型侧名称保持 `data_extract_query_context`，模型参数为空。
- 工具使用 `deerflow.tools.types.Runtime` 读取最后一条真实用户消息、运行时别名映射和 `tool_call_id`。
- 模型可见内容使用紧凑 JSON，完整结果保存在 `ToolMessage.artifact`。
- 工具继续发送 `data_query_context` custom stream event，现有控制台和调试页无需新增协议。

## 3、模块边界

- 稳定 SDK 工具不导入 `deerflow-dev`，因此不依赖实验性 DataAgent 状态。
- `DataAgentOrchestrationMiddleware` 读取成功 artifact，写入 `data_query_context` 并重置检索、SQL、执行和图表状态。
- `DataAgentState` 直接复用 SDK 的 `EntityExtractionResult` 类型。
- DataAgent 工具注册复用 SDK `entity_extract_tool`，不再维护重复实现。
- 旧 `deerflow-dev/tools/query_context.py` 和 `deerflow-dev/tools/builtins/query_context_tool.py` 已删除，不保留兼容层。
- 工具从 `deerflow.tools.builtins` 导出，但未加入全局 `BUILTIN_TOOLS`，不会扩大普通 lead-agent 的默认工具面。

## 4、可读性与规范

- 文件按照默认规则、结构化类型、核心抽取器、运行时适配和标准工具入口分区。
- 函数、实现类和工具入口均补充中文作用说明、Args、Returns 和异常说明。
- 错误消息改为中文，并通过统一 `_build_tool_command` 构造成功或失败结果。
- 真实用户消息识别复用 `deerflow.utils.messages.is_real_user_message`，不再维护 DataAgent 私有重复逻辑。

## 5、验证结果

- SDK 实体抽取、DataAgent 完整测试和 harness 边界测试：73 passed。
- Ruff check：通过。
- Ruff format check：30 files already formatted。
- Python `py_compile`：通过。
- `run_data_agent_stream.py --help`：通过。
- `run_data_agent_web.py --help`：通过。

## 6、后续扩展

后续可以在保持 `EntityExtractionResult` 和工具名称不变的前提下替换
`EntityExtractor` 内部实现，例如引入字典服务、领域 NER 模型或可训练抽取器。
DataAgent 只依赖 ToolMessage artifact 合同，不需要再次调整状态适配边界。
