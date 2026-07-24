# 阶段 1：Runtime 架构与权限

## 1. 目标

在现有 DeerFlow 正式 Runtime 中装配 DataAgent 和专项 SubAgent，不复制 Run 生命周期。

## 2. 正式入口

```text
Frontend
  -> assistantId=lead_agent
  -> context.agent_name=data-agent
  -> Gateway RunManager
  -> make_lead_agent()
  -> load_agent_config("data-agent")
  -> service_ability(data_query)
```

继续复用现有 Checkpoint、RunEventStore、SSE、Tracing、TokenUsage 和 SubagentExecutor。

## 3. Agent 配置

```yaml
name: data-agent
model: data-lead-model
allowable_subagents:
  - sql-subagent
  - analysis-subagent
  - chart-subagent
tool_groups:
  - file:read
service_ability:
  type: data_query
  version: 1
  sql_subagent_name: sql-subagent
```

```yaml
subagents:
  custom_agents:
    sql-subagent:
      model: sql-model
      tools:
        - data_validate_sql
        - data_execute_sql
      disallowed_tools:
        - task
        - bash
        - ask_clarification
      skills: []

    analysis-subagent:
      model: analysis-model
      tools:
        - analysis_execute
      disallowed_tools:
        - task
        - data_execute_sql

    chart-subagent:
      model: chart-model
      tools:
        - chart_build
      disallowed_tools:
        - task
        - data_execute_sql
```

## 4. 权限规则

- `task_tool` 必须在执行层校验 `allowable_subagents`。
- DataAgent Lead 只能看到当前允许的 SubAgent。
- SQL 工具只在目标 SubAgent、有效 Query Snapshot 和有效授权同时存在时装配。
- SQL SubAgent 不获得 `sqlrag_retrieve`，避免绕过 Lead 的业务确认。
- Analysis 和 Chart 只能读取显式传入的 Artifact。
- Slash Skill、Tool Search 和 MCP Promotion 不能扩大 Agent 的工具权限。

## 5. Sandbox

- Sandbox 是 Agent Tool 的隔离执行环境，不承载生产数据库。
- SQL Executor 若在 sandbox 外执行，SQL SubAgent 通过类型化工具调用 Executor。
- SQL Executor 若由 sandbox 直接调用数据库，必须使用短期凭据和受限网络地址。
- Docker sandbox 内不能将 `127.0.0.1` 当作宿主机数据库地址。

## 6. Todo

- [ ] 1.1 梳理 `make_lead_agent()` 中 service ability、task 和 SubAgent 配置装配顺序。
- [ ] 1.2 为 DataAgent 的专项 SubAgent 建立显式 allowlist。
- [ ] 1.3 在 `task_tool` 执行层增加 allowlist 校验。
- [ ] 1.4 确认每个 SubAgent 使用显式模型，不使用 `inherit`。
- [ ] 1.5 为 SQL、Analysis、Chart 建立最小工具白名单。
- [ ] 1.6 为 SubAgent 上下文传递定义允许字段和禁止字段。
- [ ] 1.7 增加 Runtime 装配、工具可见性和越权执行测试。

## 7. 阶段退出条件

- [ ] DataAgent Lead 可以调用三个专项 SubAgent。
- [ ] 未授权 SubAgent 在 Prompt、Tool Schema 和执行层均不可用。
- [ ] Lead 无法直接调用 SQL 执行工具。
- [ ] 各 SubAgent 无法调用 `task` 形成递归委派。
