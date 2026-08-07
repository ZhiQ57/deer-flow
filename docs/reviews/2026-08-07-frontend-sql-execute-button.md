# 前端 SQL 执行按钮修复复盘

## 变更

- `SqlCodeBlock` 不再根据 `SqlExecutionProvider.enabled` 决定是否渲染执行按钮。
- 所有已完成的 SQL fenced code block 都显示执行按钮，按钮仍位于下载和复制操作之前。
- 点击时如果当前会话未具备可用执行能力，前端通过本地化 Toast 提示：
  - 中文：当前会话未开启 SQL 执行权限，请检查 DataAgent 配置或联系管理员。
  - 英文：SQL execution is not enabled for this conversation. Check the DataAgent configuration or contact an administrator.
- SQL 代码仍需等待流式内容完成后才能执行，避免把未完成的 SQL 发送到后端。

## 验证

- SQL/Markdown 相关单元测试：17 项通过。
- TypeScript 类型检查：通过。
- ESLint：通过。

本次提交只包含前端和文档文件，未修改后端代码。
