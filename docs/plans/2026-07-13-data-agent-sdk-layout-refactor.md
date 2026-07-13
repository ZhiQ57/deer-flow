# DataAgent SDK 目录重构计划

## 1、现状与约束

- [X] 1.1 检查当前分支、未跟踪文件和 DataAgent 既有实现，确认不覆盖未读取的用户工作。
- [X] 1.2 对照 `backend/packages/harness/deerflow` 的 `agents`、`agents/middlewares`、`tools/builtins`、`subagents` 和 `thread_state.py` 目录边界。
- [X] 1.3 梳理 DataAgent 单元测试、控制台执行脚本、本地调试脚本和文档中的旧导入路径。

## 2、SDK 目录重组

- [X] 2.1 保留 `agents/data_agent` 作为 DataAgent 图工厂和系统提示目录。
- [X] 2.2 将 QueryContext 与流程门禁拆到 `agents/middlewares`，每个 middleware 使用独立模块。
- [X] 2.3 将 DataAgent 状态扩展迁移到 `agents/thread_state.py`。
- [X] 2.4 将 SQL 校验、数据库执行和 ChartSpec 基础能力迁移到 `tools`。
- [X] 2.5 将三个模型可调用工具拆到 `tools/builtins`，由包入口统一返回工具列表。
- [X] 2.6 删除旧模块和重复 middleware，不保留旧导入路径兼容层。

## 3、调用方迁移

- [X] 3.1 更新 DataAgent 图工厂的内部导入与工具注册。
- [X] 3.2 更新 `backend/tests/service_agent/test-data-agent` 的测试和执行脚本导入。
- [X] 3.3 增加目录边界测试，防止业务实现重新堆回 `agents/data_agent`。

## 4、文档同步

- [X] 4.1 更新 `docs/guide/used-api.md` 的模块路径和当前 API。
- [X] 4.2 更新 DataAgent 使用说明、根 README 和 `backend/AGENTS.md` 的架构描述。
- [X] 4.3 新增 review 文档，记录目录边界、迁移结果和验证结论。

## 5、验证

- [X] 5.1 运行 DataAgent 定向 pytest。
- [X] 5.2 运行 Ruff 检查与格式校验。
- [X] 5.3 运行 Python 编译检查、执行脚本 `--help` 和任务范围 `git diff --check`。
- [X] 5.4 清理 `__pycache__`、`.pyc` 和临时日志，并复核 Git 状态。
