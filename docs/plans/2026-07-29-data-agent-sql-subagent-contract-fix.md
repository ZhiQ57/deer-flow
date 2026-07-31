# DataAgent SQL SubAgent 合同错误修复计划

## 1. 排查链路

- [X] 1.1 复盘本地 DataAgent 路由中标签确认后的阶段流转：`awaiting_confirmation` → `approved` → `task(sql-subagent)`。
- [X] 1.2 定位 `SQL_SUBAGENT_CONTRACT_INVALID` 的唯一生产点，确认错误由父级 `SqlStageMiddleware` 在解析 task 结果时产生。
- [X] 1.3 Review `task_tool` 与 SQL SubAgent 工具结果捕获方式，确认旧逻辑把 SQL 合同绑定在 `ToolMessage.content` 文本上。

## 2. 修复策略

- [X] 2.1 将 `data_validate_sql` / `data_execute_sql` 改为 `content_and_artifact` 双通道输出，保留模型可读 content，同时把结构化 SQL 结果放入 artifact。
- [X] 2.2 让 `task_tool` 从子任务工具步骤的 artifact 优先重建 `data_query_sql_result`，仅兼容旧 content JSON。
- [X] 2.3 让父级 `SqlStageMiddleware` 优先读取 task `artifact`，并在 task 已带 `SQL_*` 失败码时透传真实错误，避免二次误报合同错误。
- [X] 2.4 更新前端内部错误码文案和跨层合同文档。

## 3. 验证

- [X] 3.1 补充 artifact/content 截断场景回归测试。
- [X] 3.2 补充 task 失败码透传回归测试。
- [X] 3.3 运行后端 DataAgent/SQL/task 相关测试与 ruff。
- [X] 3.4 运行前端 DataAgent 消息解析单测。
