# DataAgent 正式查询闭环改造计划

## 1、目标与范围

- [X] 1.1 明确目标主流程：
  `用户问题 -> TableRAG 检索 -> 查询意图标签 -> 前端专属展示 -> 可选确认 -> SQL SubAgent 校验/执行 -> 结果解释`。
- [X] 1.2 确认正式入口继续复用 `assistantId=lead_agent + context.agent_name=data-agent`，不新增平行 Gateway 运行路由。
- [X] 1.3 确认 `publish_query_labels` 已注册到正式 `BUILTIN_TOOLS`，后续重点转为状态、协议、Profile、前端展示和 SQL SubAgent。
- [X] 1.4 明确 `backend/packages/harness/deerflow-dev/` 属于实验工作，不作为正式实现迁移来源或架构依据。
- [ ] 1.5 在正式 Harness 中新增 DataAgent Runtime Profile，并通过 `agent_config.name == "data-agent"` 解析启用，不在 custom-agent 配置中暴露 `runtime_profile` 字段。
- [ ] 1.6 在 DataAgent custom-agent 配置中新增 SQL RAG 与 SQL 执行参数。
- [ ] 1.7 按 DeerFlow 子智能体规范新增 `sql-subagent`，专门负责 SQL 可行性验证、只读执行和结果结构化返回。
- [ ] 1.8 在前端实现查询标签专属展示、确认操作和 SQL 执行阶段反馈。

## 2、现状结论

### 2.1 正式 DataAgent 入口正确

正式页面通过 `lead_agent` 运行图加载 custom-agent：

```text
Frontend
  -> assistantId=lead_agent
  -> context.agent_name=data-agent
  -> Gateway RunManager / StreamBridge
  -> make_lead_agent()
  -> load_agent_config("data-agent")
```

这条链路可以复用既有线程、checkpoint、journal、SSE、停止运行、token usage 和 tracing，
不需要新增 DataAgent 专属 Gateway 路由。

### 2.2 查询标签工具已进入正式工具集

`publish_query_labels_tool` 工具的关键问题是：

- 标签工具的输出协议还需要升级为稳定快照.
- `QueryLabelsMiddleware` 不应通过 `agent_name == "data-agent"` 直接硬编码追加。
- 正式状态 Schema 仍需要承载 DataAgent 的标签、确认、SQL 阶段状态。
- 前端仍需要把标签消息渲染为专属查询意图卡。

### 2.3 `deerflow-dev` 不进入正式方案

`deerflow-dev` 目录只作为历史实验上下文，不作为正式代码参考、迁移来源或兼容目标。
正式实现应直接在稳定包内按 DeerFlow 现有规范设计：

- `deerflow.agents.*`
- `deerflow.tools.*`
- `deerflow.subagents.*`
- `deerflow.config.*`

后续计划和测试不再依赖 `deerflow-dev` 的状态、工具或 middleware 命名。

### 2.4 TableRAG 检索源已由 `tabelrag.yaml` 明确

当前 `tabelrag.yaml` 已明确：

- `database_type: "pg"`
- `index_database.dsn`
- `source_database.dsn`
- `index_store`
- `retrieval`
- `field_value_sync`

因此 TableRAG 检索数据源以 `tabelrag.yaml` 为准。DataAgent custom-agent 配置不重复保存
TableRAG DSN，只保存是否启用 SQL RAG、执行源引用、执行限制和确认策略。

### 2.5 SQL 执行源应属于 custom-agent 配置

SQL 执行连接、方言、只读约束、超时、行数限制和 Schema 白名单，应在
`backend/.deer-flow/agents/data-agent/config.yaml` 中新增 DataAgent 专属配置。

这类配置属于 DataAgent 的业务执行能力，不应写入 `tabelrag.yaml`，因为 `tabelrag.yaml`
负责 TableRAG 索引和检索，SQL 执行源负责真实查询执行，二者职责不同。

### 2.6 工具过滤由现有工具配置流程承担

本计划不重新设计 DeerFlow 工具过滤系统。DataAgent 仍遵守现有：

- `tool_groups`
- Skill `allowed-tools`
- `get_available_tools(...)`
- subagent 继承父级工具策略
- MCP deferred tool / tool_search 流程

后续只在 Profile 和 SQL SubAgent 中声明需要的工具集合与集成点，具体过滤策略由现有
DeerFlow 工具配置流程继续演进。

## 3、配置方案

### 3.1 custom-agent 配置新增字段

配置文件新增 `data_agent` 字段: 

示例：

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

data_agent:
  enable_sql_rag: true
  table_rag_config: tabelrag.yaml
  data_source_id: text2sql-pg-local
  confirmation_mode: on_ambiguity
  sql_subagent_name: sql-subagent
  sql_execution:
    enabled: true
    database_type: mysql
    dsn_env: DATA_AGENT_SQL_DSN
    readonly: true
    statement_timeout_seconds: 30
    max_rows: 500
    max_result_chars: 100000
```

字段说明：

| 字段 | 作用 |
|---|---|
| `enable_sql_rag` | 是否启用 DataAgent 的 TableRAG -> SQL 闭环。关闭后只做普通 DataAgent 对话和标签展示。 |
| `table_rag_config` | 指向 TableRAG 配置文件，默认可为仓库根目录 `tabelrag.yaml`。 |
| `data_source_id` | 逻辑数据源标识，不保存 DSN。用于把 TableRAG 检索快照、意图标签、SQL 校验和 SQL 执行记录串到同一个数据源上下文。 |
| `confirmation_mode` | `auto`、`on_ambiguity`、`always`。默认建议 `on_ambiguity`。 |
| `sql_subagent_name` | 负责 SQL 校验/执行的子智能体名称。 |
| `sql_execution` | SQL 执行源配置，包含方言、连接引用、只读约束和结果预算。 |

### 3.2 `data_source_id` 的明确边界

`data_source_id` 不是数据库连接字符串，也不是 TableRAG 配置替代品。它的作用是稳定地标记
“本次检索、标签、SQL 和执行结果属于同一份业务数据源”。

它解决三个问题：

- 防错配：避免使用 A 库的 TableRAG Evidence，却去 B 库执行 SQL。
- 可追踪：前端标签卡、后端 checkpoint、SQL SubAgent 返回值都能显示同一个来源 ID。
- 可扩展：未来一个 DataAgent 支持多个业务库时，可以用它选择对应 `tabelrag.yaml` 和
  `sql_execution` 连接。

首个版本如果只有一个数据源，可以把它作为人工可读别名，例如 `text2sql-pg-local`；
如果未配置，则由后端根据 `table_rag_config` 路径和 `sql_execution.database_type`
生成稳定默认值，但计划上建议显式配置。

### 3.3 `AgentConfig` 扩展要求

当前 `load_agent_config()` 会丢弃 `AgentConfig` 未声明字段。因此新增 `data_agent` 后必须同步：

- [ ] 在 `backend/packages/harness/deerflow/config/agents_config.py` 增加 Pydantic 配置模型。
- [ ] 让 `preserve_non_managed_fields()` 保留 `data_agent`，避免 UI 或 `update_agent` 重写配置时丢失。
- [ ] Gateway Agents API 返回 `data_agent` 只读字段，方便前端判断是否展示 DataAgent 专属控件。
- [ ] 如后续允许 UI 编辑 `data_agent`，再单独扩展 PATCH 请求模型；首版可以只支持手写配置。

## 4、Agent Runtime Profile 策略

### 4.1 设计目标

Profile 是正式运行时的内部扩展点，用于消除 `lead_agent/agent.py` 中按固定名称散落的
middleware、state 和 prompt 判断。

约束：

- 不在 custom-agent 配置中新增 `runtime_profile` 字段。
- DataAgent Profile 通过 `agent_config.name == "data-agent"` 解析启用。
- 默认 custom-agent 和默认 lead-agent 继续使用 Default Profile。
- Profile 只负责运行时装配，不替代现有工具过滤流程。

### 4.2 目录建议

```text
backend/packages/harness/deerflow/agents/runtime_profiles/
├── __init__.py
├── base.py
├── registry.py
├── default.py
└── data_agent.py
```

### 4.3 Profile 接口

建议接口：

```python
class AgentRuntimeProfile(Protocol):
    name: str

    def matches(self, *, agent_name: str | None, agent_config: AgentConfig | None) -> bool: ...

    def state_schema(self, *, base_state: type) -> type: ...

    def prepare_tools(self, *, tools: list[BaseTool], agent_config: AgentConfig | None) -> list[BaseTool]: ...

    def extra_middlewares(self, *, config: RunnableConfig, agent_config: AgentConfig | None) -> list[AgentMiddleware]: ...

    def prompt_sections(self, *, agent_config: AgentConfig | None) -> list[str]: ...

    def validate_config(self, *, agent_config: AgentConfig | None) -> None: ...
```

### 4.4 Registry 解析规则

`registry.py` 中集中声明匹配规则：

```python
PROFILES = [
    DataAgentRuntimeProfile(agent_names={"data-agent"}),
    DefaultRuntimeProfile(),
]
```

解析流程：

```text
load_agent_config(agent_name)
  -> resolve_runtime_profile(agent_name, agent_config)
  -> profile.validate_config(...)
  -> profile.state_schema(ThreadState)
  -> get_available_tools(...)
  -> profile.prepare_tools(...)
  -> build_middlewares(...)
  -> profile.extra_middlewares(...)
  -> apply_prompt_template(...)
  -> profile.prompt_sections(...)
```

这样仍然按 `data-agent` 名称启用能力，但硬编码集中在 Profile 注册表中，`lead_agent`
工厂只依赖抽象接口。

### 4.5 DataAgent Profile 职责

DataAgent Profile 首版负责：

- 选择包含 DataAgent 字段的正式状态 Schema。
- 注入 `QueryLabelsMiddleware`。
- 注入 DataAgent 查询阶段 middleware。
- 注入 SQL RAG 配置校验。
- 在 prompt 中加入 DataAgent 查询闭环约束。
- 确认 `enable_sql_rag=true` 时 `sql_subagent_name` 可用。
- 将 `data_agent` 配置写入运行 metadata，便于 journal 和前端追踪。

DataAgent Profile 不负责：

- 重新实现工具过滤系统。
- 替代 `tabelrag.yaml` 的检索配置。
- 直接在主智能体中执行 SQL。
- 复制 Gateway run 生命周期。

## 5、状态与协议

### 5.1 DataAgentState

在稳定包中新增正式状态，不引用 `deerflow-dev`：

```text
backend/packages/harness/deerflow/agents/data_agent/state.py
```

建议状态字段：

- `data_query_stage`
- `data_query_labels`
- `data_query_approval`
- `data_table_rag_context`
- `data_sql_candidate`
- `data_sql_validation`
- `data_sql_execution`
- `data_final_result`

状态设计原则：

- 每个业务轮次只保留当前有效快照。
- 用户修改条件后清理旧标签、旧确认、旧 SQL 校验和旧执行结果。
- checkpoint 恢复后前端能还原标签卡和确认状态。

### 5.2 查询标签快照

`publish_query_labels` 输出应升级为完整快照：

```json
{
  "version": 1,
  "snapshot_id": "sha256:...",
  "data_source_id": "text2sql-pg-local",
  "summary": "查询华东地区销售额最高的前 10 个商品",
  "intent": "ranking",
  "confidence": 0.92,
  "labels": [
    {
      "key": "metric",
      "label": "指标",
      "value": "销售额",
      "normalized": "SUM(order_amount)",
      "source": "database",
      "evidence_refs": ["table:orders", "column:orders.order_amount"]
    }
  ],
  "ambiguities": []
}
```

要求：

- `snapshot_id` 由规范化标签、`data_source_id` 和 TableRAG 检索摘要计算。
- `source=database` 的标签必须引用 TableRAG Evidence。
- 后一次标签快照替换前一次快照。
- SQL SubAgent 只能处理当前有效 `snapshot_id`。

### 5.3 确认模式

支持：

| 模式 | 行为 |
|---|---|
| `auto` | 标签发布后自动进入 SQL SubAgent。 |
| `on_ambiguity` | 无歧义时自动继续；低置信度或存在歧义时等待用户确认。 |
| `always` | 每次标签发布后都等待用户确认。 |

确认动作：

- 确认并执行。
- 仅生成 SQL。
- 修改查询条件。
- 取消查询。

确认结果写入 `data_query_approval`：

```json
{
  "snapshot_id": "sha256:...",
  "status": "approved",
  "action": "execute",
  "source": "user"
}
```

## 6、SQL SubAgent 方案

### 6.1 子智能体定位

新增 `sql-subagent`，只负责 SQL 可行性验证和只读执行，不负责和用户直接对话。

主 DataAgent 职责：

- 理解用户问题。
- 调用 TableRAG 检索。
- 发布查询标签。
- 处理用户确认。
- 把确认后的结构化上下文交给 `sql-subagent`。
- 汇总 SQL SubAgent 的结构化结果并回答用户。

SQL SubAgent 职责：

- 根据意图快照和 TableRAG Evidence 生成候选 SQL。
- 校验 SQL 是否只读、是否符合方言、是否只引用允许 Schema/Table/Column。
- 必要时修正 SQL。
- 使用 custom-agent `data_agent.sql_execution` 指定的执行源只读执行。
- 返回结构化结果、SQL、列信息、行数、截断信息和错误说明。

### 6.2 子智能体配置

按 DeerFlow subagent 配置规范新增：

```yaml
subagents:
  custom_agents:
    sql-subagent:
      description: "验证并执行 DataAgent 生成的只读 SQL"
      model: Qwen3.6-plus
      tools:
        - data_sql_validate
        - data_sql_execute
      skills:
        - table-rag-agent
      timeout_seconds: 60
```

实际字段以当前 `subagents_config` 结构为准。`data-agent` 的 `allowable_subagents`
应包含 `sql-subagent`。

### 6.3 SQL 工具

新增稳定工具模块：

```text
backend/packages/harness/deerflow/tools/data_query/
├── __init__.py
├── config.py
├── validators.py
├── executors.py
└── builtins.py
```

建议工具名：

- `data_sql_validate`
- `data_sql_execute`

校验要求：

- SQL 必须只读。
- SQL 方言必须匹配 `sql_execution.database_type`。
- SQL 必须绑定当前 `snapshot_id`。
- SQL 必须绑定当前 `data_source_id`。
- SQL 引用对象必须来自当前 TableRAG Evidence 或允许补充检索结果。
- 执行工具只接受最近一次有效校验返回的 `executable_sql`。

执行要求：

- DSN 只从 `dsn_env` 或 Secret 引用读取。
- 使用只读账号。
- 开启只读事务。
- 设置 statement timeout。
- 限制最大行数和结果字符数。
- 返回结果中标记是否截断。

### 6.4 SQL SubAgent 返回协议

```json
{
  "version": 1,
  "snapshot_id": "sha256:...",
  "data_source_id": "text2sql-pg-local",
  "status": "success",
  "sql": "SELECT ...",
  "columns": [
    {"name": "product_name", "type": "text"},
    {"name": "sales_amount", "type": "numeric"}
  ],
  "rows": [],
  "row_count": 10,
  "truncated": false,
  "execution_ms": 128,
  "validation": {
    "readonly": true,
    "dialect": "pg",
    "allowed_objects": true
  }
}
```

## 7、后端实施 Todo

### 7.1 配置模型

- [ ] 7.1.1 在 `AgentConfig` 新增 `data_agent` 配置模型。
- [ ] 7.1.2 增加 `DataAgentConfig`、`SqlExecutionConfig` 等 Pydantic 类型。
- [ ] 7.1.3 保证 `update_agent` 和 Agents API 重写配置时不丢失 `data_agent`。
- [ ] 7.1.4 对 `enable_sql_rag=true` 场景校验 `table_rag_config`、`data_source_id`、`sql_execution` 和 `sql_subagent_name`。

### 7.2 Runtime Profile

- [ ] 7.2.1 新增 `agents/runtime_profiles/base.py`。
- [ ] 7.2.2 新增 `agents/runtime_profiles/registry.py`。
- [ ] 7.2.3 新增 `DefaultRuntimeProfile`。
- [ ] 7.2.4 新增 `DataAgentRuntimeProfile(agent_names={"data-agent"})`。
- [ ] 7.2.5 改造 `_make_lead_agent()`，由 Profile 提供 state、middleware、prompt sections 和工具后处理。
- [ ] 7.2.6 移除 `lead_agent/agent.py` 中直接按 `agent_name == "data-agent"` 追加查询标签中间件的逻辑。

### 7.3 DataAgent 状态和中间件

- [ ] 7.3.1 新增正式 `DataAgentState`。
- [ ] 7.3.2 升级 `QueryLabelsMiddleware`，写入完整标签快照。
- [ ] 7.3.3 新增 DataAgent 阶段 middleware，管理检索、标签、确认、SQL SubAgent 和最终回答阶段。
- [ ] 7.3.4 新增确认策略 middleware，支持 `auto/on_ambiguity/always`。
- [ ] 7.3.5 复用现有 `human_input_response` 协议承载确认、修改和取消。
- [ ] 7.3.6 用户修改条件时清理旧状态并重新进入 TableRAG 检索。

### 7.4 SQL SubAgent 和 SQL 工具

- [ ] 7.4.1 新增 `sql-subagent` 配置模板。
- [ ] 7.4.2 新增 `data_sql_validate` 工具。
- [ ] 7.4.3 新增 `data_sql_execute` 工具。
- [ ] 7.4.4 SQL 工具读取 `data_agent.sql_execution`，不读取 `tabelrag.yaml` 中的 DSN 作为执行源。
- [ ] 7.4.5 SQL 校验绑定 `snapshot_id`、`data_source_id` 和 TableRAG Evidence 摘要。
- [ ] 7.4.6 SQL 执行只接受校验工具返回的 `executable_sql`。
- [ ] 7.4.7 SQL SubAgent 返回结构化结果，主 DataAgent 只负责解释和展示。

### 7.5 测试

- [ ] 7.5.1 覆盖 `publish_query_labels` 已在正式工具集中可用。
- [ ] 7.5.2 覆盖 `data-agent` 名称解析到 DataAgent Profile。
- [ ] 7.5.3 覆盖普通 custom-agent 仍解析到 Default Profile。
- [ ] 7.5.4 覆盖 `AgentConfig` 能读取并保留 `data_agent` 字段。
- [ ] 7.5.5 覆盖标签快照、确认、修改条件和状态失效。
- [ ] 7.5.6 覆盖 SQL SubAgent 只接受当前 `snapshot_id`。
- [ ] 7.5.7 覆盖 SQL 执行源来自 custom-agent 配置。
- [ ] 7.5.8 覆盖 SQL 只读、超时、行数和结果字符预算。

## 8、前端实施 Todo

### 8.1 类型与协议

- [ ] 8.1.1 新增查询意图快照 TypeScript 类型。
- [ ] 8.1.2 新增 `data_query_approval` 类型。
- [ ] 8.1.3 新增 SQL SubAgent 结构化结果类型。
- [ ] 8.1.4 增加 ToolMessage artifact / custom event 的运行时解析器。
- [ ] 8.1.5 Agent API 类型返回 `data_agent`，用于识别 DataAgent 专属页面能力。

### 8.2 查询意图卡

- [ ] 8.2.1 新增 `QueryIntentCard`。
- [ ] 8.2.2 展示意图摘要、指标、维度、过滤、时间、排序、Top N 和图表倾向。
- [ ] 8.2.3 展示标签来源：`user/database/derived`。
- [ ] 8.2.4 展示 TableRAG Evidence 摘要。
- [ ] 8.2.5 展示置信度和歧义提示。
- [ ] 8.2.6 支持自动批准、待确认、已确认、已修改、已取消状态。

### 8.3 确认交互

- [ ] 8.3.1 复用现有 human-input 回复通道。
- [ ] 8.3.2 支持确认并执行。
- [ ] 8.3.3 支持仅生成 SQL。
- [ ] 8.3.4 支持修改查询条件。
- [ ] 8.3.5 支持取消查询。
- [ ] 8.3.6 待确认期间避免并发普通输入破坏 `snapshot_id` 绑定。

### 8.4 SQL 结果展示

- [ ] 8.4.1 展示 SQL SubAgent 返回的 SQL。
- [ ] 8.4.2 展示执行状态、耗时、行数和截断状态。
- [ ] 8.4.3 展示结构化表格结果。
- [ ] 8.4.4 支持失败时展示校验失败原因或执行错误摘要。
- [ ] 8.4.5 页面刷新后从历史消息和 checkpoint 恢复标签卡与执行结果。

## 9、Skill 和提示词 Todo

- [ ] 9.1 更新 `skills/public/z_sqltable-rag`，明确先检索、再发布标签、再确认、再委托 SQL SubAgent。
- [ ] 9.2 Skill 中说明 TableRAG Evidence 如何映射到标签 `evidence_refs`。
- [ ] 9.3 Skill 中说明不确定字段、指标、时间范围时如何形成 `ambiguities`。
- [ ] 9.4 DataAgent Profile prompt section 中强调主智能体不直接执行 SQL。
- [ ] 9.5 SQL SubAgent prompt 中强调只读、方言、Schema 白名单、超时和结果预算。

## 10、文档 Todo

- [ ] 10.1 更新 `docs/guide/used-api.md`，记录新增配置、状态和工具协议。
- [ ] 10.2 新增或更新 `docs/agents/data-agent/README.md`。
- [ ] 10.3 更新 `backend/AGENTS.md`，记录 Runtime Profile 与 DataAgent 扩展点。
- [ ] 10.4 更新 `frontend/AGENTS.md`，记录查询意图卡和确认交互所有权。
- [ ] 10.5 新增实现 review，记录最终安全边界、测试结果和遗留风险。

## 11、推荐交付顺序

### 第一阶段：Profile 和标签展示

- [ ] 11.1 扩展 `AgentConfig.data_agent`。
- [ ] 11.2 接入 Runtime Profile 注册表。
- [ ] 11.3 DataAgent 通过名称解析启用 Profile。
- [ ] 11.4 `QueryLabelsMiddleware` 迁入 Profile 装配。
- [ ] 11.5 前端展示 `QueryIntentCard`。

### 第二阶段：确认闭环

- [ ] 11.6 完成标签快照协议。
- [ ] 11.7 完成 `auto/on_ambiguity/always`。
- [ ] 11.8 完成确认、仅生成 SQL、修改条件和取消。
- [ ] 11.9 完成状态失效和历史恢复。

### 第三阶段：SQL SubAgent

- [ ] 11.10 新增 `sql-subagent`。
- [ ] 11.11 新增 `data_sql_validate`。
- [ ] 11.12 新增 `data_sql_execute`。
- [ ] 11.13 主 DataAgent 委托 SQL SubAgent。
- [ ] 11.14 前端展示 SQL、表格结果和执行状态。

### 第四阶段：生产加固

- [ ] 11.15 多数据源选择。
- [ ] 11.16 Schema/Table/Column Evidence 强校验。
- [ ] 11.17 敏感列脱敏。
- [ ] 11.18 SQL 回归评测集。
- [ ] 11.19 审计日志和错误追踪。

## 12、验收标准

- [ ] 12.1 DataAgent 正式页面提问后可以完成 TableRAG 检索。
- [ ] 12.2 `publish_query_labels` 生成的标签快照能进入后端状态和前端标签卡。
- [ ] 12.3 DataAgent Profile 由 `agent_config.name == "data-agent"` 解析启用，普通 custom-agent 不受影响。
- [ ] 12.4 custom-agent `data_agent` 配置可以被读取并在更新配置时保留。
- [ ] 12.5 `data_source_id` 同时出现在标签快照、SQL 校验请求和 SQL SubAgent 返回结果中。
- [ ] 12.6 SQL 执行源来自 custom-agent `data_agent.sql_execution`，不是 `tabelrag.yaml` 的检索 DSN。
- [ ] 12.7 确认模式下，用户确认前不会委托 SQL SubAgent 执行。
- [ ] 12.8 用户修改条件后不会复用旧标签、旧确认、旧 SQL 或旧执行结果。
- [ ] 12.9 SQL SubAgent 只能执行通过校验的只读 SQL。
- [ ] 12.10 页面刷新后可以恢复标签卡、确认状态和 SQL 执行结果。
- [ ] 12.11 后端单元测试、前端单元测试和关键 E2E 通过。
