# Gateway SQL Execution 边界重构计划

## 1. 需求与边界

- [X] 1.1 SQL Execution 的 SQL 校验、数据源绑定、数据库驱动、只读事务、结果预算和错误分类归属 Gateway 后端。
- [X] 1.2 `deerflow-harness` 不直接连接业务数据库，不解析执行 DSN，不持有数据库 Secret。
- [X] 1.3 SQL SubAgent 只生成或修复 SQL，通过 Gateway 内部 API 获得校验、执行结果。
- [X] 1.4 前端继续使用现有手动执行 API，不接触内部认证、Snapshot 或数据库配置。
- [X] 1.5 Gateway 返回的 SQL 工具 artifact 是父智能体重建 SQL 结果的权威数据，SQL SubAgent 自由文本不具备数据库事实权限。

## 2. Git 与工作区

- [X] 2.1 确认原工作区存在未提交改动，不覆盖、不暂存、不提交这些改动。
- [X] 2.2 从本地最新 `dev` 创建 `refactor/gateway-sql-execution-boundary`。
- [X] 2.3 使用 `.worktrees/gateway-sql-execution-boundary` 隔离开发。
- [X] 2.4 开发、测试、Review 完成后提交需求分支。
- [X] 2.5 通过 fast-forward 合并回本地 `dev`。
- [ ] 2.6 推送 `origin/dev`。暂缓原因：本需求开始前本地 `dev` 已领先 `origin/dev` 149 个既有提交，直接推送会同时发布这些需求外历史，需由用户确认。

## 3. 后端模块

- [X] 3.1 新建 `backend/app/gateway/modules/sql_execution/` 垂直业务模块。
  - [X] 3.1.1 `contracts.py`：Gateway 请求、响应和内部 Service DTO。
  - [X] 3.1.2 `binding.py`：执行 DSN 与检索/执行数据源绑定。
  - [X] 3.1.3 `validator.py`：SQL AST、只读、方言、Schema 和 TableRAG registry 校验。
  - [X] 3.1.4 `drivers.py`：PostgreSQL/MySQL 只读驱动。
  - [X] 3.1.5 `service.py`：协调校验和执行；结果预算与错误分类拆分为独立文件。
  - [X] 3.1.6 `router.py`：手动 UI 与内部 SubAgent 路由。
- [X] 3.2 手动 UI 路由保持 `POST /api/threads/{thread_id}/sql/execute` 合同稳定。
- [X] 3.3 新增仅内部认证可调用的 SQL SubAgent API。
  - [X] 3.3.1 服务端解析 owner、thread、agent、approved snapshot。
  - [X] 3.3.2 请求体只接受候选 SQL、Snapshot/Run 身份，不接受 DSN、Secret、Schema 或 registry。
  - [X] 3.3.3 Gateway 从线程状态读取权威 retrieval、approval 和执行预算。
  - [X] 3.3.4 防止普通会话伪造内部来源。
- [X] 3.4 增加 Gateway 内部 SQL API Client/Tool Provider。
- [X] 3.5 增加 Router、Service、内部认证、线程所有权和错误脱敏测试。

## 4. 智能体模块

- [X] 4.1 删除 `deerflow.agents.service_agent.sql_executor` 数据库实现及公共导出。
- [X] 4.2 删除 SDK 内执行 DSN/Secret 解析。
- [X] 4.3 将 TableRAG 所需的绑定改为消费 Gateway 注入的无密钥绑定。
- [X] 4.4 为 Harness 增加通用、非 SQL 专属的 SubAgent 工具提供器扩展点。
- [X] 4.5 Gateway 注册 SQL SubAgent 工具提供器，Harness 不导入 `app.*`。
- [X] 4.6 `data_validate_sql` / `data_execute_sql` 改为 Gateway API 调用。
- [X] 4.7 删除 `task_tool` 向 SQL 工具传递请求级数据库 Secret 的逻辑。
- [X] 4.8 删除 `SqlStageMiddleware` 的本地 SQL Service 二次调用，改为验证权威 ToolMessage artifact。
- [X] 4.9 保留 `sql_only`、失败后重新校验、执行次数预算及成功后停止等行为。
- [X] 4.10 更新智能体流程、工具注入和 Harness/App 边界测试。

## 5. 前端模块

- [X] 5.1 确认手动执行 API URL 与请求字段保持兼容。
- [X] 5.2 补充前端 API 合同测试，确保不会发送内部来源、Snapshot、Run、Secret 或认证头。
- [X] 5.3 确认 SQL Result Panel 对成功、校验错误和数据库错误的展示不回退。
- [X] 5.4 SQL Execution 单元测试已通过；相关 E2E 已执行，受现有历史页面持续 `Loading...` 的环境问题阻塞，详见 Review。

## 6. 文档与公共 API

- [X] 6.1 更新 `docs/guide/used-api.md`。
- [X] 6.2 更新 `backend/AGENTS.md` 的 Harness/App 边界与 SQL Execution 说明。
- [X] 6.3 更新 `frontend/AGENTS.md` 的调用拓扑。
- [X] 6.4 更新相关 DataAgent README。
- [X] 6.5 删除旧导入路径，不保留兼容层。

## 7. 测试、Review 与 Debugger

- [X] 7.1 后端 SQL Service/Router 定向测试通过。
- [X] 7.2 DataAgent 查询流、task tool、client 和边界测试通过。
- [X] 7.3 本次变更范围后端 Ruff format/check 通过；全量基线问题记录于 Review。
- [X] 7.4 前端 SQL Execution 单元测试通过。
- [X] 7.5 前端 `pnpm check` 通过。
- [X] 7.6 完成 `docs/reviews/2026-07-31-gateway-sql-execution-boundary.md`。
- [X] 7.7 根据 Review 执行 Debugger 修复并复测。

## 8. 防偏移记录

### 后端当前结论

- SQL Execution 的最终部署位置是 `backend/app/gateway/modules/sql_execution/`。
- 手动 UI 和 SQL SubAgent 使用不同信任入口，共用 Gateway 内部 Service。
- 内部入口必须重新加载服务端权威线程、Agent 和 Snapshot 数据。
- SQL 数据库 Secret 只驻留 Gateway Run 能力注册表，并在 Run 结束时释放。

### 智能体当前结论

- SQL SubAgent 不执行数据库代码。
- Harness 只保留 Agent 编排、SQL 工具调用和结果 artifact 重建。
- SQL 工具实现由 Gateway 通过通用扩展点注入。
- Harness 运行上下文只接收脱敏能力投影和无密钥 binding。

### 前端当前结论

- 前端只调用手动 UI API。
- 前端合同原则上保持兼容，本需求主要增加防止内部字段泄漏的回归测试。
