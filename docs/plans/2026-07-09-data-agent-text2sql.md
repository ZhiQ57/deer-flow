# DataAgent Text2SQL 开发计划

## 1、现状确认

- [X] 1.1 阅读根目录 `AGENTS.md` 与后端/前端模块指南，确认 custom-agent、skills、MCP 的开发边界。
- [X] 1.2 确认当前 `main` 与 `dev` 指向同一提交，已从 `main` 新建功能分支 `feat/data-agent-text2sql`。
- [X] 1.3 确认已有 `table_rag` SDK 位于 `backend/packages/harness/table_rag`，TableRAG Skill 位于 `skills/public/z_sqltable-rag`。

## 2、DataAgent 原生配置设计

- [X] 2.1 确认 DataAgent 使用 DeerFlow 原生路径 `.deer-flow/users/default/agents/data-agent/`。
- [X] 2.2 设计 `config.yaml`：限定模型、工具组和 Text2SQL 所需 Skill 白名单。
- [X] 2.3 设计 `SOUL.md`：定义 Text2SQL 工作流、TableRAG MCP 使用约束、SQL 安全边界和交付格式。

## 3、TableRAG MCP 接入设计

- [X] 3.1 确认 TableRAG MCP 的 stdio 启动命令、环境变量和只读/管理工具开关。
- [X] 3.2 提供 `extensions_config` 示例，便于 DeerFlow 原生启动时启用 `tablerag_*` MCP 工具。
- [X] 3.3 明确本地 DSN、索引初始化、字段值同步由部署环境注入，不写入真实凭据。

## 4、实现与文档

- [X] 4.1 写入本地原生 DataAgent 运行时文件：`.deer-flow/users/default/agents/data-agent/config.yaml` 与 `SOUL.md`。
- [X] 4.2 写入可追踪的 DataAgent 模板和启动说明到 `docs/agents/data-agent/`。
- [X] 4.3 如涉及用户可见配置示例，同步更新 `extensions_config.example.json`、`README.md` 或相关开发指南。

## 5、验证与 Review

- [X] 5.1 增加或执行轻量测试，验证 DataAgent 配置、Skill 名称和 TableRAG MCP 配置可解析。
- [X] 5.2 执行可行的后端单测或定向 pytest；若环境未齐备，记录阻塞条件。
- [X] 5.3 编写 `docs/reviews/*` review 文档，记录变更、验证结果和 DeerFlow 原生启动测试步骤。

## 6、启动联调修复

- [X] 6.1 确认 `/workspace/agents/data-agent` 404 根因是前端缺少智能体根路径页面，实际聊天入口为 `/workspace/agents/data-agent/chats/new`。
- [X] 6.2 新增智能体根路径重定向页面，直接访问 `/workspace/agents/{agent_name}` 时跳转到新会话入口。
- [X] 6.3 将本地 `extensions_config.json` 的 `tablerag` 移入 `mcpServers.tablerag`，并指向已准备好的 `tabelrag.yaml`。
- [X] 6.4 恢复真实 `backend/packages/harness/table_rag` SDK 文件后，执行 TableRAG MCP 启动与检索验证。
- [X] 6.5 修复本地 extensions_config.json 中 TableRAG MCP 使用系统 Python 导致 No module named 'table_rag' 的问题。
- [X] 6.6 清空示例 mcpInterceptors 占位项，避免 my_package 未安装导致 MCP interceptor 警告。

