当前 DataAgent 并不是独立 LangGraph，而是“正式 `lead_agent` + `data_query service_ability`”。
模型/子代理的 SQL 执行入口是外部 `sql-execute` MCP；`service_ability.sql_execution`
仅保留给前端手动 SQL 路由使用，不会把 SQL 工具注入 AgentLoop。

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
├─ 32. QueryLabelsMiddleware
│      └─ 构造查询标签、Evidence 引用、Snapshot 和审批策略
│
├─ 33. QueryIntentApprovalMiddleware
│      └─ 拦截 ask_intent_approval；生成审批卡并把人类结果写回工具历史
│
├─ 34. TerminalResponseMiddleware
│      └─ 避免模型在工具结束后返回空消息
│
├─ 35. SafetyFinishReasonMiddleware（可选）
│      └─ 模型被安全策略终止时禁止继续执行残缺工具调用
│
└─ 36. ClarificationMiddleware
       └─ 处理普通 Agent 澄清请求
```

说明：这是注册顺序，不代表每个 Hook 都按单一方向执行。`wrap_model_call`、`wrap_tool_call` 中前面的 Middleware 是外层；`after_model` 通常按反向顺序触发。

## 四、DataAgent 的实际 SQL 主链

```text
DataAgent Graph 开始
├─ QueryIntentApprovalMiddleware.before_agent()
│  └─ 如果这是审核后的第二个 Run
│     ├─ 校验 request_id
│     ├─ 校验 snapshot / 审核项
│     ├─ 校验审核项目完整性
│     ├─ 用同一 tool_call_id 写回 ask_intent_approval ToolMessage 结果
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
   ├─ DataAgent Lead 直接读取 sqltable-rag MCP 的 ToolMessage
   │  └─ 基于返回的表、字段、业务口径和 Join Graph 继续推理
   │
   ├─ DataAgent Lead 再次推理业务口径
   │
   ├─ 工具：publish_query_labels
   │  └─ QueryLabelsMiddleware 实际接管执行
   │     ├─ 验证 Evidence refs 属于当前 Retrieval
   │     ├─ 构造 intent / labels / ambiguities（不再接收模型自报 confidence）
   │     ├─ 服务端生成 snapshot_id
   │     ├─ 生成 data_query_labels Artifact
   │     ├─ 写入 approval_policy
   │     └─ stage = labels_published
   │
   ├─ 若 approval_policy.required = true
   │  └─ 工具：ask_intent_approval
   │     └─ QueryIntentApprovalMiddleware 实际接管执行
   │        ├─ 生成 human_input request
   │        ├─ ToolMessage.name = ask_intent_approval
   │        ├─ stage = awaiting_confirmation
   │        └─ Command(goto=END)，等待用户审核
   │
   └─ approved 或 approval_policy.required=false 后继续进入 SQL 委派
```

需要审核时，实际上是两个 Run：

```text
Run A：用户问题
└─ TableRAG → publish_query_labels → ask_intent_approval → awaiting_confirmation → END
   └─ 前端将同一 snapshot 的标签与审批请求合并为一张查询意图卡

Run B：用户提交审核结果
└─ 隐藏 HumanMessage(human_input_response)
   └─ QueryIntentApprovalMiddleware.before_agent()
       └─ ask_intent_approval ToolMessage 结果写回历史，stage = approved
          └─ DataAgent Lead 才能继续委派 SQL SubAgent

后续可见用户消息不会被机械视为“必须重新确认的新轮次”。模型可以阅读历史 ToolMessage，
自行判断“重新生成 SQL / 继续执行 / 重新执行”是否仍指向同一查询意图；如果当前持久化快照
已 approved，可直接委派 SQL。如果历史结果是 cancelled、未确认或本轮语义变化，模型可以
重新检索，也可以基于历史检索快照再次调用 `publish_query_labels`，重新展示标签/审批组件。
系统不会根据“重新执行”等关键词写死流程，只在工具调用时校验安全合同。
```

## 五、SQL SubAgent 执行链路

```text
DataAgent Lead
└─ 工具：task(subagent_type = "sql-subagent")
   │
   └─ task_tool()
      ├─ 获取 sql-subagent 配置和独立 SQL 模型
      ├─ 继承用户身份、Thread、Run 和授权上下文
      ├─ 按 `custom_agents.sql-subagent.tools` 只暴露 `sql_execute`
      ├─ SubagentExecutor.execute_async()
      │  └─ sql_execute(SQL 字符串) → 外部 sql-execute MCP
      │     ├─ 返回真实 columns / rows
      │     ├─ 返回行数与截断状态
      │     └─ content 包装当前 SQL 与 SQL 结果摘要
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
└─ 生成 SQL 字符串
   │
   └─ 工具：sql_execute(sql)
      └─ 外部 mcp-extensions/sql-execute
         ├─ MCP Server 负责只读、单语句、Schema 和结果预算边界
         ├─ 执行真实数据库查询
         ├─ 返回 columns / rows / row_count / returned_row_count
         ├─ 返回 truncated、empty 和受控 error_code
         └─ content 包装“当前执行SQL为 / SQL结果为”摘要
```

这里没有 `bash`：

```text
错误理解：
SQL SubAgent → bash → 数据库

当前真实链路：
SQL SubAgent
  → sql_execute
  → 外部 sql-execute MCP
  → PyMySQL / psycopg
  → 数据库
```

`SandboxMiddleware` 仍然存在于通用 Agent 基础设施里，但 SQL 数据库连接并不发生在 Sandbox bash 中。

## 六、SQL 结果回到 DataAgent

```text
SubagentExecutor 捕获真实 ToolMessage
└─ sql_execute ToolMessage
   │
   └─ task_tool 返回原始 MCP 结果给 Lead-Agent
      ├─ 读取真实 SQL、columns 和 rows
      ├─ 读取 row_count / truncated / error_code
      └─ Lead-Agent 根据 content 摘要和结构化结果生成最终答案
```

当前持久化状态的主要变化是：

```text
idle
└─ retrieving / needs_refinement
   └─ awaiting_confirmation / approved / cancelled
      └─ succeeded / failed
```

模型侧 SQL 执行不再依赖 Gateway `service_states` 的 SQL 阶段或
`SqlStageMiddleware`；MCP 结果通过普通 ToolMessage 和 `task_*` 事件返回。

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
DataAgent → task → SQL SubAgent → sql_execute → 外部 sql-execute MCP
结果自动进入 ToolMessage、Checkpoint 和 Lead 上下文

手动执行：
前端按钮 → Gateway SQL API → Executor → Result Panel
结果默认只存在前端；用户点击“添加到对话”后才进入下一次模型上下文
```

关键实现位置：

- Gateway Run：[thread_runs.py](D:/A-PythonWork/AOpenGithub/deer-flow/backend/app/gateway/routers/thread_runs.py:548)、[services.py](D:/A-PythonWork/AOpenGithub/deer-flow/backend/app/gateway/services.py:886)、[worker.py](D:/A-PythonWork/AOpenGithub/deer-flow/backend/packages/harness/deerflow/runtime/runs/worker.py:375)
- Lead 装配：[agent.py](D:/A-PythonWork/AOpenGithub/deer-flow/backend/packages/harness/deerflow/agents/lead_agent/agent.py:523)、[registry.py](D:/A-PythonWork/AOpenGithub/deer-flow/backend/packages/harness/deerflow/agents/service_agent/registry.py:31)
- DataAgent Middleware：[query_labels_middleware.py](D:/A-PythonWork/AOpenGithub/deer-flow/backend/packages/harness/deerflow/agents/middlewares/query_labels_middleware.py:46)、[query_intent_approval_middleware.py](D:/A-PythonWork/AOpenGithub/deer-flow/backend/packages/harness/deerflow/agents/service_agent/data_agent/query_intent_approval_middleware.py:29)
- SQL 执行：[task_tool.py](D:/A-PythonWork/AOpenGithub/deer-flow/backend/packages/harness/deerflow/tools/builtins/task_tool.py:323)、[sql-execute/server.py](D:/A-PythonWork/AOpenGithub/deer-flow/mcp-extensions/sql-execute/server.py:225)
- 手动执行支线：[router.py](D:/A-PythonWork/AOpenGithub/deer-flow/backend/app/gateway/modules/sql_execution/router.py:224)、[sql-code-block.tsx](D:/A-PythonWork/AOpenGithub/deer-flow/frontend/src/components/workspace/sql-execution/sql-code-block.tsx:16)、[sql-result-panel.tsx](D:/A-PythonWork/AOpenGithub/deer-flow/frontend/src/components/workspace/sql-execution/sql-result-panel.tsx:72)
