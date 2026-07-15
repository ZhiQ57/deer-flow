# DataAgent 查询标签工具与中间件开发计划

## 1、需求与边界

- [X] 1.1 保留现有 `entity_extract_tool` 文件、导出和可调用能力。
- [X] 1.2 标签由 lead-agent 基于用户问题、TableRAG Evidence 和 SQL 结果自行判断，不再把实体抽取作为数据流程前置门禁。
- [X] 1.3 标签工具只负责声明结构化标签；实际执行由标签 middleware 拦截，不额外请求模型。
- [X] 1.4 标签只用于用户侧展示和状态记录，不改变 TableRAG、SQL 校验、SQL 执行和 ChartSpec 的既有安全门禁。

## 2、测试驱动开发

- [X] 2.1 为标签工具名称、参数 Schema 和占位实现编写测试。
- [X] 2.2 为标签 middleware 的同步、异步拦截和 handler 不执行编写测试。
- [X] 2.3 为 ToolMessage artifact、状态更新、custom stream 和多次标签更新编写测试。
- [X] 2.4 为数据库来源标签的 Evidence 约束和无关工具透传编写测试。
- [X] 2.5 为 DataAgent 工具注册、middleware 链、轮次重置和非前置门禁编写回归测试。
- [X] 2.6 为 Web 调试页标签事件转换、去重和展示面板编写测试。

## 3、实现

- [X] 3.1 新增稳定 SDK 标签声明工具 `publish_query_labels`。
- [X] 3.2 新增 DataAgent 标签 middleware，返回不带 `goto=END` 的 `Command(update=...)`。
- [X] 3.3 新增 `data_query_labels` 独立状态，并在新用户轮次重置。
- [X] 3.4 将标签工具和 middleware 接入完整 DataAgent 图，继续保留实体抽取、SQL 和 ChartSpec 工具。
- [X] 3.5 调整 DataAgent prompt 与编排门禁，使 lead-agent 直接组织 TableRAG 关键词并按需发布标签。
- [X] 3.6 在完整 Web 调试脚本中展示标签 custom/values 事件，不改变原检索、SQL 执行和结果返回流程。

## 4、文档与验证

- [X] 4.1 更新 DataAgent README、后端架构说明和公共 API 指南。
- [X] 4.2 新增实现 review，记录设计边界、风险和测试结果。
- [X] 4.3 运行专项 pytest、Ruff、format、Python 编译和 `git diff --check`。
