当前 DataAgent 并不是独立 LangGraph，而是“正式 `lead_agent` + `data_query service_ability`”。自动 SQL 链路也不经过 bash：数据库执行入口是 `data_execute_sql → SqlExecutionService`.

下面按当前代码的实际调用关系绘制.

## 一、请求进入 Gateway

```text
用户在 DataAgent 对话中发送请求
└─ Frontend useStream<AgentThreadState>()
   ├─ assistantId = "lead_agent"
   ├─ context.agent_name = "data-agent"
   ├─ ultra 模式
   │  ├─ thinking_enabled = true
   │  ├─ is_plan_mode = true
   │  ├─ subagent_enabled = true
   │  └─ reasoning_effort = "high"（未显式设置时）
   └─ POST /api/threads/{thread_id}/runs/stream
      │
      ├─ require_permission("runs", "create")
      │  └─ 验证登录用户和 Thread 所有权
      │
      └─ start_run()
         ├─ 校验 model_name
         ├─ 校验 Thread 访问权限
         ├─ RunManager.create_or_reject()
         │  ├─ 检查同一 Thread 的运行冲突
         │  └─ 创建 RunRecord
         ├─ normalize_input()
         │  └─ 转换 HumanMessage 和请求上下文
         ├─ build_run_config()
         │  └─ 合并 model、agent_name、ultra、subagent 等参数
         └─ asyncio.create_task(run_agent())
            └─ Agent 转入 Gateway 后台执行
```

## 二、DataAgent 构建过程

```text
run_agent()
├─ RunManager.set_status(running)
├─ StreamBridge.publish("metadata")
├─ 安装 RuntimeContext
│  ├─ thread_id
│  ├─ run_id
│  ├─ user_id / user_role
│  ├─ agent_name = data-agent
│  ├─ Request Secrets
│  └─ RunJournal / Trace 信息
│
└─ make_lead_agent()
   └─ _make_lead_agent()
      ├─ load_agent_config("data-agent")
      │  ├─ 加载 DataAgent Lead 模型
      │  ├─ 加载 tool_groups
      │  ├─ 加载 allowable_subagents
      │  └─ 加载 service_ability
      │
      ├─ resolve_service_ability()
      │  └─ DataAgentServiceAbility(type="data_query", version=1)
      │
      ├─ 检查 SQL SubAgent 双重授权
      │  ├─ service_ability.sql_subagent_name
      │  └─ agent_config.allowable_subagents 包含 sql-subagent
      │
      ├─ 构造 DataAgent 工具面
      │  ├─ 默认 Lead 工具（受 tool_groups 限制）
      │  ├─ sqlrag_retrieve
      │  ├─ publish_query_labels
      │  ├─ task
      │  └─ tool_search（MCP 延迟加载开启时）
      │
      ├─ 构造 Middleware
      └─ create_agent()
         ├─ DataAgent Lead 模型
         ├─ DataAgent System Prompt / SOUL
         ├─ ThreadState
         └─ Checkpointer / Store
```

这里有一个重要行为：即使前端没有正确传递 `subagent_enabled=true`，只要 DataAgent 的 `allowable_subagents` 明确包含 SQL SubAgent，当前代码也会把有效的 `subagent_enabled` 打开。

## 三、DataAgent Lead Middleware 完整装配

下面是当前正式 Lead Agent 的注册顺序。标记“可选”的部分取决于 `config.yaml` 和模型配置。

```text
DataAgent Lead Middleware
├─ 01. InputSanitizationMiddleware
│      └─ 清理进入模型的消息，阻止伪造框架标签和消息结构
│
├─ 02. ToolOutputBudgetMiddleware
│      └─ 限制工具结果长度，避免超大结果占满上下文
│
├─ 03. ToolResultSanitizationMiddleware
│      └─ 清理 Web/MCP 等外部工具返回的不可信内容
│
├─ 04. ThreadDataMiddleware
│      └─ 加载 thread_id、线程目录和 ThreadData
│
├─ 05. UploadsMiddleware
│      └─ 加载本轮上传文件信息
│
├─ 06. SandboxMiddleware
│      └─ 创建或复用当前 Thread 的 Sandbox
│         注意：它不负责数据库连接
│
├─ 07. DanglingToolCallMiddleware
│      └─ 修补历史中缺少 ToolMessage 的 tool_call
│
├─ 08. LLMErrorHandlingMiddleware
│      └─ 处理模型调用异常和可见错误回退
│
├─ 09. GuardrailMiddleware（Authorization，可选）
│      └─ 按用户、角色和授权属性控制工具执行
│
├─ 10. GuardrailMiddleware（自定义策略，可选）
│      └─ 执行配置中的额外工具策略
│
├─ 11. SandboxAuditMiddleware
│      └─ 记录 Sandbox 工具执行审计信息
│
├─ 12. ReadBeforeWriteMiddleware（可选）
│      └─ 文件未读取时禁止直接覆盖写入
│
├─ 13. ToolProgressMiddleware（可选）
│      └─ 记录工具开始、进度和结束状态
│
├─ 14. ToolErrorHandlingMiddleware
│      └─ 将工具异常转换为标准 ToolMessage
│
├─ 15. DynamicContextMiddleware
│      └─ 向模型注入日期、运行时上下文等动态信息
│
├─ 16. SkillActivationMiddleware
│      └─ 处理用户显式 /skill 激活
│
├─ 17. SkillToolPolicyMiddleware
│      └─ 根据激活的 Skill 收缩工具权限
│
├─ 18. DurableContextMiddleware
│      ├─ 注入摘要、Skill 引用和 SubAgent delegation ledger
│      ├─ 恢复历史 SQL_* 阶段错误为 failed
│      └─ 清理跨 run 遗留的 in_progress 子任务
│
├─ 19. DeerFlowSummarizationMiddleware（可选）
│      └─ 上下文过长时压缩历史消息
│
├─ 20. TodoMiddleware（Pro/Ultra）
│      └─ 提供任务计划和 Todo 状态
│
├─ 21. TokenUsageMiddleware（可选）
│      └─ 统计模型 Token 使用量
│
├─ 22. TitleMiddleware
│      └─ 生成对话标题
│
├─ 23. MemoryMiddleware（按配置）
│      └─ 读取或写入长期记忆
│
├─ 24. ViewImageMiddleware（视觉模型）
│      └─ 向视觉模型注入已查看图片
│
├─ 25. McpRoutingMiddleware（可选）
│      └─ 根据请求自动推荐、提升延迟加载的 MCP 工具
│
├─ 26. DeferredToolFilterMiddleware（可选）
│      └─ 未经 tool_search 提升前隐藏延迟工具 Schema
│
├─ 27. SystemMessageCoalescingMiddleware
│      └─ 合并多个 SystemMessage，兼容 Qwen/vLLM 等模型
│
├─ 28. SubagentLimitMiddleware
│      └─ 限制并发和单次 Run 的 SubAgent 委派数量
│
├─ 29. LoopDetectionMiddleware（可选）
│      └─ 识别重复工具调用和死循环
│
├─ 30. TokenBudgetMiddleware（可选）
│      └─ 达到单 Run Token 上限时终止继续调用
│
├─ 31. Custom / Configured Middleware（可选）
│      └─ 配置文件中的扩展 Middleware
│
├─ 32. DataAgentTurnResetMiddleware
│      └─ 新的可见用户消息建立 stage=idle，旧查询授权立即失效
│
├─ 33. TableRagStageMiddleware
│      └─ 控制 sqlrag_retrieve 阶段、预算和 Retrieval Snapshot
│
├─ 34. QueryLabelsMiddleware
│      └─ 构造查询标签、Evidence 引用、Snapshot 和审核请求
│
├─ 35. QueryApprovalMiddleware
│      └─ 消费前端审核回复；未审核时阻止模型继续运行
│
├─ 36. SqlStageMiddleware
│      └─ 只允许 approved Snapshot 委派 SQL SubAgent
│
├─ 37. TerminalResponseMiddleware
│      └─ 避免模型在工具结束后返回空消息
│
├─ 38. SafetyFinishReasonMiddleware（可选）
│      └─ 模型被安全策略终止时禁止继续执行残缺工具调用
│
└─ 39. ClarificationMiddleware
       └─ 处理普通 Agent 澄清请求
```

说明：这是注册顺序，不代表每个 Hook 都按单一方向执行。`wrap_model_call`、`wrap_tool_call` 中前面的 Middleware 是外层；`after_model` 通常按反向顺序触发。

## 四、DataAgent 的实际 SQL 主链

```text
DataAgent Graph 开始
├─ DataAgentTurnResetMiddleware.before_agent()
│  ├─ 查找最新可见 HumanMessage
│  ├─ 排除审核回复使用的隐藏 HumanMessage
│  └─ service_states[data_query] = stage: idle
│
├─ QueryApprovalMiddleware.before_agent()
│  └─ 如果这是审核后的第二个 Run
│     ├─ 校验 request_id
│     ├─ 校验 snapshot_id
│     ├─ 校验审核项目完整性
│     └─ 写入 approved / cancelled / retrieving
│
└─ DataAgent Lead 模型调用
   │
   ├─ 工具：sqlrag_retrieve
   │  └─ TableRAG MCP
   │     ├─ hybrid-search
   │     ├─ search-evidences
   │     ├─ search-tables
   │     ├─ search-columns
   │     ├─ search-values
   │     └─ expand-join-graph
   │
   ├─ TableRagStageMiddleware
   │  ├─ 同一个 AIMessage 只保留第一次检索
   │  ├─ 限制检索次数
   │  ├─ 验证 TableRAG JSON
   │  ├─ 生成 Evidence/Table/Column/Value ref
   │  ├─ 构建 registry
   │  ├─ 绑定 data_source_id 和 binding_fingerprint
   │  ├─ 生成 retrieval_digest
   │  └─ stage = retrieving / needs_refinement
   │
   ├─ DataAgent Lead 再次推理业务口径
   │
   ├─ 工具：publish_query_labels
   │  └─ QueryLabelsMiddleware 实际接管执行
   │     ├─ 验证 Evidence refs 属于当前 Retrieval
   │     ├─ 构造 intent / labels / ambiguities（不再接收模型自报 confidence）
   │     ├─ 服务端生成 snapshot_id
   │     ├─ 生成 data_query_labels Artifact
   │     └─ 执行确认策略
   │        │
   │        ├─ 自动通过
   │        │  └─ stage = approved
   │        │
   │        ├─ 需要用户确认
   │        │  ├─ stage = awaiting_confirmation
   │        │  ├─ 生成 human_input request
   │        │  └─ Command(goto=END)
   │        │     └─ 当前 Run 正常结束，等待用户审核
   │        │
   │        └─ 非交互环境无法确认
   │           └─ stage = cancelled
   │
   └─ approved 后继续进入 SQL 委派
```

需要审核时，实际上是两个 Run：

```text
Run A：用户问题
└─ TableRAG → 标签 → awaiting_confirmation → END
   └─ 前端显示标签审核卡

Run B：用户提交审核结果
└─ 隐藏 HumanMessage(human_input_response)
   └─ QueryApprovalMiddleware.before_agent()
      └─ stage = approved
         └─ DataAgent Lead 才能继续委派 SQL SubAgent
```

## 五、SQL SubAgent 执行链路

```text
DataAgent Lead
└─ 工具：task(
      subagent_type = "sql-subagent",
      prompt = 模型原始任务
   )
   │
   ├─ SqlStageMiddleware.wrap_tool_call()
   │  ├─ 检查 stage == approved
   │  ├─ 检查当前 visible turn
   │  ├─ 检查 action == execute / sql_only
   │  ├─ 检查 allowable_subagents
   │  ├─ 禁止同一个响应重复委派 SQL SubAgent
   │  └─ 将模型原始 prompt 替换为服务端 JSON Envelope
   │     ├─ snapshot_id
   │     ├─ data_source_id
   │     ├─ intent / labels
   │     ├─ retrieval_digest
   │     ├─ evidence / tables / columns / values
   │     ├─ join_graphs
   │     ├─ database_type
   │     ├─ action
   │     └─ max_execution_attempts
   │
   └─ task_tool()
      ├─ 获取 sql-subagent 配置和独立 SQL 模型
      ├─ 禁止递归 task
      ├─ 继承用户身份、Thread、Run 和授权上下文
      ├─ 动态装配 SQL 工具
      │  ├─ data_validate_sql
      │  └─ data_execute_sql（仅 action=execute）
      │
      ├─ SubagentExecutor.execute_async()
      └─ 发送 task_* Custom Event
         ├─ task_started
         ├─ task_running
         ├─ task_completed
         ├─ task_failed
         ├─ task_cancelled
         └─ task_timed_out
```

SQL SubAgent 自己也会构建一个 Agent：

```text
SQL SubAgent
├─ 独立 SQL 模型
├─ 独立 System Prompt
├─ 不允许继续调用 task
├─ 不允许 ask_clarification
├─ 不允许 bash（按当前 SQL SubAgent 配置）
├─ 不允许文件写入
└─ Middleware
   ├─ InputSanitizationMiddleware
   ├─ ToolOutputBudgetMiddleware
   ├─ ToolResultSanitizationMiddleware
   ├─ ThreadDataMiddleware
   ├─ SandboxMiddleware
   ├─ DanglingToolCallMiddleware
   ├─ LLMErrorHandlingMiddleware
   ├─ GuardrailMiddleware（可选）
   ├─ SandboxAuditMiddleware
   ├─ ReadBeforeWriteMiddleware（可选）
   ├─ ToolProgressMiddleware（可选）
   ├─ ToolErrorHandlingMiddleware
   ├─ ViewImageMiddleware（可选）
   ├─ McpRoutingMiddleware（可选）
   ├─ DeferredToolFilterMiddleware（可选）
   ├─ LoopDetectionMiddleware
   ├─ TokenBudgetMiddleware
   ├─ Configured Extension Middleware（可选）
   ├─ SafetyFinishReasonMiddleware（可选）
   ├─ DurableContextMiddleware
   ├─ DeerFlowSummarizationMiddleware（可选）
   └─ SystemMessageCoalescingMiddleware
```

SQL 模型内部执行闭环：

```text
SQL SubAgent 模型
└─ 生成候选 SQL
   │
   └─ 工具：data_validate_sql(sql)
      └─ SqlExecutionService.validate()
         ├─ SQLGlot 方言解析
         ├─ 只允许单条 SELECT / UNION
         ├─ 拒绝 DDL / DML / 多语句
         ├─ 拒绝锁、危险函数和系统库
         ├─ 校验 Schema
         ├─ 校验当前 Retrieval registry
         ├─ 校验 data source binding
         ├─ 自动收紧 LIMIT
         └─ 返回
            ├─ executable_sql
            ├─ sql_sha256
            └─ validation_digest
               │
               └─ 工具：data_execute_sql(
                    executable_sql,
                    validation_digest
                  )
                  └─ SqlExecutionService.execute()
                     ├─ 再次校验 SQL digest
                     ├─ 再次校验 binding fingerprint
                     ├─ 从 dsn_env 读取数据库 DSN
                     ├─ PyMySQL / psycopg 连接数据库
                     ├─ 开启只读事务
                     ├─ 设置执行超时
                     ├─ 执行 SQL
                     ├─ rollback + close
                     └─ 返回 JSON 安全结果
                        │
                        ├─ 成功
                        │  ├─ columns
                        │  ├─ rows
                        │  ├─ row_count
                        │  ├─ truncated
                        │  └─ duration_ms
                        │
                        └─ 失败
                           ├─ error_code
                           ├─ error_category
                           ├─ error_message
                           ├─ retryable
                           └─ recommended_action
                              │
                              └─ retryable=true
                                 └─ SQL 模型修复 SQL
                                    └─ 必须重新 data_validate_sql
                                       └─ 再次 data_execute_sql
```

这里没有 `bash`：

```text
错误理解：
SQL SubAgent → bash → 数据库

当前真实链路：
SQL SubAgent
  → data_execute_sql
  → SqlExecutionService
  → PyMySQL / psycopg
  → 数据库
```

`SandboxMiddleware` 仍然存在于通用 Agent 基础设施里，但 SQL 数据库连接并不发生在 Sandbox bash 中。

## 六、SQL 结果回到 DataAgent

```text
SubagentExecutor 捕获真实 ToolMessage
├─ data_validate_sql ToolMessage
└─ data_execute_sql ToolMessage
   │
   └─ task_tool 重建 data_query_sql_result
      ├─ 不信任 SQL 模型最终自由文本
      ├─ 只信任真实 SQL 工具输出
      └─ 返回 task ToolMessage
         │
         └─ SqlStageMiddleware._merge_result()
            ├─ 校验 snapshot_id
            ├─ 校验 data_source_id
            ├─ 校验 validation_digest
            ├─ 调用 SqlExecutionService.validate() 再校验
            ├─ 生成 data_query_sql_result Artifact
            └─ service_states
               ├─ succeeded
               └─ failed
                  │
                  └─ DataAgent Lead 再次调用
                     ├─ 读取 SQL、数据库行或原始错误
                     └─ 生成最终用户答案
```

当前持久化状态的主要变化是：

```text
idle
└─ retrieving / needs_refinement
   └─ awaiting_confirmation / approved / cancelled
      └─ succeeded / failed
```

`sql_generating`、`sql_validating`、`sql_ready`、`executing` 已在 reducer 中预留，但当前正式链路主要通过 SubAgent 的 `task_*` 事件展示过程，父 `service_states` 通常在任务返回后直接从 `approved` 投影到 `succeeded/failed`。

## 七、SSE 返回前端

```text
Agent 每产生一个 chunk
└─ run_agent()
   └─ StreamBridge.publish()
      ├─ values
      ├─ messages
      ├─ updates
      └─ custom
         ├─ data_query_labels
         └─ task_started/running/completed/failed
            │
            └─ Gateway SSE
               └─ StreamBridge.subscribe()
                  └─ Frontend useStream 消费
                     ├─ QueryIntentCard / 审核卡
                     ├─ SubtaskCard
                     ├─ QueryResultCard
                     └─ DataAgent 最终回答

Run 完成
└─ RunManager 更新 success/error/interrupted
   ├─ 写入 Checkpoint
   ├─ 写入 RunJournal
   ├─ 持久化 TokenUsage
   ├─ 同步对话标题
   ├─ StreamBridge.publish_end()
   └─ StreamBridge.cleanup(delay=60)
```

## 八、前端“执行 SQL”按钮是独立支线

模型输出 SQL 后，用户手动点击“执行”并不会创建 DataAgent Run，也不会调用 SQL SubAgent：

```text
SQL Markdown CodeBlock
└─ SqlCodeBlock “执行”按钮
   └─ SqlExecutionProvider.execute(sql)
      └─ POST /api/threads/{thread_id}/sql/execute
         ├─ 验证登录用户和 Thread 所有权
         ├─ 按 agent_name 加载 DataAgent 配置
         ├─ SqlExecutionService.validate(source="manual_ui")
         └─ SqlExecutionService.aexecute()
            └─ 返回结果给 SQL Result Panel
               ├─ 成功数据表
               ├─ 数据库原始错误
               ├─ 重新执行
               └─ 选中文本 → 添加到对话
                  └─ 作为引用上下文进入下一次 DataAgent 请求
```

两条执行路径的区别是：

```text
自动执行：
DataAgent → task → SQL SubAgent → data_execute_sql → Executor
结果自动进入 ToolMessage、Checkpoint 和 Lead 上下文

手动执行：
前端按钮 → Gateway SQL API → Executor → Result Panel
结果默认只存在前端；用户点击“添加到对话”后才进入下一次模型上下文
```

关键实现位置：

- Gateway Run：[thread_runs.py](D:/A-PythonWork/AOpenGithub/deer-flow/backend/app/gateway/routers/thread_runs.py:548)、[services.py](D:/A-PythonWork/AOpenGithub/deer-flow/backend/app/gateway/services.py:886)、[worker.py](D:/A-PythonWork/AOpenGithub/deer-flow/backend/packages/harness/deerflow/runtime/runs/worker.py:375)
- Lead 装配：[agent.py](D:/A-PythonWork/AOpenGithub/deer-flow/backend/packages/harness/deerflow/agents/lead_agent/agent.py:523)、[registry.py](D:/A-PythonWork/AOpenGithub/deer-flow/backend/packages/harness/deerflow/agents/service_agent/registry.py:31)
- DataAgent Middleware：[turn_reset_middleware.py](D:/A-PythonWork/AOpenGithub/deer-flow/backend/packages/harness/deerflow/agents/service_agent/turn_reset_middleware.py:39)、[table_rag_middleware.py](D:/A-PythonWork/AOpenGithub/deer-flow/backend/packages/harness/deerflow/agents/service_agent/table_rag_middleware.py:28)、[query_labels_middleware.py](D:/A-PythonWork/AOpenGithub/deer-flow/backend/packages/harness/deerflow/agents/middlewares/query_labels_middleware.py:46)、[approval_middleware.py](D:/A-PythonWork/AOpenGithub/deer-flow/backend/packages/harness/deerflow/agents/service_agent/approval_middleware.py:23)、[sql_stage_middleware.py](D:/A-PythonWork/AOpenGithub/deer-flow/backend/packages/harness/deerflow/agents/service_agent/sql_stage_middleware.py:26)
- SQL 执行：[task_tool.py](D:/A-PythonWork/AOpenGithub/deer-flow/backend/packages/harness/deerflow/tools/builtins/task_tool.py:323)、[sql_tools.py](D:/A-PythonWork/AOpenGithub/deer-flow/backend/packages/harness/deerflow/agents/service_agent/sql_tools.py:33)、[sql_executor.py](D:/A-PythonWork/AOpenGithub/deer-flow/backend/packages/harness/deerflow/agents/service_agent/sql_executor.py:658)
- 手动执行支线：[sql_execution.py](D:/A-PythonWork/AOpenGithub/deer-flow/backend/app/gateway/routers/sql_execution.py:150)、[sql-code-block.tsx](D:/A-PythonWork/AOpenGithub/deer-flow/frontend/src/components/workspace/sql-execution/sql-code-block.tsx:16)、[sql-result-panel.tsx](D:/A-PythonWork/AOpenGithub/deer-flow/frontend/src/components/workspace/sql-execution/sql-result-panel.tsx:72)
