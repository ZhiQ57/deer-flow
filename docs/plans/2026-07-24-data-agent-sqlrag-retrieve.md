# DataAgent 适配单一 SQLRAG MCP 工具计划

## 1、需求与现状确认

- [X] 1.1 阅读 `mcp_retrieval_tool_examples.md`，确认 MCP 只暴露 `sqlrag_retrieve`。
- [X] 1.2 确认 `sqlrag_retrieve` 通过 `operation` 区分六种只读检索方法。
- [X] 1.3 确认 raw 召回、索引校验、索引初始化和字段值同步已从 MCP 合同移除。
- [X] 1.4 确认本地 `dev` 比 `origin/dev` 超前 141 个提交，当前本地 `dev` 是本次开发基线。
- [X] 1.5 保留工作区内既有未提交修改，不把无关文件纳入本需求提交。

## 2、分支与影响范围

- [X] 2.1 从本地最新 `dev` 创建 `refactor/data-agent-sqlrag-retrieve` 分支。
- [ ] 2.2 梳理生产 DataAgent 的工具白名单、TableRAG 中间件、提示词和 Skill 合同。
- [ ] 2.3 梳理实验性 DataAgent、调试页面、示例配置和测试中的旧工具名。
- [ ] 2.4 确认公共 API 与模块边界影响，并同步更新 `docs/guide/used-api.md`。

## 3、测试驱动适配

- [ ] 3.1 先修改或新增测试，约束唯一工具名必须为 `sqlrag_retrieve`。
- [ ] 3.2 覆盖六种 `operation` 的只读检索识别与状态登记。
- [ ] 3.3 覆盖带 MCP Server 冗余前缀的旧命名不再进入 DataAgent 工具面。
- [ ] 3.4 覆盖提示词、Skill 和配置示例不再引用十个旧工具定义。

## 4、实现与文档

- [ ] 4.1 修改生产 DataAgent 工具筛选和 TableRAG 中间件，统一识别 `sqlrag_retrieve`。
- [ ] 4.2 修改实验性 DataAgent 和调试辅助代码，统一使用新工具合同。
- [ ] 4.3 更新 DataAgent SOUL、TableRAG Skill、MCP 合同、示例配置和用户说明。
- [ ] 4.4 更新 `README.md`、`backend/AGENTS.md` 与 `docs/guide/used-api.md`。

## 5、验证、审查与交付

- [ ] 5.1 运行相关后端测试和公共 Skill 测试。
- [ ] 5.2 运行 Ruff 格式与静态检查。
- [ ] 5.3 审查变更，修复发现的问题并复测。
- [ ] 5.4 编写 `docs/reviews/2026-07-24-data-agent-sqlrag-retrieve.md`。
- [ ] 5.5 使用中文规范提交本分支变更。
- [ ] 5.6 将功能分支合并回 `dev`，确认无关未提交修改仍被保留。
