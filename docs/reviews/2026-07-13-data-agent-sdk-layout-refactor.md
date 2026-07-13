# DataAgent SDK 目录重构 Review

审查日期：2026-07-13

## 1、结论

DataAgent 已从“所有实现集中在 `agents/data_agent`”重构为与 DeerFlow SDK
一致的模块边界。图工厂、middleware、状态、built-in tools、工具基础能力和
subagent 配置目录各自独立，旧导入路径已删除，不保留兼容转发。

## 2、当前目录边界

- `agents/data_agent`：只保留 `agent.py`、`prompt.py`、`constants.py` 和包入口。
- `agents/middlewares`：独立放置 `QueryContextMiddleware` 与 `DataAgentOrchestrationMiddleware`，共用消息函数放在私有模块。
- `agents/thread_state.py`：定义 `DataAgentState`、结构化状态类型和 reducer。
- `tools/builtins`：按工具拆分 SQL 校验、SQL 执行和 ChartSpec 工具。
- `tools/sql_validation.py`：提供 MySQL 只读 SQL AST 校验。
- `tools/database.py`：提供 MySQL 只读事务执行和结果预算。
- `tools/chart_spec.py`：提供 ChartSpec 推断与字段校验。
- `subagents/builtins`：保留后续内置 TableRAG/NL2SQL 垂直子代理的 SDK 边界；当前运行时仍使用受限 DeerFlow custom subagent。

## 3、API 迁移

- 图入口改为 `from agents import build_data_agent, make_data_agent`。
- middleware 从 `agents.middlewares` 导入。
- 状态从 `agents.thread_state` 导入。
- built-in tools 由 `tools.builtins.get_data_agent_tools()` 返回。
- SQL、数据库和图表基础能力分别从 `tools.sql_validation`、`tools.database`、`tools.chart_spec` 导入。
- 已删除 `agents.data_agent.middleware/state/tools/database/sql_validation/chart`，没有兼容层。

## 4、测试保护

- 新增 `test_data_agent_sdk_layout.py`，固定必需目录和已删除旧模块。
- 现有 DataAgent middleware、SQL、数据库和图表测试全部迁移到新导入路径。
- 控制台与本地调试脚本改用 `agents` 图入口和 `tools.database`。

## 5、验证说明

- Python 编译检查通过。
- DataAgent 完整测试集：65 passed。
- Ruff check 与 format check 通过。
- 控制台执行脚本和本地调试脚本 `--help` 冒烟通过。
- DataAgent 任务范围 `git diff --check` 通过，Python 缓存已清理。
- 本机终端安全软件会隔离原始 `deerflow/skills/skillscan/orchestrator.py`；定向测试使用仓库已有的保守 SkillScan 实现临时替代，测试结束后恢复原文件，不属于本次代码变更。
