# 阶段 7：测试、安全检查与发布

## 1. 目标

完成代码、模型、数据访问和前端链路的验证，并按阶段灰度发布。

## 2. 测试范围

### 2.1 单元测试

- Contract 和 JSON Schema。
- State reducer。
- Tool allowlist。
- SQL AST 校验。
- 错误分类。
- Artifact 授权。
- Model Version 解析。

### 2.2 集成测试

- Lead -> SQL SubAgent。
- SQL SubAgent -> SQL Executor。
- SQL Executor -> 只读数据库。
- SQL Result -> Analysis。
- SQL/Analysis Result -> Chart。
- SubAgent Result -> Lead。

### 2.3 端到端测试

- 自动确认查询。
- 存在歧义的人工确认。
- SQL 生成和执行成功。
- SQL 执行失败后自动修复。
- Evidence 不足后重新检索。
- 查询、分析和图表组合请求。
- 页面刷新后的状态恢复。

### 2.4 安全测试

- Lead 直接调用 SQL 工具。
- SQL SubAgent 调用未授权工具。
- 伪造 Snapshot、validation digest 和 Artifact。
- DDL、DML、多语句、系统表和危险函数。
- DSN、密码和 Token 日志泄露。
- 超时、超大结果和资源耗尽。

## 3. 观测

每个任务记录：

- `thread_id`
- `run_id`
- `task_id`
- Agent 名称
- Model ID 和 Version
- Contract Version
- Tool 名称
- Attempt 数
- Error Code
- Token Usage
- 执行耗时
- Artifact ID

生产指标：

- 端到端成功率。
- SQL 首次成功率和修复成功率。
- Schema-RAG 调用次数。
- 各 Agent P50/P95 延迟。
- Token 使用量。
- 超时率、拒绝率和结果截断率。

## 4. 发布顺序

1. Contract、State 和权限控制。
2. DataAgent Lead 与 Schema-RAG。
3. SQL SubAgent 与 Direct Executor。
4. SQL Query Executor（如生产需要）。
5. Analysis SubAgent。
6. Chart SubAgent。
7. 专项模型灰度替换。

每一步都必须具有独立功能开关和回滚路径。

## 5. Todo

- [ ] 7.1 完成全部单元测试。
- [ ] 7.2 完成 SQL 只读数据库集成测试。
- [ ] 7.3 完成多 Agent 端到端测试。
- [ ] 7.4 完成安全测试和凭据泄露检查。
- [ ] 7.5 完成模型离线评测和基线对比。
- [ ] 7.6 完成 Tracing、RunEvent 和前端任务状态展示。
- [ ] 7.7 建立功能开关、灰度比例和回滚步骤。
- [ ] 7.8 更新 README、模块 AGENTS、`docs/guide/used-api.md` 和对应 Review。
- [ ] 7.9 在测试通过后合并回 `dev` 并推送 `origin/dev`。

## 6. Definition of Done

- [ ] 代码格式检查通过。
- [ ] 后端和前端单元测试通过。
- [ ] 关键集成测试和端到端测试通过。
- [ ] 模型离线指标达到发布阈值。
- [ ] SQL 权限和凭据检查通过。
- [ ] 文档与实现一致。
- [ ] 灰度和回滚经过验证。
