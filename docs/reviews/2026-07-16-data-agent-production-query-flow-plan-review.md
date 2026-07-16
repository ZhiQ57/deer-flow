# DataAgent 正式闭环实施方案

正式实施基线：**本文档本身**。本轮不再引用或处理其他旧方案文档。

状态：**核心开发完成，进入联调与发布前验收收口**

在 deerflow 新增的代码位置, 必须打上注释: `# ADD: 代码描述`

# 任务背景

这个 DataAgent[backend.deer-flow\agents\data-agent] 是我基于 DeerFlow 的 custom-agent 机制配置的智能体，它能修改 DeerFlow 的默认主智能体, 从而实现定制化业务.

我为 DataAgent 增加了专属的Skill能力[skills\public\z_sqltable-rag].


## 期望目标
我现在希望实现, 用户提问查询数据库, DataAgent分析需求, 推理并借助table-rag检索工具, 找到用户真实意图, 然后调用标签工具[backend\packages\harness\deerflow\tools\builtins\query_labels_tool.py]和执行标签中间件.
将意图标签现在在前端页面(定制化), 然后可以选择模型让用户确认, 也可以模型自己执行下一步生成SQL, 执行SQL得到结果, 直到回答用户提问.

# 1、复审 Todo

- [X] 1.1 阅读正式 `lead_agent + agent_name` 运行链路。
- [X] 1.2 阅读 custom-agent 配置、工具装配、SubAgent、状态和 human-input 协议。
- [X] 1.3 阅读 `publish_query_labels`、`QueryLabelsMiddleware`、DataAgent 实验实现和现有测试。
- [X] 1.4 阅读前端消息分组、ToolMessage artifact、human-input 卡片和历史恢复链路。
- [X] 1.5 对照“不新增平行运行链路、不大幅改动 DeerFlow 原始流程”评估原计划。
- [X] 1.6 形成最小侵入修订建议。
- [X] 1.7 用户确认本复审结论后，回写正式实施计划。本复审文档作为本次实现的正式基线，未改动旧历史方案文档。
- [X] 1.8 正式计划确认后，才允许创建开发提交并进入 TDD 实现。

# 2、现状结论

## 2.1 DataAgent入口

通过 lead_agent 运行图加载 custom-agent：
```text
Frontend
  -> assistantId=lead_agent
  -> context.agent_name=data-agent
  -> Gateway RunManager / StreamBridge
  -> make_lead_agent()
  -> load_agent_config("data-agent")
      -> resolve service_ability(type=data_query, version=1)
      -> 在原工具面追加 publish_query_labels
      -> 在原 middleware 锚点追加 TurnReset / TableRagStage / QueryLabels / Approval / SqlStage
      -> ThreadState.service_states 保存唯一活动授权快照
      -> 通过原 task 工具委派 allowlisted sql-subagent
          -> 按当前 snapshot 动态装配 data_validate_sql / data_execute_sql
  -> create_agent()
```

这条链路可以复用既有线程、checkpoint、journal、SSE、停止运行、token usage 和 tracing， 不需要新增 DataAgent 专属 Gateway 路由。

# 3. 实施方案

## 3.1 custom-agent 配置新增 `service_ability` 字段
业务定制化智能体的逻辑配置位于: [backend\.deer-flow\agents], 本次我们以 DataAgent 作为首个实现对象。代码不得硬编码该物理目录，必须复用 `resolve_agent_dir()` / `load_agent_config()` 的“用户隔离目录优先、legacy 目录回退”解析规则。

当前 `load_agent_config()` 会丢弃 `AgentConfig` 未声明字段。因此新增 `service_ability` 后必须同步：

- [X] 在 `backend/packages/harness/deerflow/config/agents_config.py` 增加 `service_ability` 字典, 字典方便定制业务存在个性化的参数要求.
- [X] 让 `preserve_non_managed_fields()` 保留 `service_ability`，避免 UI 或 `update_agent` 重写配置时丢失。
- [X] Gateway Agents API 只返回脱敏后的 `service_ability` 能力元数据，方便前端判断是否展示 DataAgent 专属控件；不得返回 DSN、Secret 或连接参数。

config.yaml[backend\.deer-flow\agents\data-agent]配置文件示例如下:

```yaml
name: data-agent
description: 面向 Text2SQL / NL2SQL 的专业数据分析智能体.
model: Qwen3.6-plus

allowable_subagents:
  - default
  - sql-subagent

tool_groups:
  - web
  - file:read
  - file:write
  - bash

skills:
  - table-rag-agent
  - data-analysis
  - chart-visualization

service_ability:
  type: data_query
  version: 1
  enable_sql_rag: true
  table_rag_config: tablerag.yaml
  data_source_id: text2sql-mysql-local
  source_binding_mode: logical_data_source
  confirmation_mode: on_ambiguity
  min_auto_confidence: 0.85
  sql_subagent_name: sql-subagent
  sql_execution:
    enabled: true
    # 真实拓扑：PostgreSQL 保存 TableRAG 索引，MySQL 保存业务表。
    database_type: mysql
    dsn_env: DATA_AGENT_MYSQL_DSN
    readonly: true
    statement_timeout_seconds: 30
    max_rows: 500
    max_result_chars: 100000
```
新增 service_ability 字段关于 DataAgent 的能力参数说明：

| 字段 | 作用 |
|---|---|
| `type` | 业务能力适配器的唯一类型。DataAgent 查询闭环固定为 `data_query`，禁止依靠 agent 名称或猜测字段组合匹配。 |
| `version` | 业务能力配置合同版本。首版固定为 `1`，未知版本 fail closed。 |
| `enable_sql_rag` | v1 固定为 `true`，表示启用完整 TableRAG -> SQL 合同。若要停用该能力，应移除整个 `service_ability`，禁止保留无法正确装配的数据查询“半能力”。 |
| `table_rag_config` | 服务端 TableRAG 配置引用，示例为仓库根目录 `tablerag.yaml`。MCP 进程实际配置仍由 `TABLERAG_MCP_CONFIG/TABLERAG_CONFIG` 决定，部署时两者必须指向同一受控配置。 |
| `data_source_id` | 逻辑数据源标识，不保存 DSN。用于把 TableRAG 检索快照、意图标签、SQL 校验和 SQL 执行记录串到同一个数据源上下文。 |
| `source_binding_mode` | `same_physical_target` 要求检索/执行 fingerprint 相同；`logical_data_source` 用于服务端显式配置的异构索引/业务源映射。 |
| `confirmation_mode` | `auto`、`on_ambiguity`、`always`。默认建议 `on_ambiguity`。 |
| `min_auto_confidence` | `on_ambiguity` 自动继续的最低置信度，范围 `[0, 1]`；缺少 confidence、存在 ambiguities 或低于阈值时必须确认。 |
| `sql_subagent_name` | 负责 SQL 校验/执行的子智能体名称。 |
| `sql_execution` | SQL 执行源配置，包含方言、连接引用、只读约束和结果预算。 |

> 注:
> 1. `data_source_id` 解决三个问题：
> - 防错配：避免使用 A 库的 TableRAG Evidence，却去 B 库执行 SQL。
> - 可追踪：前端标签卡、后端 checkpoint、SQL SubAgent 返回值都能显示同一个来源 ID。
> - 可扩展：未来一个 DataAgent 支持多个业务库时，可以用它选择对应 `tablerag.yaml` 和 `sql_execution` 连接

## 3.2 代码任务细节

### 3.2.1 `thread_state`新增状态字段

[backend\packages\harness\deerflow\agents\thread_state.py]文件我已经准备好业务字段, 相关代码行: 238-262.

例如:
deerflow原有的解析流程不改动, 小幅度新增字段, 具体的验证和内部细节, 可以新增文件: `backend/packages/harness/deerflow/agents/service_agent/data_agent_thread_state.py` 写好状态字段, 在外层 `thread_state.py` 引入业务依赖字段放入 `service_states`.

- [X] 根据DataAgent需求补充业务 state 和导入流程.


### 3.2.2 业务状态字段快照

新增业务状态字段的快照消息存储功能, DeerFlow服务端代码应该增加消息类型和存储, 以便断网续连时, 状态字段依然保留.

- [X] 为服务端增加 DataAgent 状态字段快照
及时保留DataAgent给予用户展示的标签.

### 3.2.3 业务中间件如何自适应

[backend\packages\harness\deerflow\agents\lead_agent\agent.py]代码内, 我写入了硬编码 `agent_name == "data-agent"`,决定插入中间件.但是我希望找到更好的自适应方法, 不需要硬编码就可以自适应插入定制中间件.

- [X] 优化业务定制中间件的动态插入, 不破坏原有中间件编排流程.

## 3.4. 新增SQL SubAgent 方案

### 3.4.1 子智能体定位

新增 `sql-subagent`，只负责候选 SQL 生成、SQL 可行性验证和只读执行，不负责 TableRAG 检索、意图确认或与用户直接对话。

主 DataAgent 职责：

- 理解用户问题。
- 调用 TableRAG 检索。
- 发布查询标签。
- 处理用户确认。
- 把确认后的结构化上下文交给 `sql-subagent`。
- 汇总 SQL SubAgent 的结构化结果并回答用户。

SQL SubAgent 职责：

- 根据意图快照和 TableRAG Evidence 生成候选 SQL.
- 校验 SQL 是否只读、是否符合方言、是否只引用允许 Schema/Table/Column.
- 必要时修正 SQL.
- 使用 custom-agent `service_ability.sql_execution` 指定的执行源只读执行。
- 返回结构化结果、SQL、列信息、行数、截断信息和错误说明。

### 3.4.2 子智能体配置

按 DeerFlow subagent 配置规范新增：

```yaml
subagents:
  custom_agents:
    sql-subagent:
      description: "验证并执行 DataAgent 生成的只读 SQL"
      system_prompt: >
        只根据父 DataAgent 提供的已确认意图快照与 Evidence 生成 SQL；
        必须先校验再执行，只返回约定的 JSON 结果，不与用户交互，也不自行检索或猜测数据源。
      model: Qwen3.6-plus
      tools:
        - data_validate_sql
        - data_execute_sql
      disallowed_tools:
        - task
        - ask_clarification
        - present_files
      skills: []
      max_turns: 12
      timeout_seconds: 60
```

实际字段以当前 `subagents_config` 结构为准。`data-agent` 的 `allowable_subagents`
应包含 `sql-subagent`


- [X] 设计和实现完整的 SQL SubAgent.


# 4. P0 问题

### 4.1 标签快照必须由服务端补全和签名

模型不应负责生成 `snapshot_id`，也不能仅凭模型填写的 Evidence 文本判断数据库来源标签可信。

修订后的标签合同：

- 模型输入保留 `intent`、`labels`、`summary`，新增结构化 `confidence` 和 `ambiguities`。
- middleware 根据当前用户消息 ID、规范化标签、`data_source_id` 和 TableRAG retrieval digest 计算 `snapshot_id`。
- `source=database` 的标签必须引用当前 retrieval state 中真实存在的 Evidence ID；不能只检查任意非空字符串。
- ToolMessage artifact 是持久化真相源；custom stream event 只用于低延迟展示。
- 后一次快照替换前一次，旧 approval、validation 和 execution 同步失效。

### 4.2 确认流程必须绑定快照

原计划只描述确认动作，没有定义 stale response、防并发和跨轮次恢复规则。

首版复用现有 human-input 协议：

- `QueryLabelsMiddleware` 或专属 approval middleware 在需要确认时，把 `human_input` 请求写入同一 ToolMessage artifact 并结束本次 run。
- request artifact 同时携带 `request_id` 和 `snapshot_id`；前端回复继续使用 DeerFlow v1 `human_input_response`，只回传 `request_id`，后端通过当前 pending state 将它解析并绑定到唯一 `snapshot_id`，不修改通用回复协议。
- 首版复用 `source=ask_clarification` 的持久化通道，并使用 `data-query:` 前缀隔离 `request_id`；不得新增一个无法被 journal 保存的临时 response source。
- 前端继续使用隐藏 HumanMessage 的 `additional_kwargs.human_input_response` 回复。
- 后端只接受与当前 pending request 和 snapshot 同时匹配的响应。
- `execute`、`sql_only`、`cancel` 为结构化选项；自由文本视为“修改条件并重新检索”。
- 自由文本修改必须清除旧 retrieval、labels、approval、validation 和 execution，再重新进入 TableRAG。
- `on_ambiguity` 必须增加明确的自动阈值配置；缺少 confidence 时按需确认处理。

### 4.3 前端实施概要 Todo

> 本节保留前端业务验收概要；第 11 节是可编码的详细任务。AI 完成第 11 节对应任务并验证后，才能同步勾选本节，禁止把两处当成两套实现。

#### 4.3.1 类型与协议

- [X] 4.3.1.1 新增查询意图快照 TypeScript 类型。
- [X] 4.3.1.2 新增 `data_query_approval` 类型。
- [X] 4.3.1.3 新增 SQL SubAgent 结构化结果类型。
- [X] 4.3.1.4 增加 ToolMessage artifact / custom event 的运行时解析器。
- [X] 4.3.1.5 Agent API 类型返回脱敏后的 `service_ability` 能力元数据，用于识别 DataAgent 专属页面能力。

#### 4.3.2 查询意图卡

- [X] 4.3.2.1 新增 `QueryIntentCard`。
- [X] 4.3.2.2 展示意图摘要、指标、维度、过滤、时间、排序、Top N 和图表倾向（以结构化标签统一展示）。
- [X] 4.3.2.3 展示标签来源：`user/database/derived`。
- [X] 4.3.2.4 展示 TableRAG Evidence 摘要。
- [X] 4.3.2.5 展示置信度和歧义提示。
- [X] 4.3.2.6 支持自动批准、待确认、已确认、已修改、已取消状态。

#### 4.3.3 确认交互

- [X] 4.3.3.1 复用现有 human-input 回复通道。
- [X] 4.3.3.2 支持确认并执行。
- [X] 4.3.3.3 支持仅生成 SQL。
- [X] 4.3.3.4 支持修改查询条件。
- [X] 4.3.3.5 支持取消查询。
- [X] 4.3.3.6 待确认期间避免并发普通输入破坏 `snapshot_id` 绑定。

#### 4.3.4 SQL 结果展示

- [X] 4.3.4.1 展示 SQL SubAgent 返回的 SQL。
- [X] 4.3.4.2 展示执行状态、耗时、行数和截断状态。
- [X] 4.3.4.3 展示结构化表格结果。
- [X] 4.3.4.4 支持失败时展示校验失败原因或执行错误摘要。
- [X] 4.3.4.5 页面刷新后从历史消息和 checkpoint 恢复标签卡与执行结果

# 5、服务能力配置与动态解析 TODO

> 本节在前文 `service_ability` 设计基础上继续细化，只追加实施步骤，不删除前文配置和结论。

## 5.1 `service_ability` 配置模型

- [X] 5.1.1 在 `AgentConfig` 增加 `service_ability: dict[str, Any] | None`，保持自定义业务参数可扩展，不把 DataAgent 专属字段硬编码进通用 Agent 顶层字段。
- [X] 5.1.2 明确 `service_ability` 的未知字段策略：顶层字典允许扩展，DataAgent 运行时对已知字段执行严格校验，未知字段只保留并记录 debug 日志，不得静默改变工具权限。
- [X] 5.1.3 增加 `ServiceAbilityConfig` 解析器，统一读取 `type`、`version`、`enable_sql_rag`、`table_rag_config`、`data_source_id`、`confirmation_mode`、`min_auto_confidence`、`sql_subagent_name` 和 `sql_execution`。
- [X] 5.1.4 为 `confirmation_mode`、`database_type`、超时、最大行数和结果字符数增加边界校验；布尔值不得被 `int()` 静默转换。
- [X] 5.1.5 校验 `dsn_env` 只能是环境变量名或已登记的 Secret 引用，禁止在 `service_ability` 中直接保存 DSN、密码或访问令牌。
- [X] 5.1.6 通过现有 `preserve_non_managed_fields()` 保留完整 `service_ability`，覆盖 harness `update_agent` 和 Gateway Agents API 两条配置写回路径。
- [X] 5.1.7 Agents API 只返回脱敏后的 `service_ability` 元数据，不返回 DSN、Secret 内容或数据库连接参数。
- [X] 5.1.8 增加配置加载失败的可读错误：字段路径、非法值和修复建议必须明确，但错误信息不得回显 Secret。

## 5.2 业务能力解析注册表

- [X] 5.2.1 新增轻量级 `service_agent` 能力注册模块，不引入会改变默认 lead-agent 流程的通用 Runtime Profile。
- [X] 5.2.2 定义能力适配器接口：匹配配置、校验配置、提供状态字段、提供工具、提供 middleware、提供 prompt appendix 和提供前端能力元数据。
- [X] 5.2.3 注册 `DataAgentServiceAbility`，仅通过 `service_ability.type == "data_query"` 和受支持的 `version` 匹配启用，不再依赖 `agent_name == "data-agent"` 或字段猜测装配中间件。
- [X] 5.2.4 解析顺序必须确定：无业务能力时返回默认空适配器；多个能力同时匹配时 fail closed，禁止随机选择。
- [X] 5.2.5 能力适配器只负责业务扩展，不能复制 `get_available_tools()`、Gateway run 生命周期、checkpoint 或 tracing 逻辑。
- [X] 5.2.6 为普通 custom-agent、bootstrap 和默认 lead-agent 增加回归测试，确认服务能力注册表不会改变原有工具、middleware 顺序和 state schema。
- [X] 5.2.7 将解析出的能力名称、版本和脱敏配置摘要写入运行 metadata，供 journal、日志和前端调试使用。

# 6、ThreadState 与服务状态快照 TODO

## 6.1 `service_states` 数据结构

- [X] 6.1.1 保留 `thread_state.py` 的 `service_states` 通用扩展入口，但修正当前 `dict.fromkeys(existing + new)` 对 dict 不可哈希、必然抛出 `TypeError` 的 reducer；不得把现有错误实现当作兼容行为保留。
- [X] 6.1.2 新增 `backend/packages/harness/deerflow/agents/service_agent/data_agent_thread_state.py`，定义 DataAgent 专属状态类型和字段注释。
- [X] 6.1.3 将 DataAgent 状态包裹为带命名空间的服务状态快照，至少包含 `service_name`、`version`、`snapshot_id`、`stage`、`payload` 和 `updated_at`。
- [X] 6.1.4 `payload` 内统一承载 `data_query_labels`、`data_query_approval`、`data_retrieval_context`、`data_sql_validation`、`data_sql_execution` 等业务字段，避免向 ThreadState 顶层散落大量业务键。
- [X] 6.1.5 为服务状态实现按 `service_name` 维护一个活动快照的替换 reducer；相同 `snapshot_id` 重复写入必须幂等，新 `snapshot_id` 替换活动快照，历史版本只保存在 ToolMessage artifact/journal，禁止 state 列表无界增长。
- [X] 6.1.6 明确定义新用户问题、用户修改条件、用户确认恢复和普通历史恢复四种状态合并行为。
- [X] 6.1.7 旧快照被替换时，必须同时失效旧 approval、SQL candidate、validation、execution 和 final result，不能只替换标签卡。
- [X] 6.1.8 为空结果、失败结果和成功结果定义明确状态，不允许一次失败的检索或执行覆盖同一轮次最近一次可用成功结果。

## 6.2 服务状态持久化与恢复

- [X] 6.2.1 确认 LangGraph checkpoint 会完整保存 `service_states`，并增加 checkpoint round-trip 单元测试。
- [X] 6.2.2 为服务状态增加版本化序列化器，读取未知版本时返回受控错误或只读降级，不得导致整个线程历史无法加载。
- [X] 6.2.3 为 DataAgent 标签和 SQL 结果生成持久化 ToolMessage artifact，artifact 与 `service_states` 必须引用相同 `snapshot_id`。
- [X] 6.2.4 custom event 只承担实时 UI 更新，不作为断线续连、页面刷新和历史恢复的唯一数据源。
- [X] 6.2.5 检查 StreamBridge、run journal、thread history 和 checkpoint values 的字段传递，确保服务状态不会在任一层被过滤或截断。
- [X] 6.2.6 为 artifact、服务状态和日志增加大小上限；超出上限时保留摘要、digest 和引用，不直接把完整数据库结果写入每个 checkpoint。
- [ ] 6.2.7 增加断网续连、停止运行后恢复、页面刷新和从历史消息重新打开四类测试。

# 7、DataAgent 中间件动态装配 TODO

## 7.1 装配入口

- [X] 7.1.1 在 `make_lead_agent()` 读取已解析的 `service_ability`，构造 DataAgent 能力适配器；默认 Agent 仍走现有分支。
- [X] 7.1.2 在现有 `get_available_tools()` 之后、Skill 工具过滤之前追加服务能力工具，确保工具策略仍能看到完整的业务工具集合。
- [X] 7.1.3 在现有 middleware 链的稳定锚点注入 DataAgent middleware，不复制整条 lead-agent middleware 链。
- [X] 7.1.4 保持现有顺序约束：状态重置在本轮业务处理之前，TableRAG 阶段门禁在标签和 SQL 工具之前，ClarificationMiddleware 仍保持末端拦截。
- [X] 7.1.5 将现有 `agent_name == "data-agent"` 的 QueryLabelsMiddleware 直接追加逻辑迁移为服务能力适配器输出，迁移前后普通 Agent 的 middleware 列表必须一致。
- [X] 7.1.6 对 `DeerFlowClient`、Gateway `make_lead_agent()` 和测试用的直接图工厂分别验证能力装配行为，避免只修复 Gateway 路径。

## 7.2 DataAgent middleware 职责拆分

- [X] 7.2.1 `DataAgentTurnResetMiddleware` 只处理新一轮可见用户消息，隐藏 human-input 响应不得误触发全量重置。
- [X] 7.2.2 `TableRagStageMiddleware` 记录当前轮次检索尝试、成功标志、retrieval digest、Evidence 引用和数据源标识。
- [X] 7.2.3 `QueryLabelsMiddleware` 拦截标签工具，校验标签来源、Evidence、快照字段并写入 artifact 与 service state。
- [X] 7.2.4 `QueryApprovalMiddleware` 处理 `auto`、`on_ambiguity`、`always`，并把等待确认请求写入可恢复的 human-input artifact。
- [X] 7.2.5 `SqlStageMiddleware` 阻止未完成标签、未确认或快照过期时的 SQL SubAgent 调用。
- [X] 7.2.6 `SqlStageMiddleware` 将 SQL SubAgent 的结构化结果投影回 service state，同时保留原始 ToolMessage 供前端展示。
- [X] 7.2.7 每个 middleware 同时实现同步和异步 hook；SQL 驱动只在同步 Tool 执行面运行，本次差异的 changed blocking-IO 扫描未发现新增候选。
- [X] 7.2.8 为每个 middleware 增加“允许调用”和“拒绝调用”测试，错误 ToolMessage 必须给模型可执行的修复提示。

# 8、TableRAG 检索与标签快照 TODO

## 8.1 检索结果登记

- [X] 8.1.1 识别 TableRAG 完整检索工具和单路补充工具的实际注册名称，兼容 MCP 前缀和 `_tablerag_retrieve` 后缀规则。
- [X] 8.1.2 只登记当前用户轮次成功的 TableRAG 检索结果；`ok=false`、空结果和异常结果不得被当作 Evidence。
- [X] 8.1.3 从结果中提取并规范化 `evidences`、`tables`、`columns`、`values` 和 `join_graphs`，保存摘要与 digest，不把完整原始响应无限写入 state。
- [X] 8.1.4 禁止通过普通标签工具调用绕过 TableRAG 检索门禁；DataAgent 进入数据库查询流程后，首次有效检索必须先于标签发布。
- [X] 8.1.5 多次检索时保留当前轮次有效 Evidence 的合并视图，并记录每次检索来源和时间；同轮后续空结果只写安全失败记录，不覆盖前一次成功 Evidence。
- [X] 8.1.6 检索源的 `data_source_id` 必须与 `service_ability.data_source_id` 相同，不一致立即阻止后续标签和 SQL 阶段。

## 8.2 标签快照合同

- [X] 8.2.1 冻结标签 artifact v1 JSON Schema，并同步写入 `contracts/` 中的跨组件合同文件。
- [X] 8.2.2 标签快照至少包含 `version`、`snapshot_id`、`data_source_id`、`summary`、`intent`、`confidence`、`labels`、`ambiguities` 和 retrieval digest。
- [X] 8.2.3 服务端根据当前用户消息 ID、规范化标签、数据源 ID 和检索摘要计算 `snapshot_id`，模型不得传入可覆盖的 snapshot ID。
- [X] 8.2.4 `source=database` 的标签必须带真实 Evidence 引用；Evidence 引用不存在、跨数据源或不属于当前轮次时拒绝发布。
- [X] 8.2.5 对标签名称、值、Evidence 摘要、ambiguities 数量和总体 artifact 大小设置上限，避免模型生成超大状态或前端卡片。
- [X] 8.2.6 同一轮次重复发布标签时使用幂等替换；新快照写入后清除旧 approval、validation 和 execution 引用；同一 AIMessage 的重复标签调用只保留第一个。
- [X] 8.2.7 为标签发布失败、Evidence 缺失、低置信度、重复发布和快照冲突补充单元测试。

# 9、确认与人工输入闭环 TODO

## 9.1 确认请求

- [X] 9.1.1 明确 `on_ambiguity` 的置信度阈值配置和缺少 confidence 时的 fail-closed 行为。
- [X] 9.1.2 确认请求必须包含 `request_id`、`snapshot_id`、当前标签摘要、ambiguities 和最小选项集合。
- [X] 9.1.3 `execute` 表示确认后继续 SQL 校验和执行，`sql_only` 表示只生成并校验 SQL，`cancel` 表示结束当前查询。
- [X] 9.1.4 自由文本回复视为查询条件修改，必须进入新检索流程，不能直接修改旧 SQL 字符串后执行。
- [X] 9.1.5 非交互运行环境或 `disable_clarification` 场景不得等待 human-input；必须根据策略安全停止并说明未解决的歧义。

## 9.2 回复校验与恢复

- [X] 9.2.1 从隐藏 HumanMessage 的 `human_input_response` 读取回复，校验 `source`、`request_id` 和 response kind，再用服务端当前 pending request 将 `request_id` 解析到唯一 `snapshot_id`；不得要求现有 v1 回复携带其合同中不存在的 `snapshot_id`。
- [ ] 9.2.2 过期、重复、跨线程或跨数据源的回复必须返回错误提示，不得改变当前有效快照。
- [X] 9.2.3 确认恢复运行时保留当前有效 retrieval 和 labels，仅清理需要重新生成的 SQL 阶段字段。
- [X] 9.2.4 修改条件恢复运行时清空旧 retrieval、labels、approval、validation、execution 和 chart 状态，再重新执行 TableRAG。
- [X] 9.2.5 取消查询后写入终态 `cancelled`，禁止同一 pending request 再次触发 SQL。
- [ ] 9.2.6 为确认前禁止执行、确认后继续、修改后失效、重复回复和页面刷新恢复补充后端和前端测试。

# 10、SQL SubAgent 与工具 TODO

## 10.1 SubAgent 合同

- [X] 10.1.1 按当前 `subagents_config` 的必填字段补齐 `description`、`system_prompt`、`tools`、`disallowed_tools`、`skills`、`model`、`max_turns` 和 `timeout_seconds`，不能只依赖示例中的部分字段。
- [X] 10.1.2 冻结 `sql-subagent` 输入合同：`snapshot_id`、`data_source_id`、意图标签、TableRAG Evidence 摘要、SQL 生成约束和服务能力版本。
- [X] 10.1.3 冻结 `sql-subagent` 输出合同：顶层固定 `version/kind/snapshot_id/data_source_id/validation/execution`；SQL、列、行数、截断和安全错误码分别归入 `validation`、`execution`，禁止同时维护扁平重复字段。
- [X] 10.1.4 SQL SubAgent 只允许调用 `data_validate_sql`、`data_execute_sql`，不允许 `task`、`ask_clarification`、文件读写、bash、TableRAG 索引写操作或其他业务工具。
- [X] 10.1.5 为父 DataAgent 与 SQL SubAgent 设计服务能力上下文传递机制，不能让子智能体自行猜测 DSN、数据库类型或数据源。
- [X] 10.1.6 为父子调用增加 `parent_run_id`、`thread_id`、`snapshot_id` 和 `data_source_id` 关联，保证 tracing、journal 和前端状态可追踪。
- [X] 10.1.7 明确 SQL SubAgent 结构化结果如何从 `task` ToolMessage 回写父 DataAgent service state；无法解析或 schema 不匹配时必须 fail closed。

## 10.2 SQL 工具注册与权限

- [X] 10.2.1 统一确定工具名称为现有 Skill、实验实现和测试已经使用的 `data_validate_sql` 与 `data_execute_sql`；不得新增 `data_sql_validate` / `data_sql_execute` 别名兼容层。
- [X] 10.2.2 SQL 工具只在 DataAgent service ability 和 `sql-subagent` 显式 allowlist 中出现，默认 lead-agent 不得看到工具 schema。
- [X] 10.2.3 SQL 校验工具必须绑定当前 `snapshot_id`、`data_source_id` 和 Evidence 摘要，禁止脱离当前查询上下文独立执行。
- [X] 10.2.4 SQL 执行工具只接受最近一次成功校验返回的 `executable_sql` 或校验 digest，拒绝模型直接提交未校验 SQL。
- [X] 10.2.5 校验单条只读 SQL、匹配 `service_ability.sql_execution.database_type`、限制 Schema/Table/Column、禁止多语句和写操作。
- [X] 10.2.6 执行前再次校验快照、数据源、校验 digest 和权限；任一不匹配均返回受控失败，不建立数据库连接执行。
- [X] 10.2.7 使用只读账号、只读事务、statement timeout、最大行数、最大单元格尺寸和最大结果字符数。
- [X] 10.2.8 对数据库异常做安全格式化，禁止向模型、artifact、日志和前端泄露 DSN、密码、网络拓扑或内部堆栈。
- [X] 10.2.9 为 SQL 校验绕过、SQL 注入、危险函数、跨库访问、超时、空结果、截断结果和重复执行补充测试。

# 11、前后端协议与 UI 集成 TODO

## 11.1 后端消息与事件

- [X] 11.1.1 确定 DataAgent 标签、确认、SQL 校验和 SQL 执行的 ToolMessage artifact 字段命名，避免同时存在多套相似协议。
- [X] 11.1.2 为 artifact 增加 `version`、`kind`、`snapshot_id`、`data_source_id` 和 `service_name`，方便前端严格解析。
- [X] 11.1.3 custom event 与 values 状态必须使用同一快照内容或 digest，前端收到乱序事件时不得回退到旧标签。
- [X] 11.1.4 保留普通 ToolMessage 文本 fallback；未知版本 artifact 必须降级为普通工具步骤，不得导致整个消息列表崩溃。
- [ ] 11.1.5 确认 `run_id`、`thread_id` 和消息 ID 在实时消息、历史消息和刷新恢复后的身份一致。

## 11.2 前端标签与确认卡

- [X] 11.2.1 在 `frontend/src/core/messages` 增加 DataAgent artifact 运行时解析器和 TypeScript 类型，拒绝不完整、超长或跨版本 payload。
- [X] 11.2.2 在现有消息分组中识别 `publish_query_labels` 的标签 artifact，新增 `QueryIntentCard` 展示标签和 Evidence。
- [X] 11.2.3 标签卡显示当前快照状态：`published`、`awaiting_confirmation`、`approved`、`modified`、`cancelled`、`failed`（confirmed response 状态由持久化 human-input response 推导）。
- [X] 11.2.4 确认按钮通过现有 `sendMessage` 第四参数发送 `hide_from_ui` 和 `human_input_response`，不得把确认 payload 放入普通运行 context。
- [X] 11.2.5 待确认时复用 `hasOpenHumanInputRequest()` 禁用普通输入；确认提交失败时恢复可重试状态。
- [ ] 11.2.6 处理乱序 custom event、重复 ToolMessage、旧快照和页面刷新，始终以最新有效 `snapshot_id` 为准。
- [X] 11.2.7 SQL 结果卡展示 SQL、校验状态、执行状态、耗时、列、结果行、行数、截断和安全错误摘要。
- [X] 11.2.8 普通 custom-agent 没有 DataAgent artifact 时保持原有消息渲染，不出现空白标签卡或错误提示。

## 11.3 前端测试

- [X] 11.3.1 为 artifact parser 编写正常、缺字段、未知版本、超长字段和恶意字符串测试。
- [X] 11.3.2 为 QueryIntentCard 编写标签来源、Evidence、ambiguities、置信度和状态转换测试。
- [ ] 11.3.3 为确认卡编写 execute、sql_only、modify、cancel 和重复提交测试。
- [ ] 11.3.4 为页面刷新、历史消息、断线续连和旧快照乱序恢复编写测试。
- [ ] 11.3.5 运行 `pnpm test`、`pnpm typecheck`、`pnpm lint`、`pnpm check` 和关键 Playwright E2E。

# 12、后端测试与质量门禁 TODO

## 12.1 单元测试

- [X] 12.1.1 测试 `service_ability` 加载、默认值、未知字段、非法值和配置写回不丢字段。
- [X] 12.1.2 测试服务能力注册表匹配、冲突、空能力和普通 Agent 旁路。
- [X] 12.1.3 测试 `service_states` reducer 的新增、替换、幂等、失效和失败保留逻辑。
- [X] 12.1.4 测试 TableRAG 结果解析、Evidence digest、数据源绑定和检索失败处理。
- [X] 12.1.5 测试标签快照 hash 稳定性、Evidence 引用、重复发布和过期快照拒绝。
- [X] 12.1.6 测试确认策略、human-input 响应解析、修改条件重置和取消终态。
- [X] 12.1.7 测试 SQL SubAgent 输入/输出合同、工具 allowlist 和结构化结果回写。
- [X] 12.1.8 测试 SQL 校验、执行预算、只读限制、错误脱敏和重复执行阻止。

## 12.2 集成与 E2E

- [X] 12.2.1 使用 fake model 验证完整流程：用户问题 -> TableRAG -> 标签 -> 自动/等待确认 -> SQL SubAgent -> SQL 结果 -> 最终回答。
- [ ] 12.2.2 使用 Gateway RunManager 验证正式 `lead_agent + agent_name=data-agent` 路径，不新增路由。
- [X] 12.2.3 验证普通 custom-agent 的工具、middleware、state 和前端渲染不受影响。
- [X] 12.2.4 验证需要人工确认的策略分支在用户确认前绝不会调用 SQL 校验或执行工具；`auto` 分支只有在快照完整、无歧义且满足阈值时才可自动继续。
- [X] 12.2.5 验证用户修改条件后旧 snapshot、approval、validation 和 execution 不会复用。
- [ ] 12.2.6 验证停止运行、断线重连、checkpoint 恢复和页面刷新后的标签/结果一致性。
- [X] 12.2.7 在真实 PostgreSQL TableRAG 索引 + MySQL 业务源环境验证逻辑数据源绑定、只读事务、timeout、行数和结果预算；同时保留 PostgreSQL `same_physical_target` 单元回归，禁止只用 fake DB 作为唯一验收。
- [ ] 12.2.8 对关键路径执行并发测试，确认同一 thread 的快照不会被并行运行互相覆盖。

## 12.3 DeerFlow 质量门禁

- [ ] 12.3.1 运行 backend 定向测试和完整 `make test`。
- [ ] 12.3.2 运行 `make format`、`make lint` 和 blocking-IO 检测/回归测试。
- [X] 12.3.3 运行 harness/app import boundary 测试，确保 harness 不反向依赖 `app.*`。
- [X] 12.3.4 检查新增同步数据库 IO 不会直接阻塞 async graph；changed blocking-IO 扫描确认本次差异未新增候选。
- [X] 12.3.5 检查测试、日志、fixture 和文档中没有真实凭据、完整 DSN 或临时产物。

# 13、Skill、合同与开发文档 TODO

## 13.1 Skill 与合同

- [X] 13.1.1 更新 `skills/public/z_sqltable-rag/SKILL.md`，统一 `service_ability`、`data_validate_sql/data_execute_sql`、SQL SubAgent 和确认快照术语。
- [X] 13.1.2 明确 Skill 只能把 TableRAG Evidence 作为数据库事实来源，不能伪造 `snapshot_id`、Evidence 或 SQL 执行结果。
- [X] 13.1.3 在 Skill 中写明 `sql_only`、`execute`、`cancel` 和修改条件后的重新检索行为。
- [X] 13.1.4 更新 `contracts/` 中的 service state、query labels、approval、SQL SubAgent result 和错误合同。
- [ ] 13.1.5 对 Skill 的 `allowed-tools` 与 DataAgent/SQL SubAgent allowlist 做自动一致性检查。

## 13.2 仓库文档

- [X] 13.2.1 更新 `docs/guide/used-api.md`，记录 `service_ability`、artifact、状态、工具和确认协议。
- [X] 13.2.2 新增或更新 `docs/agents/data-agent/README.md`，说明配置位置、启动条件、权限边界和故障排查。
- [X] 13.2.3 更新 `backend/AGENTS.md`，记录 service ability 注册、状态快照、SQL 工具和测试入口。
- [X] 13.2.4 更新 `frontend/AGENTS.md`，记录 QueryIntentCard、human-input 所有权、artifact 解析和 E2E 测试入口。
- [X] 13.2.5 更新 README/配置示例时明确 DSN 通过环境变量或 Secret 提供，禁止复制真实连接信息。
- [X] 13.2.6 在实现 review 文档中记录安全边界、已知限制、测试结果和未完成的多数据源/脱敏工作。

## 13.3 代码注释规范

- [X] 13.3.1 新增 Python 函数、基类、实现类和关键 reducer 均使用中文注释，说明作用、参数和返回值。
- [X] 13.3.2 DataAgent 新增代码关键位置统一增加 `# ADD: DataAgent 正式查询闭环新增`，避免与原 DeerFlow 代码混淆。
- [X] 13.3.3 前端新增协议解析器和组件说明其 artifact 来源、状态转换和失败降级行为。

# 14、实施阶段与交付顺序 TODO

## 14.1 第一阶段：配置、状态和动态装配

- [X] 14.1.1 完成 `service_ability` 配置模型和 round-trip 测试。
- [X] 14.1.2 完成 `DataAgentServiceAbility` 注册与默认流程旁路测试。
- [X] 14.1.3 完成 `service_states` 快照封装、LangGraph checkpointer round-trip、API 序列化和恢复测试。
- [X] 14.1.4 完成 DataAgent middleware 动态插入和工具 allowlist。

## 14.2 第二阶段：TableRAG、标签和前端卡片

- [X] 14.2.1 完成 TableRAG retrieval context 登记和 Evidence digest。
- [X] 14.2.2 完成标签快照 artifact、snapshot_id 和数据源绑定。
- [ ] 14.2.3 完成 QueryIntentCard、历史恢复和 custom event 乱序处理。当前仅完成 artifact 解析、卡片渲染和基础分组测试；真实乱序事件与刷新恢复仍待线程级 E2E 验收。
- [ ] 14.2.4 完成标签与前端展示的定向 E2E。

## 14.3 第三阶段：确认闭环

- [X] 14.3.1 完成 `auto`、`on_ambiguity`、`always` 三种策略。
- [X] 14.3.2 完成 execute、sql_only、cancel 和修改条件操作。
- [ ] 14.3.3 完成 stale response、并发输入、断线恢复和旧快照失效测试。

## 14.4 第四阶段：SQL SubAgent

- [X] 14.4.1 完成 SQL SubAgent 输入/输出合同和父子上下文传递。
- [X] 14.4.2 完成 `data_validate_sql` 和 `data_execute_sql` 工具注册及严格 allowlist。
- [X] 14.4.3 完成只读校验、执行预算、错误脱敏和结构化结果回写。
- [X] 14.4.4 完成 SQL、执行状态和表格结果的前端展示。

## 14.5 第五阶段：生产加固

- [ ] 14.5.1 在首版已完成单数据源强绑定的基础上，扩展多数据源选择；不得把首版必需的数据源绑定推迟到本阶段。
- [X] 14.5.2 完成 Schema/Table/Column/Evidence 强校验。
- [ ] 14.5.3 完成敏感列脱敏、审计日志和 SQL 回归评测集。
- [ ] 14.5.4 完成性能、并发、超时、取消和资源回收压测。

# 15、验收标准 TODO

- [X] 15.1 DataAgent 通过正式 `lead_agent + agent_name=data-agent` 路径运行，不新增 Gateway 路由。
- [X] 15.2 `service_ability` 能被正确读取、校验和写回，普通 custom-agent 配置不会丢失未知业务字段。
- [X] 15.3 DataAgent 能完成 TableRAG 检索，并且标签发布前存在当前轮次有效 Evidence。
- [X] 15.4 `publish_query_labels` 生成的 artifact、service state、checkpoint、history 和前端卡片使用同一 `snapshot_id`。
- [X] 15.5 `data_source_id` 出现在标签快照、SQL SubAgent 请求、SQL 校验和 SQL 执行结果中，错配时 fail closed。
- [X] 15.6 `auto` 模式可自动进入 SQL SubAgent；`on_ambiguity` 和 `always` 模式按合同等待确认。
- [X] 15.7 在策略要求人工确认时，用户确认前不会调用 SQL 校验或执行；`auto` 仅在快照完整、无歧义且满足阈值时自动继续；`sql_only` 不执行数据库，`cancel` 不产生 SQL 执行。
- [X] 15.8 用户修改查询条件后，旧标签、旧确认、旧校验和旧执行结果均不可复用。
- [X] 15.9 SQL SubAgent 只能使用显式只读工具，并返回可解析的结构化结果。
- [X] 15.10 SQL 只读校验、超时、行数、结果字符预算、错误脱敏和重复执行限制全部生效。
- [ ] 15.11 页面刷新、断线续连和 checkpoint 恢复后，标签卡、确认状态、SQL 和结果表保持一致。
- [X] 15.12 默认 Agent、普通 custom-agent、bootstrap、Gateway factory 和 DeerFlowClient 定向回归测试通过。
- [ ] 15.13 后端、前端、关键 E2E、格式化、lint、blocking-IO 和 import boundary 检查全部通过。
- [ ] 15.14 所有新增代码、配置、合同、Skill 和文档已按项目规范更新，并完成 review/debugger/test 闭环。

# 16、Git 与发布流程 TODO

- [X] 16.1 当前分支 `feat/data-agent-query-intent-flow` 的 merge-base 为 `dev` 当前提交，二次开发未写入 `main`。
- [X] 16.2 保留现有用户修改，先审查 staged/unstaged diff，禁止用 reset 或 checkout 覆盖用户内容。
- [ ] 16.3 按阶段拆分提交，提交前缀使用 `feat`、`fix`、`test`、`docs` 或 `refactor`。
- [ ] 16.4 每次提交信息使用中文，说明做了什么、为什么做以及验证了什么。
- [X] 16.5 开发完成后执行 review，记录安全边界、兼容性、测试结果和遗留风险。
- [X] 16.6 review 发现 SQL allowlist、旧快照、并行工具调用和 AST 嵌套 DML 问题后已执行 debugger 修复，并重复定向回归、真实数据库 E2E、Ruff 和前端检查。
- [ ] 16.7 全部验收通过后合并回 `dev`，再按仓库规范推送 `origin/dev`。
- [X] 16.8 发布前检查没有生成物、缓存、真实凭据、临时日志或数据库结果进入 Git。

# 17、开发前置确认 TODO

- [X] 17.1 确认 `service_ability` 作为 DataAgent 业务能力配置入口，不再新增 `runtime_profile` 配置字段。
- [X] 17.2 确认 DataAgent 首版继续采用 `lead_agent + agent_name=data-agent` 正式入口。
- [X] 17.3 确认 SQL SubAgent 的父子状态回传、工具 allowlist 和服务能力上下文传递设计。
- [X] 17.4 确认 TableRAG 与 SQL 执行源的 `data_source_id` 绑定方式。
- [X] 17.5 确认数据库方言、DSN/Secret 提供方式和只读账号策略。
- [X] 17.6 确认前端以 ToolMessage artifact 为持久化真相源，custom event 只作为实时增强。
- [X] 17.7 完成本方案用户确认后，才开始修改业务代码；未确认前只允许补充文档和测试计划。

# 18、专业一致性审查与冻结决策

## 18.1 审查结论

整体方向可行：继续复用正式 `lead_agent + agent_name`、custom-agent、现有 middleware、`task` SubAgent、checkpoint、journal、SSE 和 human-input 协议，符合“不新建平行运行链路、不大幅修改 DeerFlow 原流程”的约束。

但本方案在本次专业复审前存在多处会让开发 AI 做出相反实现的 P0 矛盾，不能直接进入编码。下表给出唯一冻结结论；前文如仍有遗漏，以本表和后续合同为准。

| 主题 | 原方案矛盾或不明确点 | 风险 | 冻结决策 |
|---|---|---|---|
| 配置字段 | 同时出现 `service_ability` 和 `data_agent` | 加载、写回和前端类型各实现一套 | 只使用 `service_ability`；不保留 `data_agent` 别名。 |
| 能力识别 | 计划用字典内容匹配，但没有类型和版本 | 多个适配器可能同时匹配，行为依赖遍历顺序 | 固定 `type: data_query`、`version: 1`，严格匹配，未知类型/版本 fail closed。 |
| 配置路径 | 文档把 `backend/.deer-flow/agents` 当固定物理路径 | 绕过用户隔离目录，读取错用户配置 | 只调用 `resolve_agent_dir()` / `load_agent_config(..., user_id=...)`，禁止拼接目录。 |
| 数据库方言 | 初审误把 PostgreSQL TableRAG 索引库视为业务执行库；真实召回表实际位于 MySQL | 强制 fingerprint 相等会让真实环境永远阻塞，直接放开又会允许任意跨库 | 正式 SQL 门禁支持 PostgreSQL/MySQL。默认 `same_physical_target` 仍要求同物理目标；真实环境显式使用 `logical_data_source`，服务端把 PostgreSQL 索引目标、MySQL 执行目标和逻辑 `data_source_id` 共同绑定为不可伪造 digest。 |
| SQL 实现位置 | 当前 `backend/packages/harness/deerflow-dev` 下的 SQL 工具属于实验实现，且绑定 MySQL parser/driver | 直接 import 实验代码会把未稳定 API 和错误方言带入正式 harness | 生产工具必须在正式 harness 的业务能力边界内注册；实验代码只作为行为测试参考，不作为生产 import 依赖。 |
| SQL 工具命名 | 同时出现 `data_validate_sql/data_execute_sql` 与 `data_sql_validate/data_sql_execute` | Skill、配置、工具注册和测试互相找不到 | 固定现有名称 `data_validate_sql`、`data_execute_sql`，不增加旧名兼容层。 |
| SQL SubAgent 职责 | 一处说只校验/执行，另一处又要求生成和修正 SQL | 父 Agent 与子 Agent 重复生成、重复执行 | DataAgent 负责检索、意图、确认和编排；SQL SubAgent 独占 SQL 生成、校验和执行。 |
| SubAgent 工具来源 | 父 Agent 注入的业务工具不会自动出现在 `task` 内部重新构造的工具集合 | 配置写了工具名，但子智能体运行时报“不存在” | 增加一个服务能力工具提供器，由 lead 构建和 SubAgent 构建共同调用；工具仍按能力类型和子智能体 allowlist 过滤，不进入全局 `BUILTIN_TOOLS`。 |
| SubAgent 启用条件 | 只配置 `allowable_subagents`，但 `task` 是否出现还依赖 runtime `subagent_enabled` | 页面未传开关时闭环随机失效 | 先解析能力，再计算 `effective_subagent_enabled`；DataAgent SQL 能力可提供默认值，显式 runtime `false` 作为安全关闭并返回可读错误，普通 Agent 行为不变。 |
| ThreadState reducer | 当前 `dict.fromkeys(existing + new)` 处理 dict 时必然 `TypeError`，且列表会无界增长 | 首次合并即崩溃，checkpoint 越来越大 | `service_states` 每个 `service_name` 只保留一个活动快照；按服务名替换、同 snapshot 幂等，历史写 artifact/journal。 |
| 状态真相源 | 同时把 service state、ToolMessage artifact、custom event 都称为状态来源 | 刷新、乱序或恢复后前后端状态不一致 | 后端授权判断以 checkpoint 中活动 service state 为准；前端持久展示以 ToolMessage artifact 为准；custom event 仅实时增强。三者必须来自同一规范化快照。 |
| Evidence ID | 计划要求真实 Evidence ID，但当前 `EvidenceRetrievalResult` 不返回稳定 ID | AI 会虚构 ID，或只校验任意文本 | 首版由服务端根据 `data_source_id + evidence_type + evidence_content + canonical metadata` 生成 `evidence_ref`；模型只能引用服务端登记的 ref。未来 TableRAG 原生 ID 上线后再升级合同版本。 |
| 确认回复 | 计划要求回复携带 `snapshot_id`，现有 v1 `human_input_response` 没有该字段 | 为一个业务修改通用协议，影响 journal/memory/frontend | 请求 artifact 带 `snapshot_id`；回复保持 v1，通过 `request_id -> pending snapshot_id` 服务端映射校验。 |
| 确认终止 | 当前 `QueryLabelsMiddleware` 只 `Command(update=...)`，不会等待用户 | 模型发布标签后仍可能立即执行 SQL | 只有 `QueryApprovalMiddleware` 负责确认策略；需要确认时写 human-input artifact 并 `Command(..., goto=END)`。 |
| 自动确认 | 验收同时写了 `auto` 自动继续和“用户确认前绝不执行” | 自动模式永远不能通过验收 | “确认前禁止执行”只约束需要人工确认的分支；`auto` 必须满足完整快照、无歧义和阈值才可继续。 |
| 工具暴露 | 当前原型把 `publish_query_labels` 放入全局 `BUILTIN_TOOLS` | 默认 Agent 和普通 custom-agent 都看到业务工具 | 从全局 builtins 移出，由 `DataAgentServiceAbility` 定向提供；SQL 工具同理。 |
| 前端识别 | 现有消息分组只特殊识别 `ask_clarification`，`publish_query_labels` artifact 不会自然成为定制卡片 | 后端有数据但 UI 只显示普通工具步骤 | 增加严格 artifact parser 和 `QueryIntentCard` 分支；通用 HumanInputCard 继续复用，未知版本降级显示。 |
| 数据源绑定 | `data_source_id` 只是字符串，不能单独证明 TableRAG 索引与 SQL 业务源属于同一逻辑数据源 | 标签看似一致但实际执行到错误业务库 | 服务端构造 `DataSourceBinding` 并生成组合 `binding_fingerprint`。`same_physical_target` 模式要求两个目标 fingerprint 相等；`logical_data_source` 仅接受服务端显式配置的异构目标组合，任何 ID、模式或组合 digest 不一致都在连接前 fail closed。 |
| 多数据源阶段 | 原方案把“强绑定”放到第五阶段 | 前四阶段已经可能在错误库执行 | 单数据源强绑定属于首版 P0；第五阶段只扩展多数据源选择。 |

## 18.2 业务边界冻结

为避免“找到用户真实意图”被 AI 理解为无边界推测，首版业务语义固定如下：

1. DataAgent 仍可处理普通对话。只有当前可见用户轮次首次调用只读 TableRAG 检索工具时，才进入 `data_query` 业务流程；普通对话不得生成空标签卡或触发 SQL。
2. 一个可见用户轮次只维护一个活动查询快照。包含多个互不相关问题时必须请求拆分或澄清；允许用单条 `SELECT/WITH` 表达的组合指标仍可作为一个快照。
3. 首版只支持读取和分析，不支持 `INSERT`、`UPDATE`、`DELETE`、DDL、存储过程、事务控制、文件导入导出或任何数据库管理动作。
4. “让用户确认或模型自己执行”解释为 `confirmation_mode` 策略，不解释为新增“选择 LLM 模型”的前端功能。若产品确实需要模型选择器，应另立需求，不混入意图确认合同。
5. `execute`：生成、校验并只读执行；`sql_only`：生成并校验但不连接数据库执行；`cancel`：写入取消终态；自由文本：作为新条件重新检索并产生新快照。
6. TableRAG 无有效结果时进入 `needs_refinement`，可以向用户说明缺少的条件，但不得发布伪造的 database 标签、不得生成可执行 SQL。
7. `source=user` 只能来自当前问题或有效上下文；`source=database` 必须绑定当前 `evidence_ref`；`source=derived` 必须明确是推导而非数据库事实。
8. 对话追问（例如“那去年呢”）必须继承允许的会话语义后创建新 snapshot，旧 approval 和 SQL digest 一律失效。
9. 空结果是成功执行但 `row_count=0`，与校验失败、连接失败、超时和取消分开表示；最终回答不得把空结果描述成数据库故障。
10. 模型输出始终是候选内容，不是授权凭证。数据权限必须由只读数据库账号、Schema/Table/Column allowlist、必要的 RLS/租户策略和服务端校验共同保证。

## 18.3 唯一业务状态机

```text
idle
  -> retrieving
      -> needs_refinement -> failed / retrieving(new snapshot)
      -> labels_published
          -> approved                         # auto 且满足全部条件
          -> awaiting_confirmation -> END     # on_ambiguity / always
                -> approved                   # execute / sql_only
                -> cancelled                  # cancel
                -> retrieving(new snapshot)   # 自由文本修改
  -> sql_generating
  -> sql_validating
      -> failed
      -> sql_ready
          -> succeeded                        # sql_only，不执行数据库
          -> executing -> succeeded / failed  # execute
```

状态迁移规则：

| 当前阶段 | 允许动作 | 必要前置 | 禁止动作 |
|---|---|---|---|
| `idle/retrieving` | TableRAG 只读检索 | 当前可见用户轮次 | 标签确认、SQL 校验、SQL 执行 |
| `needs_refinement` | 请求澄清或结束 | 检索为空/失败的受控原因 | SQL 生成与执行 |
| `labels_published` | 运行确认策略 | 有 retrieval digest、evidence refs、数据源绑定 | 直接绕过策略执行 SQL |
| `awaiting_confirmation` | 接收唯一 pending response | `request_id` 映射当前 snapshot | 普通输入并发、任何 SQL 动作 |
| `approved` | 调用一次 SQL SubAgent | approval 与 snapshot 未过期 | 父 Agent 自行调用 SQL 执行工具 |
| `sql_generating/sql_validating` | 子 Agent 生成和校验 | 输入合同完整 | 与用户交互、重新检索、文件/bash 工具 |
| `sql_ready` | `sql_only` 结束或 `execute` 执行 | validation digest 与 SQL 完全一致 | 未校验 SQL、旧快照 SQL |
| `executing` | 一次受控只读执行 | 当前 snapshot、source、digest 全匹配 | 自动并发执行、修改 SQL |
| 终态 | 父 DataAgent 解释结果 | 结构化结果合同可解析 | 复用终态授权触发新执行 |

任何 tool middleware 都必须校验当前 stage；拒绝时返回可修复但不泄密的 ToolMessage。禁止只依赖 prompt 要求模型自觉遵守状态机。

## 18.4 规范化合同

### 18.4.1 服务能力合同

- `service_ability.type` 固定为 `data_query`，`version` 固定为 `1`。
- `AgentConfig` 只增加一个可扩展 `service_ability` 顶层字段；DataAgent 细节放在独立解析器，不污染通用配置模型。
- `preserve_non_managed_fields()` 必须 round-trip 保留该字段；Agents API 只返回 `type/version/enabled/confirmation_mode` 等脱敏能力摘要。
- `make_lead_agent()` 必须显式传入已解析的 `resolved_user_id` 加载 agent 配置，不能依赖隐式 ContextVar 猜测用户。
- 未配置能力时返回空适配器；类型冲突、版本未知、字段非法、Secret 缺失或数据源不一致时阻止业务能力启用，但默认 Agent 仍按原流程运行。

### 18.4.2 数据源绑定合同

`DataSourceBindingV1` 至少包含：

```text
version, data_source_id, database_type, source_binding_mode,
table_rag_config_ref, retrieval_target_fingerprint,
execution_secret_ref, execution_target_fingerprint, binding_fingerprint,
allowed_schemas, allowed_tables, allowed_columns
```

- fingerprint 只能由服务端解析连接配置后计算，模型和前端不能传入或覆盖。
- fingerprint 不保存密码，日志只显示短 digest；但必须足以区分 host/port/database/schema 或等价目标身份。
- `same_physical_target` 模式要求检索与执行 fingerprint 相同；`logical_data_source` 允许 PostgreSQL 索引与 MySQL 业务源不同，但组合关系、`data_source_id` 和 allowlist 必须全部来自服务端配置。
- TableRAG 检索结果本身要写入同一个 `data_source_id` 和 fingerprint digest；SQL SubAgent 输入、校验和执行逐层复核。
- 仓库中的明文测试 DSN 只能用于本地测试配置，不得复制为生产配置、artifact 或日志。

### 18.4.3 活动快照合同

`ServiceStateSnapshotV1` 至少包含：

```text
service_name="data_query", version=1, turn_id, snapshot_id,
data_source_id, stage, payload, updated_at
```

- `snapshot_id` 由服务端使用 `turn_id + normalized labels + retrieval digest + data_source_id + ability version` 计算；模型字段即使同名也必须忽略或拒绝。
- `payload` 承载 retrieval、labels、approval、SQL validation/execution 的当前投影，ThreadState 顶层不再增加 `data_query_labels` 等平行业务字段。
- reducer 按 `service_name` 替换活动快照；新 snapshot 自动清除旧 approval、SQL、validation 和 execution。
- 同一 snapshot 的状态只允许合法的单向 stage 更新；阶段回退、跨 turn 更新和终态重复执行一律拒绝。

### 18.4.4 Evidence 引用合同

- 当前 TableRAG schema 没有稳定 Evidence ID，首版生成 `evidence_ref = sha256(canonical evidence record)`；canonical record 必须包含 `data_source_id`、类型、内容及稳定 metadata，不能包含 score、时间戳等会导致同一证据漂移的字段。
- Table/Table Column/Value/JoinGraph 也生成各自类型化 ref，统一登记到当前 retrieval registry。
- 标签、SQL 约束只能引用 registry 中的 ref；前端展示的是安全摘要，原始大对象保留在受限 artifact 或服务端引用中。
- 若后续 TableRAG 返回原生主键，必须升级为 v2 合同并提供迁移测试，不能在 v1 中静默改变 ref 算法。

### 18.4.5 状态、消息和事件一致性合同

| 载体 | 职责 | 是否可作为执行授权 |
|---|---|---|
| checkpoint `service_states` | 后端当前活动状态、阶段门禁、pending approval | 是，唯一后端授权源 |
| ToolMessage artifact | 历史恢复和前端持久展示 | 否，只是活动状态的版本化投影 |
| custom stream event | 当前 run 的低延迟 UI 更新 | 否，允许丢失和乱序 |
| `task` ToolMessage | SQL SubAgent 原始返回 | 否，父 middleware 解析、校验并投影后才生效 |

四类载体必须由同一个 canonical snapshot builder 生成并携带相同 `snapshot_id`/digest。发现 artifact、event 或 SubAgent 结果与活动状态不一致时，丢弃非授权载体并 fail closed；不得用前端传回的 artifact 覆盖 checkpoint。

### 18.4.6 确认合同

- 需要确认时，`QueryApprovalMiddleware` 生成 `source=ask_clarification`、`request_id=data-query:<server digest>` 的 v1 human-input request，artifact 额外携带 `snapshot_id` 和标签摘要，然后 `goto=END`。
- 前端仍用现有隐藏 HumanMessage 发送 v1 response；后端验证 `source/request_id/response_kind`，再从活动 state 查出 pending snapshot。
- pending request 每个 snapshot 只能有一个；重复响应返回幂等结果，不得重复启动 SQL SubAgent。
- `on_ambiguity` 的自动条件必须同时满足：`confidence >= min_auto_confidence`、`ambiguities=[]`、retrieval 非空、Evidence/Schema 约束完整、数据源绑定成功。任一字段缺失都转人工确认。
- `non_interactive` / `disable_clarification` 不得沿用通用 clarification 的“自行猜测继续”语义执行数据库；有歧义时安全停止，无歧义且满足 `auto` 全部条件时才可继续。

### 18.4.7 SQL SubAgent 合同

- 父 Agent 传入严格 JSON envelope，不传自由文本拼接的 DSN 或权限声明；服务端注入 `thread_id/parent_run_id/turn_id/snapshot_id/data_source_id/ability_version`。
- SQL SubAgent 不加载 `table-rag-agent` Skill，避免重复检索和绕过父状态；它只消费父级已登记 Evidence。若需要专属提示，使用 `system_prompt` 或后续新增只负责 SQL 的最小 Skill。
- `task` 返回后必须提取唯一 JSON 对象并按合同验证。Markdown、解释文本、缺字段、旧 snapshot、错 data source 或未知 version 均视为失败；最多允许一次“按 schema 重发”修复，禁止无限委派。
- 父 Agent 只汇总已验证结果；不得在子 Agent 失败后自行调用 SQL 工具绕过边界。

## 18.5 最小侵入实现边界

允许的改动：

- 在现有 `AgentConfig` 增加一个通用 `service_ability` 扩展字段及脱敏 API 投影。
- 新增 `agents/service_agent/` 下的能力解析器、DataAgent 状态类型、工具提供器和业务 middleware。
- 在 `make_lead_agent()` 的既有工具与 middleware 锚点调用能力适配器，不复制 graph factory。
- 在现有 ThreadState 增加一个通用 `service_states` 入口并实现正确 reducer。
- 复用 ToolMessage artifact、custom event、human-input、`task`、checkpoint 和 history，只补充版本化业务合同。
- 前端在现有消息解析/分组中增加 parser 和卡片，不新建 DataAgent 聊天运行时。

禁止的改动：

- 禁止新增 DataAgent 专属 Gateway 路由、独立 LangGraph、独立 checkpoint 或独立 SSE 协议。
- 禁止复制 `get_available_tools()`、`build_middlewares()` 或整条 lead-agent 创建流程。
- 禁止把 `publish_query_labels`、SQL 工具永久放进全局 `BUILTIN_TOOLS`。
- 禁止通过 `agent_name == "data-agent"` 作为长期业务判断；agent 名仅用于加载配置。
- 禁止为冲突字段/工具名增加兼容别名；仓库规范要求直接迁移到唯一 API。
- 禁止修改通用 human-input v1 response 只为加入 `snapshot_id`。
- 禁止把完整数据库行、原始 Evidence 大对象、DSN 或 Secret 写入 checkpoint、custom event、日志和前端。

## 18.6 安全、失败与并发规则

1. SQL 校验必须基于 AST：只允许单条 `SELECT/WITH`，拒绝多语句、DML、DDL、`COPY`、`SET`、事务控制、锁表、危险函数、跨库访问和注释绕过。
2. 表、列和 join 必须来自当前 retrieval registry 与 allowlist；“Schema 合法”不足以代表列级授权。
3. 校验输出必须返回规范化 `executable_sql` 和 `validation_digest`；执行工具只接收该 SQL/digest，执行前再次校验 snapshot 和数据源。
4. 数据库连接必须使用只读账号、只读事务和 statement timeout；多租户/敏感数据由数据库 RLS、视图、脱敏列或服务端策略执行，不能依赖模型提示词。
5. TableRAG 初始检索最多一次，必要的定向补充检索最多两次；SQL 生成/校验修复最多三轮；真实 SQL 执行默认只允许一次，不自动重放。
6. 连接失败、校验失败、执行超时、取消、空结果和截断分别使用稳定错误码；错误消息不得回显 SQL 连接细节或堆栈。
7. 同一 thread 同一时间只允许一个活动 data-query snapshot 进入 SQL 阶段；每个 middleware 都复核 `turn_id + snapshot_id`，旧并发 run 只能写入被丢弃的 stale 结果。
8. 用户停止 run 时取消 SubAgent 等待和尚未开始的数据库动作；已经发出的数据库查询依赖 timeout/driver cancel 回收，取消后结果不得升级活动状态。
9. 结果预算在进入 checkpoint/artifact 前执行；超限只保存列名、前 N 行、总数/截断标志和服务端结果引用，不复制完整结果。
10. 日志记录阶段、耗时、行数、digest 和安全错误码，不记录完整结果行、Secret、完整 DSN 或敏感标签值。

## 18.7 开发前必须确认的业务决策 TODO

以下项目属于开发阻塞项。用户确认整份方案时，如果未提出不同选择，则采用每项中的“首版默认”作为冻结值。

- [X] 18.7.1 真实环境纠正：TableRAG 索引后端为 PostgreSQL，业务 SQL 执行源为 MySQL；首版正式支持该 `logical_data_source` 绑定，同时保留 PostgreSQL 同目标部署。
- [X] 18.7.2 确认“选择模型”的业务含义；首版默认：只实现确认策略，不新增 LLM 模型选择器。
- [X] 18.7.3 确认自动执行阈值；首版默认：`min_auto_confidence=0.85`，存在任何 ambiguity 时强制确认。
- [X] 18.7.4 确认数据授权范围；首版默认：单租户、只读账号、显式 Schema/Table/Column allowlist，未配置 allowlist 时禁止 SQL 执行。
- [X] 18.7.5 确认敏感列和结果脱敏清单；首版默认：未完成分类的列不允许进入自动执行结果。
- [X] 18.7.6 确认查询结果产品规则；首版默认：最多 500 行、100000 字符，空结果正常展示，超限显示截断并不自动分页重查。
- [X] 18.7.7 确认 SQL 是否向用户展示；首版默认：标签卡确认阶段不展示候选 SQL，生成后在结果卡展示已校验 SQL。
- [X] 18.7.8 确认图表是否属于本期强验收；首版默认：图表为执行成功后的可选增强，不阻塞 Text2SQL 闭环验收。
- [X] 18.7.9 确认非交互渠道策略；首版默认：有歧义时停止且不执行，无歧义时仅 `auto` 可继续。
- [X] 18.7.10 确认同一问题包含多个独立查询时的产品行为；首版默认：要求用户拆分，不并行执行多条 SQL。

## 18.8 AI 开发执行规则 TODO

- [X] 18.8.1 开发 AI 首先完整阅读本方案、第 18 节、根/后端/前端 `AGENTS.md` 和涉及模块代码；不得只按文件名猜测 API。
- [X] 18.8.2 开发前记录 staged/unstaged diff，保留用户现有修改；当前硬编码 middleware、全局标签工具和错误 reducer 视为待迁移原型，禁止直接扩建或用 reset 覆盖。
- [X] 18.8.3 先提交合同和失败测试，再实现：配置 -> reducer -> 能力装配 -> 检索登记 -> 标签 -> 确认 -> SQL SubAgent -> 前端。
- [X] 18.8.4 每完成一个子步骤立即运行对应定向测试并把 Todo 改为 `[X]`；不得一次实现全部模块后才测试。
- [X] 18.8.5 已补跑默认 lead-agent、Skill 工具策略、custom-agent、bootstrap、Gateway factory、DeerFlowClient 和历史实验 DataAgent 回归；发现 user-scoped 配置 mock 与缓存 key 变化后已修正测试并复测。
- [X] 18.8.6 遇到前文与第 18 节冲突时，以第 18 节为准并同步修正文档；不得自行折中、并存两个字段或新增兼容层。
- [X] 18.8.7 遇到代码中不存在的字段（例如原生 Evidence ID）时，按已冻结合同实现服务端派生，不得让模型虚构，也不得静默跳过校验。
- [X] 18.8.8 所有安全检查默认 fail closed；配置缺失、状态不一致、解析失败、旧快照、未知版本或数据源错配都不得继续 SQL。
- [X] 18.8.9 新增 Python 代码使用中文函数/类注释和 `# ADD: DataAgent 正式查询闭环新增` 标记；不得改写无关上游代码风格。
- [X] 18.8.10 修改公共 API、目录边界或合同后，同步更新 `docs/guide/used-api.md`、计划、review、Skill 和前后端类型。

## 18.9 开发前一致性校验 TODO

- [X] 18.9.1 全文搜索确认不存在作为目标 API 的 `data_agent` 配置字段，只保留背景说明和历史实验路径中的 DataAgent 名称。
- [X] 18.9.2 全文搜索确认 SQL 工具只使用 `data_validate_sql` 和 `data_execute_sql`（旧历史方案文档不作为目标 API）。
- [X] 18.9.3 确认真实首版拓扑：TableRAG 索引/检索配置使用 PostgreSQL，SQL parser/driver/业务执行容器使用 MySQL；二者通过服务端逻辑数据源绑定，不再错误要求统一为同一方言。
- [X] 18.9.4 确认 `publish_query_labels` 和 SQL 工具不会出现在默认 Agent 或普通 custom-agent 工具 schema。
- [X] 18.9.5 确认 `service_states` reducer 对 dict 可用、活动状态有界；真实 checkpoint round-trip 仍由 6.2.1 单独验收。
- [X] 18.9.6 确认 human-input v1 response 不新增 `snapshot_id`，但服务端 stale response 测试覆盖 request 到 snapshot 的绑定。
- [X] 18.9.7 确认 SQL SubAgent 在 `task_tool` 正式装配路径能拿到允许工具、不能拿到禁止工具，并且父状态只接受 schema 校验后的结果；覆盖服务端 allowlist=true/false 两条分支，真实模型/真实 Gateway 线程仍见 19.3。
- [X] 18.9.8 确认单数据源强绑定在首次真实 SQL E2E 前完成，不留到多数据源扩展阶段。
- [ ] 18.9.9 确认每个 P0 失败场景都有“未建立连接/未执行 SQL”的断言，而不只检查错误文案。
- [X] 18.9.10 第 18.7 节完成确认并回写冻结值后，才将文档状态改为“已确认，可进入 TDD 开发”。

# 19、实施记录与剩余验收

## 19.1 本轮已实现

- [X] 保留正式 `lead_agent + agent_name`、Gateway run、checkpoint、SSE 和消息流，仅增加 `service_ability` 适配层，没有新增 DataAgent 专属路由或平行图。
- [X] 完成 PostgreSQL/MySQL 双方言门禁、同目标/逻辑数据源两种绑定、TableRAG registry/ref/digest、意图快照、human-input 确认、SQL SubAgent 严格 envelope、一次性只读执行和结果预算。
- [X] `sql_only` 在工具装配层移除执行入口；父流程只从真实 SQL ToolMessage 重建结果，不信任 SQL SubAgent 最终自由文本中的数据库行。
- [X] 完成 `data_query_labels` / `data_query_sql_result` v1 artifact、跨组件 JSON Schema、前端严格 parser、QueryIntentCard 和 QueryResultCard。
- [X] 从默认 `BUILTIN_TOOLS` 移除 `publish_query_labels`，SQL 工具仅在 approved snapshot 的目标 SQL SubAgent 中动态装配。
- [X] SQL SubAgent 必须同时满足 custom-agent `allowable_subagents` 服务端判定；客户端 `subagent_enabled` 或模型自行填写目标名不能替代授权。
- [X] `service_states` reducer 拒绝阶段回退、旧 snapshot SQL 结果和旧用户轮次写入；单个 AIMessage 内的 TableRAG、标签和 SQL SubAgent 调用均串行化。
- [X] SQL AST 额外阻断无业务表查询、数据库会话信息和 PostgreSQL 可写 CTE 等嵌套 DML/DDL 节点。
- [X] 所有本轮新增 `deerflow` 后端代码关键位置已增加中文 `# ADD: ...` 标记。

## 19.2 已执行验证

- [X] 后端 DataAgent/service ability/query labels/lead-agent/custom-agent/Client/历史实验定向回归：282 项通过，1 个既有 Starlette/httpx 弃用警告。
- [X] fake-model `create_agent` 闭环：TableRAG -> 标签 -> 自动批准 -> SQL SubAgent 合同 -> SQL 结果 -> 最终回答通过。
- [X] 真实数据库 E2E：PostgreSQL TableRAG 索引 `127.0.0.1:55433/text2sql` -> MySQL 业务库 `127.0.0.1:3308/text2sql`，1 项通过。
- [X] harness/app import boundary 1 项通过；compileall 通过；本次差异 30 个 Python 文件 Ruff check/format check 通过。
- [X] changed blocking-IO scanner 报告本次差异未新增候选；此前 blocking-IO 套件其余 33 项通过，4 项为 Windows POSIX 权限断言/本机 SkillScan 隔离基线失败。
- [X] 前端完整单元测试：74 个文件、633 项通过。
- [X] 前端 `pnpm check`（ESLint + TypeScript）通过。
- [X] 三个 `contracts/data_query/*.json` 通过 JSON Schema 结构校验，并用代表性标签、service state、SQL 结果 payload 验证。

## 19.3 未完成且不得误报通过的验收

- [ ] 使用真实 Gateway RunManager 和 `agent_name=data-agent` 做线程级 E2E，包括暂停确认、恢复、停止运行、断线重连和历史刷新。
- [X] 使用真实 PostgreSQL TableRAG 索引（127.0.0.1:55433）+ MySQL 业务源（127.0.0.1:3308）验证逻辑 binding fingerprint、只读事务、行数/字符预算、危险 SQL 阻断和一次性执行；真实测试未使用 Docker 容器内绕过命令。
- [X] 增加真实 LangGraph checkpointer round-trip、阶段回退/旧 snapshot/旧轮次丢弃和同一 AIMessage 并行调用串行化测试。
- [ ] 增加 Gateway 多 run 竞争与停止中的数据库 driver 取消/回收测试。
- [ ] 运行 backend 全量 `make test`、完整 blocking-IO 和 Gateway router 检查。当前仓库 `uv.lock` 存在基线重复键，`uv run` 无法解析；Windows 下 POSIX `0o600` 断言和 SkillScan 规则被本机环境隔离，不能把这些环境问题写成“测试通过”。
- [ ] 完成敏感列分类/脱敏、数据库 RLS/租户策略、审计日志、性能压测、取消中的 driver 回收和多数据源扩展。
- [ ] 运行浏览器 Playwright 关键 E2E，验证真实页面的确认按钮、普通输入禁用、刷新恢复和 SQL 表格渲染。2026-07-16 已按 Playwright 流程尝试启动本地 Gateway/前端，但后台进程启动所需的系统授权被执行额度策略拒绝，未绕过该限制。

## 19.4 2026-07-16 继续执行记录

- [X] 19.4.1 在 `backend` 工作目录重新执行 DataAgent 查询闭环、service ability、状态持久化和标签工具定向回归：66 项通过。
- [X] 19.4.2 重新执行前端 DataAgent artifact、消息分组和意图/结果卡片定向回归：4 个测试文件、43 项通过。
- [X] 19.4.3 补充执行 `task_tool` SQL 子代理工具装配回归：39 项通过，覆盖 allowlist=true/false、approved 快照和 `sql_only` 工具面。
- [X] 19.4.4 补充 P0 SQL 失败门禁回归：危险 SQL、系统信息、可写 CTE、批量语句均在数据库驱动调用前返回 `SQL_DIGEST_MISMATCH`，驱动调用次数为 0。
- [X] 19.4.5 复核 `backend/packages/harness/deerflow/**` 本轮新增/修改文件，关键新增位置均保留中文注释和 `# ADD: ...` 标记；全局工具表仅增加防误注册说明注释。
- [X] 19.4.6 复核 `git diff --check`，无空白错误；`.vscode/launch.json` 仅存在 EOF newline 差异，配置内容未被覆盖。
- [ ] 19.4.7 变更相关后端扩大回归：356 项通过、8 项失败、1 项跳过；失败全部为本机 SkillScan 规则被安全软件隔离导致的既有环境问题，未修改 SkillScan 基线代码，不能记为全量通过。
- [ ] 19.4.8 真实 Gateway/Frontend Docker 线程与浏览器 E2E：2026-07-16 再次尝试启动 `deer-flow-dev` compose 服务时被执行额度策略拒绝，未使用绕过方式；待环境授权恢复后继续。

> 结论：本轮已完成可进入联调的核心生产闭环代码，但第 19.3 节属于发布前必做验收。未完成这些项目之前，不应勾选第 15.11～15.14、16.7 或宣称已满足生产发布条件。
