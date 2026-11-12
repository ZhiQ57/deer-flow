# 同步官方上游与专项智能体执行路径分析

## 目标与边界

先将官方 `bytedance/deer-flow` 的最新 `main` 同步到本地 `main`，再完成与二次开发 `dev` 的集成、测试和评审。随后基于最新官方实现分析更便捷的 DataAgent 配置和执行方式。

保留前端智能体页面，以及会话请求携带 `agent_name=data-agent` 的路由方式。本次先同步和分析，不实施 DataAgent 执行路径重构；除用户批准的 subagent 字段迁移外，不修改运行时配置和凭据。

## 基线

- 同步前 `dev`：`954cbe39b`，工作区干净，与 `origin/dev` 一致。
- 同步前 `main`：`d5ae3882b`。
- 本次抓取的 `upstream/main`：`13e6fc29e54201ce650b5f48743f88444b4d46f5`。
- 上游待集成提交：1,531 个；`dev` 独有提交：118 个。
- 临时集成分支：`chore/sync-upstream-2026-11-12`，从当前 `dev` 创建。

## 1、同步准备

- [X] 1.1 核实工作区、远端地址、分支基线及模块开发规范。
- [X] 1.2 抓取 `upstream` 与 `origin`，确认上游范围并创建临时集成分支。
- [X] 1.3 快进本地 `main` 到本次抓取的 `upstream/main`，修正其跟踪分支。
- [X] 1.4 按用户追加要求，先提供冲突文件的差异、原因和舍弃影响；用户已逐项裁决 1–16 组处理方式。

当前在临时分支保留未完成的合并，`dev` 仍为 `954cbe39b`。冲突处理已暂停，尚未创建提交或推送。完整盘点见 [冲突清单](../reviews/2026-11-12-upstream-merge-conflicts.md)。

## 2、上游集成

- [X] 2.1 将更新后的 `main` 合并至临时分支，盘点并解决冲突。
- [X] 2.2 保留 DataAgent 前端入口、智能体标识传递、SQL 定制与本地开发能力。
- [X] 2.3 同步依赖锁文件，并审查官方配置、公共 API 和模块边界变化。

## 3、测试、评审与更新 dev

- [X] 3.1 执行后端定向回归、格式与必要的契约检查。
- [X] 3.2 执行前端检查及智能体、消息和 SQL 交互相关回归。
- [X] 3.3 复核冲突处理、未合并路径与最终差异，记录验证结果和限制。
- [X] 3.4 创建集成合并提交 `69448cb6b`，将验证后的结果快进至本地 `dev`（未推送）。

## 验证记录（2026-11-12）

- 后端定向回归：`tests/test_client.py`、`test_lead_agent_model_resolution.py`、`test_agent_assembly_descriptor.py`、`test_gateway_services.py`、`test_task_tool_core_logic.py`、`test_durable_context_middleware.py`，使用 `config.test.yaml` 配置运行，结果为 942 passed / 22 skipped。
- 本地 `config.dev.yaml` 的 `recursion_limit=10000` 会被 `max_recursion_limit=1000` 截断，导致两个官方 `build_run_config` 默认值断言（期望 100）在默认配置下失败；改用仓库的 `config.test.yaml` 后通过，属于本地运行配置差异，不是代码回归。
- 后端 `ruff check` 与 `ruff format --check` 对本次改动文件全部通过；`compileall` 通过。
- 前端 `pnpm check`（eslint + tsc）通过；消息/Markdown/审批/子任务相关 8 个测试文件共 68 个用例通过。
- 后端依赖使用 `uv sync --locked --extra postgres` 安装，前端使用 `pnpm install --frozen-lockfile` 同步。

## 4、专项智能体路径分析

- [ ] 4.1 对照当前 DataAgent 配置、提示词、前端请求与后端执行入口。
- [ ] 4.2 核实最新官方的自定义智能体、执行工厂、图注册和扩展机制及其支持边界。
- [ ] 4.3 给出保留 `agent_name` 路由的推荐执行方案、迁移步骤和仍需自定义的部分，形成分析文档。

## 验证原则

优先验证实际冲突及本地定制与官方改动交叉的路径。测试使用项目已有的 uv 环境与 pnpm 工具链，不调用真实模型、业务数据库或外部 MCP 服务。后端 Python 版本以仓库实际要求为准，当前项目要求 `>=3.12`。测试失败、环境限制及未执行项目必须如实记录。
