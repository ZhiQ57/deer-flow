# Gateway SQL API 与前端执行链路 Review

## 1. Review 范围

本次实现：

- `manual_ui` SQL 校验上下文。
- 线程级 Gateway SQL 执行 API。
- DataAgent SQL fenced code block 执行按钮。
- 桌面端右侧 SQL Result Panel 和移动端 Sheet。
- 成功、空结果、截断结果、SQL 错误和 Gateway 错误展示。
- 中英文文案、Backend Router 测试、Frontend Unit 和 E2E 测试。

本次未实现：

- 独立进程或独立部署的 Query Executor 服务。
- SQL 查询历史持久化。
- 真实生产数据库集成测试。
- Analysis SubAgent、Chart SubAgent 和完整 Agent Contract。

## 2. 实现结果

### 2.1 Gateway API

正式接口：

```text
POST /api/threads/{thread_id}/sql/execute
```

调用顺序：

```text
认证与 threads:write
  -> 严格 thread owner 校验
  -> 按 user_id + agent_name 加载 custom-agent
  -> 校验 data_query/sql_execution.enabled
  -> SqlExecutionService.validate(source="manual_ui")
  -> SqlExecutionService.aexecute()
  -> 返回 JSON 安全原始结果
```

同步 PostgreSQL/MySQL 驱动调用由 `aexecute()` 通过线程池运行。SQL 校验或数据库错误返回
`ok=false` 的稳定结果；线程、Agent、权限和请求合同错误使用 HTTP 4xx。

### 2.2 `manual_ui` 校验

手动执行不要求客户端提供 Query Snapshot 或 TableRAG registry。Service 使用当前 DataAgent 配置和
服务端 Secret 解析数据源绑定，并继续执行以下校验：

- 单条 PostgreSQL/MySQL `SELECT/WITH` AST。
- DDL、DML、多语句、危险函数和越权访问拒绝。
- Schema、Table 和 Column allowlist。
- 数据源 binding fingerprint。
- 自动行数限制和结果预算。

校验摘要包含 `source`，因此 `subagent` 与 `manual_ui` 不能复用对方的 validation result。

### 2.3 前端

新增：

```text
frontend/src/core/sql-execution/
frontend/src/components/workspace/sql-execution/
```

Streamdown 使用 SQL custom renderer，在已完成 SQL 代码块的 action 区按以下顺序渲染：

```text
Execute -> Download -> Copy
```

入口只在当前 custom-agent metadata 同时满足 `type=data_query` 和
`sql_execution_enabled=true` 时显示。流式 SQL、非 SQL 代码块和普通 Agent 保持原行为。

`ChatBox` 提供 `SqlExecutionProvider`，消息内执行按钮和 `sql-result` 右侧面板共享当前 SQL、Loading、
Result 和 Error。桌面端使用现有右侧 Panel，移动端使用现有 Sheet。

## 3. 安全检查

- API 使用现有认证、CSRF 和 `threads:write` 权限。
- SQL 执行要求 thread 存在且 owner 严格等于当前用户，不允许旧的空 owner 线程。
- Agent 配置按当前 user_id 加载。
- 浏览器不提交或接收 DSN、密码或数据库 Secret。
- Gateway 不返回数据库驱动对象、异常堆栈或未脱敏内部错误。
- 前端只负责发送 SQL 字符串和显示安全结果，不实现数据库连接或 SQL 安全策略。

## 4. 验证结果

- Backend SQL Executor、Gateway Router、Gateway docs 和 Agents Router：37 passed。
- Frontend Unit：88 files，756 passed。
- Frontend `pnpm check`：通过。
- Frontend `pnpm build`：通过。
- SQL Execute Button E2E：1 passed，使用本机 Chrome；默认 Playwright bundled Chromium 在当前机器未安装。
- Backend Ruff check 和 format：通过。

## 5. 遗留项

- 当前环境未注入可用于验收的真实只读 PostgreSQL/MySQL，因此真实数据库集成测试仍待执行。
- `secret://` DSN 需要请求级 Secret 时，浏览器手动执行不会接收客户端 Secret；部署应使用 Gateway
  进程环境变量，或以后为独立 Executor 增加服务端 Secret Provider。
- 当前右侧面板只保存本次页面会话结果，刷新页面后不恢复执行历史。
