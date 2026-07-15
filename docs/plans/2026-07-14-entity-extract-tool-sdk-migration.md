# 实体抽取工具 SDK 迁移计划

## 1、规范与边界

- [X] 1.1 对照 `deerflow/tools/builtins` 中 `@tool`、`Runtime`、`ToolMessage` 和 `Command` 的标准写法。
- [X] 1.2 确认实体抽取核心能力应迁移到 `deerflow.tools.builtins.entity_extract_tool`。
- [X] 1.3 确认稳定 SDK 工具不导入 `deerflow-dev`，DataAgent 专用状态更新由编排 middleware 适配。

## 2、标准工具实现

- [X] 2.1 在 `entity_extract_tool.py` 定义结构化实体、抽取结果和抽取器。
- [X] 2.2 使用 DeerFlow `Runtime` 读取最后一条真实用户消息和运行时别名映射。
- [X] 2.3 使用 `ToolMessage` artifact 返回完整结构化结果，模型可见内容保持紧凑 JSON。
- [X] 2.4 保留 `data_query_context` custom stream event，供现有控制台和调试页展示。
- [X] 2.5 所有新增注释和 docstring 使用中文，并补齐 Args、Returns 和异常说明。

## 3、DataAgent 接入

- [X] 3.1 DataAgent 内置工具列表改为复用 SDK 的 `entity_extract_tool`。
- [X] 3.2 编排 middleware 将成功的实体抽取 artifact 写入 `data_query_context` 并重置下游状态。
- [X] 3.3 删除 `deerflow-dev/tools/query_context.py` 和 `deerflow-dev/tools/builtins/query_context_tool.py`，不保留旧导入兼容层。
- [X] 3.4 更新 DataAgent 状态类型、工具常量和目录边界测试。

## 4、测试与文档

- [X] 4.1 在后端标准测试目录增加实体抽取工具单元测试。
- [X] 4.2 更新 DataAgent 测试，覆盖 SDK 工具、artifact 状态适配和阶段门禁。
- [X] 4.3 更新 README、`backend/AGENTS.md`、API 指南、DataAgent 文档和既有计划/review。
- [X] 4.4 新增本次迁移 review，记录边界、验证结果和后续扩展方式。

## 5、验证

- [X] 5.1 运行实体抽取工具与 DataAgent 完整 pytest。
- [X] 5.2 运行 Ruff check 和 format check。
- [X] 5.3 运行 Python 编译检查和两个执行脚本 `--help`。
- [X] 5.4 清理缓存、检查 UTF-8，并执行 `git diff --check`。
