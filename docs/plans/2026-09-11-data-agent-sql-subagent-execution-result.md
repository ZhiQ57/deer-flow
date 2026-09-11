# DataAgent SQL MCP 外置化开发计划

## 1、需求确认与现状梳理

- [X] 1.1 复盘 2026 年 9 月 10 日服务器轨迹，确认 SQL 子代理没有真实执行，且 Lead-Agent 只看到“校验调用”文本。
- [X] 1.2 确认本次目标不是恢复 `SqlStageMiddleware`，而是把 SQL 执行能力移出 Gateway/AgentLoop。
- [X] 1.3 梳理 DeerFlow 当前 MCP 配置、stdio/HTTP 传输和 Docker Compose 扩展点。

## 2、新增隔离 MCP Extensions

- [X] 2.1 新增 `mcp-extensions/sql-execute` 独立 MCP Server、配置文件、依赖和启动入口。
- [X] 2.2 将只读 SQL 执行、结果 rows、结果预算和安全错误处理放入 `sql-execute`，工具只接收 SQL 字符串。
- [X] 2.3 新增 `mcp-extensions/sqltable-rag` 的隔离启动包装与配置，复用现有 TableRAG MCP 实现作为迁移过渡。
- [X] 2.4 提供开发阶段手动启动命令和 MCP 客户端配置示例。

## 3、DeerFlow 与 Docker 集成

- [X] 3.1 更新 `extensions_config.example.json`，提供 lead-agent / subagent 可分别配置的 MCP 工具示例。
- [X] 3.2 在开发与生产 Compose 中加入 `sql-execute` / `sqltable-rag` 服务、健康检查和内部网络配置。
- [X] 3.3 更新 Docker 配置说明，明确数据库凭据只注入 MCP Server，不进入 DeerFlow AgentLoop。

## 4、验证与文档

- [X] 4.1 增加 `sql-execute` 的本地单元测试，覆盖 SELECT、原始 rows、预算和错误结果。
- [X] 4.2 增加 MCP Server 启动/工具 schema 烟测，不依赖真实数据库轨迹。
- [X] 4.3 运行 ruff、后端配置测试和 MCP 扩展测试。
- [X] 4.4 编写 review 文档，明确服务器真实轨迹需由用户在部署环境进行端到端验证。

## 5、恢复前端手动 SQL 路由

- [X] 5.1 恢复 Gateway 手动 SQL 路由和运行上下文注册，使前端 SQL 执行按钮继续可用。
- [X] 5.2 保持模型/子代理只通过外部 MCP 执行 SQL，不注册旧 Gateway SQL Tool Provider。
- [X] 5.3 补回手动路由所需的 DataAgent service_ability 配置，并完成回归测试。
