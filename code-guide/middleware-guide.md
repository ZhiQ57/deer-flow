# DeerFlow Middleware 教程与源码解析

本文面向想读懂 DeerFlow agent 运行链路的开发者，源码范围以
`backend/packages/harness/deerflow/agents/middlewares/` 为主，同时补充两个不在该目录但实际参与中间件链的组件：

- `backend/packages/harness/deerflow/sandbox/middleware.py`
- `deerflow.guardrails.middleware.GuardrailMiddleware`，由配置动态加载

当前 lead agent 的入口:

- `backend/packages/harness/deerflow/agents/lead_agent/agent.py::build_middlewares`
- `backend/packages/harness/deerflow/agents/middlewares/tool_error_handling_middleware.py::build_lead_runtime_middlewares`

SDK 工厂 `create_deerflow_agent` 也能组装一条简化链，位置在：

- `backend/packages/harness/deerflow/agents/factory.py::_assemble_from_features`

## 1. 先理解 LangChain Middleware 生命周期

DeerFlow 的中间件基于 `langchain.agents.middleware.AgentMiddleware`。一个中间件通常不会实现所有 hook，而是选择其中几个：

| Hook | 触发点 | 典型用途 |
| --- | --- | --- |
| `before_agent` / `abefore_agent` | 每次 agent run 开始 | 初始化线程目录、清理本 run 的临时状态 |
| `before_model` / `abefore_model` | 每次模型调用前，写入 state | 注入 hidden message、压缩历史、补全上下文 |
| `wrap_model_call` / `awrap_model_call` | 包住一次模型调用 | 临时改写 request，不一定写回 checkpoint |
| `after_model` / `aafter_model` | 模型返回 AIMessage 后 | 统计 token、限制 tool_calls、强制 stop |
| `wrap_tool_call` / `awrap_tool_call` | 包住一次工具调用 | 审计、拦截、异常转换、结果打标 |
| `after_agent` / `aafter_agent` | run 结束后 | 清理 per-run 状态、异步写 memory |

执行方向有三个要点：

1. `wrap_model_call` 和 `wrap_tool_call` 是“洋葱模型”：列表里越靠前越外层。请求按列表顺序进入，响应按反方向返回。
2. `after_model` 在 LangChain factory 中按注册逆序执行。也就是说最后 append 的 `SafetyFinishReasonMiddleware` 会先看到模型响应。
3. `before_agent`、`before_model` 这类 state hook 可以返回 state update；`wrap_model_call` 更常用于临时改写请求，不必污染持久 state。

## 2. Lead Agent 的实际中间件顺序

Lead agent 的完整链分两段构建。

第一段是运行时基础链，由 `build_lead_runtime_middlewares()` 创建：

| 顺序 | 中间件 | 启用条件 | 主要 hook |
| ---: | --- | --- | --- |
| 1 | `InputSanitizationMiddleware` | 总是 | `wrap_model_call` |
| 2 | `ToolOutputBudgetMiddleware` | 总是，内部按配置决定是否生效 | `wrap_model_call`, `wrap_tool_call` |
| 3 | `ThreadDataMiddleware` | 总是 | `before_agent` |
| 4 | `UploadsMiddleware` | lead agent 总是 | `before_agent` |
| 5 | `SandboxMiddleware` | 总是 | `before_agent`, `after_agent`, `wrap_tool_call` |
| 6 | `DanglingToolCallMiddleware` | lead/subagent 默认开启 | `wrap_model_call` |
| 7 | `LLMErrorHandlingMiddleware` | 总是 | `wrap_model_call` |
| 8 | `GuardrailMiddleware` | `guardrails.enabled` 且配置 provider | `wrap_tool_call` |
| 9 | `SandboxAuditMiddleware` | 总是 | `wrap_tool_call` |
| 10 | `ReadBeforeWriteMiddleware` | `read_before_write.enabled` | `wrap_tool_call` |
| 11 | `ToolProgressMiddleware` | `tool_progress.enabled` | `wrap_tool_call`, `wrap_model_call`, `before_agent` |
| 12 | `ToolErrorHandlingMiddleware` | 总是 | `wrap_tool_call` |

第二段是 lead-only 链，由 `lead_agent/agent.py::build_middlewares()` 继续 append：

| 顺序 | 中间件 | 启用条件 | 主要 hook |
| ---: | --- | --- | --- |
| 13 | `DynamicContextMiddleware` | 总是 | `before_agent` |
| 14 | `SkillActivationMiddleware` | 总是 | `wrap_model_call` |
| 15 | `DurableContextMiddleware` | 总是 | `before_model`, `after_model`, `wrap_model_call` |
| 16 | `DeerFlowSummarizationMiddleware` | `summarization.enabled` | `before_model` |
| 17 | `TodoMiddleware` | `configurable.is_plan_mode=true` | `before_model`, `after_model`, `wrap_model_call`, `before_agent`, `after_agent` |
| 18 | `TokenUsageMiddleware` | `token_usage.enabled` | `after_model` |
| 19 | `TitleMiddleware` | 总是，内部按 `title.enabled` 判断 | `after_model` |
| 20 | `MemoryMiddleware` | 总是，内部按 `memory.enabled` 判断 | `after_agent` |
| 21 | `ViewImageMiddleware` | 当前模型 `supports_vision=true` | `before_model` |
| 22 | `DeferredToolFilterMiddleware` | `tool_search` 有 deferred tools | `wrap_model_call`, `wrap_tool_call` |
| 23 | `SystemMessageCoalescingMiddleware` | 总是 | `wrap_model_call` |
| 24 | `SubagentLimitMiddleware` | `subagent_enabled=true` | `after_model` |
| 25 | `LoopDetectionMiddleware` | `loop_detection.enabled` | `after_model`, `wrap_model_call`, `before_agent`, `after_agent` |
| 26 | `TokenBudgetMiddleware` | `token_budget.enabled` | `before_agent`, `after_model`, `wrap_model_call`, `after_agent` |
| 27 | 用户传入的 `custom_middlewares` | 有传入 | 取决于自定义实现 |
| 28 | `SafetyFinishReasonMiddleware` | `safety_finish_reason.enabled` | `after_model` |
| 29 | `ClarificationMiddleware` | 总是 | `wrap_tool_call` |

源码注释反复强调几个位置约束：

- `ThreadDataMiddleware` 必须在 `SandboxMiddleware` 前面，因为 sandbox 和工具需要线程路径。
- `ToolProgressMiddleware` 必须在 `ToolErrorHandlingMiddleware` 外层，因为它要读取 `ToolErrorHandlingMiddleware` 给工具结果盖上的 `deerflow_tool_meta`。
- `SafetyFinishReasonMiddleware` append 在 `LoopDetectionMiddleware` 后面，是为了利用 `after_model` 逆序执行，让 Safety 先清掉安全截断的 tool calls，再让 LoopDetection 统计清理后的结果。
- `ClarificationMiddleware` 必须最后 append，让它作为最内层工具拦截器专门处理 `ask_clarification`。

## 3. 一次请求大概怎么流动

可以把一次 run 想成下面这条线：

```text
before_agent
  ThreadData / Uploads / Sandbox / DynamicContext / Todo cleanup / Loop cleanup / TokenBudget seed

loop:
  before_model
    DurableContext capture
    Summarization maybe compact
    Todo reminder
    ViewImage injection

  wrap_model_call, outer -> inner
    InputSanitization
    ToolOutputBudget historical truncation
    DanglingToolCall patch
    LLMErrorHandling retry/circuit
    ToolProgress warning injection
    SkillActivation slash skill injection
    DurableContext ephemeral injection
    Todo completion reminder injection
    DeferredToolFilter hides tools
    SystemMessageCoalescing
    LoopDetection warning injection
    TokenBudget warning injection
    actual model

  after_model, reverse append order
    Clarification has no after_model
    SafetyFinishReason
    custom middlewares
    TokenBudget
    LoopDetection
    SubagentLimit
    SystemMessageCoalescing has no after_model
    DeferredToolFilter has no after_model
    ViewImage has no after_model
    Memory has no after_model
    Title
    TokenUsage
    Todo
    Summarization has no after_model
    DurableContext capture delegations

  if AIMessage has tool calls:
    wrap_tool_call, outer -> inner
      ToolOutputBudget
      Sandbox lazy state persistence
      Guardrail
      SandboxAudit
      ReadBeforeWrite
      ToolProgress
      ToolErrorHandling
      DeferredToolFilter
      Clarification
      actual tool

after_agent
  Sandbox release
  Memory queue update
  Todo/Loop/TokenBudget cleanup
```

这不是逐行等价的 LangGraph 图，而是帮助理解 DeerFlow 中间件职责的执行模型。

## 4. 基础运行时中间件源码解析

### 4.1 `InputSanitizationMiddleware`

文件：`input_sanitization_middleware.py`

作用：保护最后一条真实用户消息，避免用户直接注入 DeerFlow 内部结构化标签。

关键行为：

- 只处理最后一条“真实” `HumanMessage`，跳过 summary、hidden system-injected human message。
- 将 `<system>`、`<think>`、`<memory>`、`<system-reminder>` 等保留标签 HTML escape。
- 用 `--- BEGIN USER INPUT ---` / `--- END USER INPUT ---` 包住用户文本。
- 将原始用户文本保存到 `additional_kwargs[ORIGINAL_USER_CONTENT_KEY]`，供 slash skill 等下游逻辑读取。
- 只通过 `wrap_model_call` 临时修改模型请求，不写回 state。

为什么靠前：它是第一个 wrapper，保证后续所有模型请求级中间件看到的都是已消毒内容。

### 4.2 `ToolOutputBudgetMiddleware`

文件：`tool_output_budget_middleware.py`

作用：限制工具输出进入模型上下文的大小。

关键行为：

- `wrap_tool_call`：工具返回过大时，将完整内容落到 `outputs` 文件中，返回预览和文件路径；失败时退化为 head+tail 截断。
- `wrap_model_call`：对历史里的大工具输出做补丁，防止旧消息继续撑爆上下文。
- 支持本地输出目录和远程 sandbox 内路径。

为什么靠前：它要尽早控制工具结果体积，避免后续模型调用被单个工具结果拖垮。

### 4.3 `ThreadDataMiddleware`

文件：`thread_data_middleware.py`

作用：为每个 thread 建立或计算用户隔离的数据目录。

写入 state：

- `thread_data.workspace_path`
- `thread_data.uploads_path`
- `thread_data.outputs_path`

还会给最后一条用户消息补充：

- `run_id`
- `timestamp`
- 默认 `name="user-input"`

为什么在 `Uploads` 和 `Sandbox` 前面：上传文件和沙箱工具都需要这些线程路径。

### 4.4 `UploadsMiddleware`

文件：`uploads_middleware.py`

作用：把当前消息上传文件和历史上传文件注入给模型。

关键行为：

- 从最后一条 `HumanMessage.additional_kwargs.files` 读取当前上传文件。
- 从线程 uploads 目录扫描历史文件。
- 为文档转换出的 `.md` 提取 outline 或 preview。
- 构造 `<uploaded_files>` 块，插入最后一条用户消息前面。
- 返回 `uploaded_files` state update。
- async 路径使用 `run_in_executor`，避免目录扫描阻塞事件循环。

为什么只在 lead agent 默认开启：subagent 不处理用户上传入口，通常继承已准备好的 sandbox/workspace。

### 4.5 `SandboxMiddleware`

文件：`../sandbox/middleware.py`

作用：管理每个 thread 的 sandbox 生命周期，并把 lazy acquire 得到的 `sandbox_id` 写回 state。

关键行为：

- `lazy_init=True` 时，不在 run 开始立刻创建 sandbox，而是在首次 sandbox 工具调用时创建。
- `wrap_tool_call` 比较工具执行前后的 runtime state，如果发现 sandbox 被 lazy 创建，就用 `Command(update={"sandbox": ...})` 持久化。
- `after_agent` 会 release sandbox。

注意：类注释说“sandbox 不在每次 agent call 后释放”，但当前实现的 `after_agent` 会 release state/context 里的 sandbox。读这段时应以实现为准。

### 4.6 `DanglingToolCallMiddleware`

文件：`dangling_tool_call_middleware.py`

作用：修复历史中“AIMessage 有 tool_calls，但缺少对应 ToolMessage”的问题。

典型场景：

- 用户中断 run。
- 请求取消。
- provider 返回 invalid tool call。

关键行为：

- 扫描历史 AIMessage 的 structured/raw/invalid tool calls。
- 找不到对应 ToolMessage 时，在 AIMessage 后立即插入 synthetic error `ToolMessage`。
- 使用 `wrap_model_call`，而不是 `before_model`，因为 ToolMessage 必须紧跟触发它的 AIMessage。

### 4.7 `LLMErrorHandlingMiddleware`

文件：`llm_error_handling_middleware.py`

作用：模型调用异常的 retry、熔断和用户友好降级。

关键行为：

- 识别 transient、busy、quota、auth、generic 等错误。
- transient/busy 按指数退避重试。
- 连续失败后打开 circuit breaker，短时间内 fast fail。
- 最终返回带 `deerflow_error_fallback` metadata 的 `AIMessage`，而不是让 run 崩溃。
- 对 stream chunk timeout 给出“拆小任务”的用户提示。

### 4.8 `GuardrailMiddleware`

文件不在本目录，由 `deerflow.guardrails.middleware` 动态加载。

作用：在工具执行前做策略授权。

启用条件：

- `app_config.guardrails.enabled`
- `app_config.guardrails.provider` 已配置

它位于 `LLMErrorHandling` 后、`SandboxAudit` 前，意味着工具授权在审计和工具执行之前发生。

### 4.9 `SandboxAuditMiddleware`

文件：`sandbox_audit_middleware.py`

作用：对 `bash` 工具调用做安全审计和基础拦截。

关键行为：

- 高风险命令直接 block，例如危险删除、`curl | bash`、写系统目录、fork bomb 等。
- 中风险命令允许执行，但在工具结果后附加 warning，例如 `pip install`、`chmod 777`。
- 所有 bash 调用写结构化审计日志。

它只拦截 `tool_call.name == "bash"`。

### 4.10 `ReadBeforeWriteMiddleware`

文件：`read_before_write_middleware.py`

作用：强制“改已有文件前必须读当前版本”。

关键设计：

- `read_file` 成功后，在 ToolMessage 的 `additional_kwargs.deerflow_read_mark` 里写入 path 和文件 hash。
- `write_file` / `str_replace` 修改已有文件前，比较当前文件 hash 与最近 read mark。
- 不匹配则返回 error `ToolMessage`，并提示模型先 `read_file`。
- 写成功不会刷新 mark，因此连续修改之间必须重新读取。
- 按 `(thread, path)` 加锁，避免同一轮并发写共享旧 mark。
- 无法读取时 fail open，让工具自己报错。

为什么在 `ToolProgress` 和 `ToolErrorHandling` 外层：被拦截的写操作不会真正执行工具，也不应该消耗 ToolProgress 的正常机会；它自己会调用 `normalize_tool_result` 打标。

### 4.11 `ToolProgressMiddleware`

文件：`tool_progress_middleware.py`

作用：基于工具结果质量判断“某个工具是否已经不再产生有效进展”。

关键行为：

- 以 `(thread_id, tool_name)` 维护状态机：`active -> warned -> blocked`。
- 从 `ToolMessage.additional_kwargs.deerflow_tool_meta` 读取结构化结果，不解析自然语言。
- 对 no results、not found、permission、重复内容、rate limit、auth/config/internal 等做不同处理。
- 可恢复问题只 warn，不硬 block；不可恢复 stop 类问题会 block。
- 通过 `wrap_model_call` 在下一次模型调用注入 progress hint。
- 默认豁免 `ask_clarification`、`write_todos`、`present_files`、`task`。

为什么必须在 `ToolErrorHandlingMiddleware` 外层：`ToolErrorHandlingMiddleware` 是 `deerflow_tool_meta` 的生产者，ToolProgress 要在 handler 返回后读取这个 meta。

### 4.12 `ToolErrorHandlingMiddleware`

文件：`tool_error_handling_middleware.py`

作用：工具异常统一转为错误 `ToolMessage`，并给所有工具结果盖结构化元数据。

关键行为：

- 捕获工具异常，生成 `Error: Tool 'x' failed...` 的 `ToolMessage`。
- 对 `task` 工具异常补充 subagent 状态 metadata。
- 调用 `normalize_tool_result`，为 ToolMessage 写入 `deerflow_tool_meta`。
- 对读取 `SKILL.md` 的工具结果写入 `skill_context_entry`，供 DurableContext 捕获。
- `GraphBubbleUp` 不捕获，避免破坏 LangGraph interrupt/resume 控制流。

## 5. Lead-only 中间件源码解析

### 5.1 `DynamicContextMiddleware`

文件：`dynamic_context_middleware.py`

作用：注入当前日期和可选 memory，同时保持主 system prompt 静态，提升 prefix cache 命中。

关键行为：

- 第一次用户消息前插入 hidden `SystemMessage`，内容为 `<system-reminder><current_date>...`。
- memory 内容作为 hidden `HumanMessage`，避免用户影响的 memory 获得 system 权限。
- 跨午夜时，给当前 turn 注入新的日期 reminder。
- 使用 ID-swap 技术替换原用户消息，保持 checkpoint 合理。
- async 路径用 `asyncio.to_thread` 并设置超时，防止 memory/tiktoken 冷启动阻塞事件循环。

### 5.2 `SkillActivationMiddleware`

文件：`skill_activation_middleware.py`

作用：当用户显式输入 `/skill-name task` 时，加载完整 `SKILL.md` 注入当前模型请求。

关键行为：

- 解析最后一条真实用户消息的 slash skill。
- 检查 skill 是否安装、启用、对当前 agent 可用。
- 读取 `SKILL.md`，构造 hidden `<slash_skill_activation>` HumanMessage。
- 如果 skill 不可用，直接返回 `AIMessage` 告诉用户错误。
- 处理技能所需 secrets：只从 request context 提供的 secrets 中绑定，不读宿主环境变量。
- 记录 run journal audit event。

它使用 `wrap_model_call`，因此注入是模型请求级的；slash 激活消息不需要永久写回 state。

### 5.3 `DurableContextMiddleware`

文件：`durable_context_middleware.py`

作用：把“跨 summarization 保留的长期上下文”拆成 state capture 和临时 request injection 两部分。

捕获内容：

- `summary_text`
- `task` delegations ledger
- 模型读过的 skill 文件引用

关键行为：

- `before_model` 捕获已有 delegations 和 skill reads。
- `after_model` 捕获模型刚刚发起的 delegations。
- `wrap_model_call` 临时插入：
  - 一个 SystemMessage：durable context authority contract
  - 一个 hidden HumanMessage：`<durable_context_data>`
- durable data 被当作不可信数据，不允许里面的内容升级成指令。

### 5.4 `DeerFlowSummarizationMiddleware`

文件：`summarization_middleware.py`

作用：在上下文过长时压缩旧消息，并把摘要写入 `summary_text`。

关键行为：

- 继承 LangChain `SummarizationMiddleware`。
- summary LLM 使用 `TAG_NOSTREAM`，避免摘要模型输出被前端误认为主 assistant 消息。
- summarization 前触发 hook，例如 memory flush。
- 生成 `RemoveMessage(REMOVE_ALL_MESSAGES)` 加保留消息，替换 messages。
- 特别保护 `DynamicContextMiddleware` 生成的 hidden reminder 和 ID-swap peers，避免日期/memory 注入结构被压缩坏。

### 5.5 `TodoMiddleware`

文件：`todo_middleware.py`

作用：在 plan mode 下增强 LangChain 的 `TodoListMiddleware`。

关键行为：

- 当 `write_todos` 的原始 tool call 被 summarization 滚出上下文，但 state 里仍有 todos 时，注入 hidden reminder。
- 如果模型想最终回答但 todos 仍未完成，最多两次强制 `jump_to="model"` 让模型继续完成任务。
- completion reminder 通过 `wrap_model_call` 临时注入，不直接写成用户可见消息。
- run 开始/结束清理 per-run reminder 状态。

### 5.6 `TokenUsageMiddleware`

文件：`token_usage_middleware.py`

作用：记录模型 token 使用，并给 AIMessage 加 token attribution。

关键行为：

- `after_model` 读取最新 AIMessage 的 `usage_metadata` 并写日志。
- 把 subagent 的 token usage 回填到触发 `task` 的 AIMessage。
- 根据 `write_todos` 动作生成更精细的 attribution，写入 `additional_kwargs.token_usage_attribution`。

### 5.7 `TitleMiddleware`

文件：`title_middleware.py`

作用：在首次完整对话后生成 thread title。

关键行为：

- 只在没有 title、且满足首轮用户/助手消息条件时触发。
- 如果 `title.model_name` 为空，使用本地 fallback：截断用户首条消息。
- 如果配置了 title model，则异步调用模型生成标题。
- 标题模型调用带 `TAG_NOSTREAM` 和 `middleware:title`，避免污染主消息流与 token 归因。

### 5.8 `MemoryMiddleware`

文件：`memory_middleware.py`

作用：run 结束后把对话排入 memory 更新队列。

关键行为：

- `after_agent` 执行。
- 只保留用户输入和最终 assistant 响应，忽略工具中间消息。
- 检测 correction / reinforcement。
- 捕获 user_id、trace_id、agent_name 后加入 debounce memory queue。
- 不直接修改 state。

### 5.9 `ViewImageMiddleware`

文件：`view_image_middleware.py`

作用：当模型调用 `view_image` 后，把图片 base64 作为 multimodal HumanMessage 注入给下一次模型调用。

关键行为：

- `before_model` 检查上一条 AIMessage 是否有 `view_image` tool calls。
- 确认对应 ToolMessage 都已完成。
- 从 `state.viewed_images` 读取图片 base64 和 mime type。
- 注入 hidden HumanMessage，content blocks 包含 text 和 `image_url`。

启用条件：当前模型配置 `supports_vision=true`。

### 5.10 `DeferredToolFilterMiddleware`

文件：`deferred_tool_filter_middleware.py`

作用：配合 `tool_search`，让 MCP 工具先隐藏，等模型搜索并 promote 后再暴露 schema。

关键行为：

- `wrap_model_call` 从 `request.tools` 中移除还没 promoted 的 deferred tools。
- `wrap_tool_call` 阻止调用未 promoted 的 deferred tool，返回错误 ToolMessage。
- promoted 状态从 `state["promoted"]` 读取，并用 `catalog_hash` 防止旧 checkpoint 暴露已变化的工具。

### 5.11 `SystemMessageCoalescingMiddleware`

文件：`system_message_coalescing_middleware.py`

作用：把多个 SystemMessage 合并成一个 leading system message，兼容严格 provider。

背景：

- vLLM、SGLang、Qwen、Anthropic 等后端可能拒绝非开头 SystemMessage 或多个不连续 SystemMessage。
- DeerFlow 会通过 DynamicContext/DurableContext 注入多个 SystemMessage。

关键行为：

- 在 `wrap_model_call` 中读取 `request.system_message` 和 `request.messages` 里的 SystemMessage。
- 合并为单个 `request.system_message`。
- 从 `request.messages` 移除 SystemMessage。
- 不修改 checkpoint state。

为什么排在后面：它应当在各种中间件都完成 SystemMessage 注入后，临近 provider 调用前做最终规整。

### 5.12 `SubagentLimitMiddleware`

文件：`subagent_limit_middleware.py`

作用：限制单次模型响应里并发 `task` 工具调用数量。

关键行为：

- `after_model` 检查最后 AIMessage。
- 如果 `task` tool calls 超过上限，只保留前 N 个。
- 使用 `clone_ai_message_with_tool_calls` 同步 structured tool_calls 和 raw provider metadata。
- 上限被 clamp 到 `[2, 4]`。

### 5.13 `LoopDetectionMiddleware`

文件：`loop_detection_middleware.py`

作用：检测模型是否反复发出相同或过高频的工具调用。

两层检测：

- hash-based：同一组 tool name + args 重复到阈值。
- frequency-based：同一种工具调用次数过高，即使参数不同。

关键行为：

- 到 warn 阈值时，把 warning 暂存到 per `(thread, run)` 队列。
- 下一次 `wrap_model_call` 注入 hidden-style `HumanMessage(name="loop_warning")`。
- 到 hard limit 时，清空最后 AIMessage 的 tool_calls，强制结束工具循环，让模型输出最终回答。
- run 开始/结束清理 pending warning。

它和 `ToolProgressMiddleware` 分工不同：

- `ToolProgressMiddleware` 看工具结果质量，按单个 tool block。
- `LoopDetectionMiddleware` 看模型调用模式，必要时停止整个 turn 的 tool loop。

### 5.14 `TokenBudgetMiddleware`

文件：`token_budget_middleware.py`

作用：按 run 统计 token 消耗，达到阈值时警告或硬停止。

关键行为：

- `before_agent` 把历史 AIMessage 标记为 seen，避免算入当前 run。
- `after_model` 累加本 run 新增 token，包括 TokenUsage 回填的 subagent token。
- 达到 warning 阈值时，下一次 `wrap_model_call` 注入 budget warning。
- 达到 hard stop 阈值时，清空最后 AIMessage 的 tool_calls，追加 budget exceeded 文本。
- `after_agent` 清理 run 状态。

### 5.15 `SafetyFinishReasonMiddleware`

文件：`safety_finish_reason_middleware.py`

作用：当 provider 因安全原因截断响应但仍返回半截 tool_calls 时，阻止这些工具执行。

关键行为：

- `after_model` 检测最后 AIMessage 的 finish reason / stop reason / safety metadata。
- 只有存在 tool_calls 时才介入。
- 清空 tool_calls，追加用户可见说明。
- 在 `additional_kwargs.safety_termination` 写观测数据。
- 发出 `safety_termination` stream event，并写 RunJournal audit。

为什么 append 在 `LoopDetectionMiddleware` 后：`after_model` 逆序执行，Safety 先清掉截断 tool_calls，LoopDetection 再基于清理后的消息统计，避免误判。

### 5.16 `ClarificationMiddleware`

文件：`clarification_middleware.py`

作用：拦截 `ask_clarification` 工具，把它转成前端可呈现的人类输入请求。

关键行为：

- `wrap_tool_call` 只处理 `tool_call.name == "ask_clarification"`。
- 正常交互模式下返回 `Command(update={"messages": [tool_message]}, goto=END)`，中断当前 run 等待用户回答。
- `disable_clarification` 上下文存在时，不中断，返回 ToolMessage 要求模型自行假设并继续。
- ToolMessage 的 `artifact.human_input` 携带结构化 UI payload。

## 6. 辅助协议模块

这些文件不是独立中间件，但很多中间件依赖它们。

| 文件 | 作用 |
| --- | --- |
| `tool_result_meta.py` | 定义 `deerflow_tool_meta`，把工具结果统一分类为 success/error/partial_success，并给出 recoverable/action/source |
| `tool_call_metadata.py` | 安全 clone AIMessage，并同步 structured `tool_calls`、raw `additional_kwargs.tool_calls`、`function_call` 和 `finish_reason` |
| `skill_context.py` | 从已读 `SKILL.md` 的工具结果中提取 skill 引用，并渲染 active skills reminder |
| `delegation_ledger.py` | 从 `task` tool call 与 ToolMessage 中提取 subagent delegation ledger |
| `safety_termination_detectors.py` | 定义 provider safety termination 检测器协议和默认检测器 |
| `__init__.py` | 包初始化，目前不承载链路逻辑 |

## 7. Subagent 链与 Lead 链的差异

Subagent 使用 `build_subagent_runtime_middlewares()`，不是完整 lead 链。

Subagent 默认包括：

- `InputSanitizationMiddleware`
- `ToolOutputBudgetMiddleware`
- `ThreadDataMiddleware`
- `SandboxMiddleware`
- `DanglingToolCallMiddleware`
- `LLMErrorHandlingMiddleware`
- 可选 `GuardrailMiddleware`
- `SandboxAuditMiddleware`
- 可选 `ReadBeforeWriteMiddleware`
- 可选 `ToolProgressMiddleware`
- `ToolErrorHandlingMiddleware`
- 可选 `ViewImageMiddleware`
- 可选 `DeferredToolFilterMiddleware`
- 可选 `LoopDetectionMiddleware`
- 可选 `SafetyFinishReasonMiddleware`

Subagent 不包含：

- `UploadsMiddleware`
- `DynamicContextMiddleware`
- `SkillActivationMiddleware`
- `DurableContextMiddleware`
- `DeerFlowSummarizationMiddleware`
- `TodoMiddleware`
- `TokenUsageMiddleware`
- `TitleMiddleware`
- `MemoryMiddleware`
- `SystemMessageCoalescingMiddleware`
- `SubagentLimitMiddleware`
- `TokenBudgetMiddleware`
- `ClarificationMiddleware`

这说明 subagent 是更窄的执行环境：重点是工具安全、sandbox、错误处理和循环保护，而不是前端上传、记忆、标题、slash skill 等 lead-facing 能力。

## 8. `create_deerflow_agent` 的 SDK 链

`backend/packages/harness/deerflow/agents/factory.py::_assemble_from_features` 是一条简化 SDK 链。

默认 `RuntimeFeatures()` 下，它大致会启用：

1. `ThreadDataMiddleware`
2. `UploadsMiddleware`
3. `SandboxMiddleware`
4. `DanglingToolCallMiddleware`
5. `ToolErrorHandlingMiddleware`
6. `LoopDetectionMiddleware`
7. `ClarificationMiddleware`

其他能力通过 `RuntimeFeatures` 打开：

- `memory=True` -> `MemoryMiddleware`
- `vision=True` -> `ViewImageMiddleware` + `view_image_tool`
- `subagent=True` -> `SubagentLimitMiddleware` + `task_tool`
- `auto_title=True` -> `TitleMiddleware`
- `token_budget=True` -> `TokenBudgetMiddleware`
- `summarization=<AgentMiddleware>` -> 自定义 summarization middleware
- `guardrail=<AgentMiddleware>` -> 自定义 guardrail middleware

注意：SDK 链不是 lead-agent 链的完整复刻。它没有默认接入 `InputSanitization`、`ToolOutputBudget`、`LLMErrorHandling`、`SandboxAudit`、`ReadBeforeWrite`、`ToolProgress`、`DynamicContext`、`DurableContext`、`SystemMessageCoalescing`、`SafetyFinishReason` 等 lead runtime 能力。

## 9. 新增或调整中间件时的源码准则

### 9.1 先判断你要改的是 state 还是 request

如果信息应该进入 checkpoint，使用：

- `before_agent`
- `before_model`
- `after_model`
- `after_agent`

如果只是给本次模型调用看的临时上下文，优先使用：

- `wrap_model_call`

例如：

- `InputSanitizationMiddleware` 不写 state，因为消毒文本只是模型安全边界。
- `DurableContextMiddleware` 的 durable 数据写 state，但渲染出的 `<durable_context_data>` 只临时进 request。
- `SystemMessageCoalescingMiddleware` 只改 request，不能破坏 checkpoint 中的原始消息结构。

### 9.2 工具拦截要注意外层/内层

工具调用 wrapper 的顺序很敏感：

```text
outer
  SandboxAudit
  ReadBeforeWrite
  ToolProgress
  ToolErrorHandling
  actual tool
inner
```

常见规则：

- 想在工具执行前直接 block：放在外层，例如 `SandboxAudit`、`ReadBeforeWrite`。
- 想观察统一结果元数据：放在 `ToolErrorHandling` 外层，例如 `ToolProgress`。
- 想生产统一错误和元数据：放在内层靠近 actual tool，例如 `ToolErrorHandling`。

### 9.3 不要在 `after_model` 直接插入普通消息来警告模型

如果 AIMessage 带 tool_calls，`after_model` 时工具结果还没出现。此时插入 HumanMessage/SystemMessage 会打断：

```text
AIMessage(tool_calls) -> ToolMessage(...)
```

这个配对顺序，OpenAI-compatible provider 可能直接 400。

所以 `LoopDetectionMiddleware`、`TokenBudgetMiddleware`、`TodoMiddleware` 都采用“先排队，下一次 `wrap_model_call` 再注入”的模式。

### 9.4 清空 tool_calls 要同步 raw metadata

如果你要删除或截断 tool calls，不要只改 `message.tool_calls`。还要处理：

- `additional_kwargs.tool_calls`
- `additional_kwargs.function_call`
- `response_metadata.finish_reason`

现有工具函数：

- `tool_call_metadata.py::clone_ai_message_with_tool_calls`

### 9.5 安全相关中间件要区分 fail-open 和 fail-closed

现有策略不是统一的：

- `ReadBeforeWriteMiddleware` 无法检查文件时 fail open，让底层工具报错。
- `SkillActivationMiddleware` 读取 skill 失败时 fail closed，直接告诉用户 skill 无法安全加载。
- secret binding 注册表读取失败时 fail closed，不注入 secrets。
- `LLMErrorHandlingMiddleware` 对模型异常做降级，不让 run 直接崩。

新增中间件时，应明确失败策略，并写在 docstring 中。

## 10. 推荐的读源码顺序

如果你第一次读 DeerFlow middleware，建议按这个顺序：

1. `tool_error_handling_middleware.py`：先看链是怎么拼出来的。
2. `lead_agent/agent.py::build_middlewares`：看 lead-only append 顺序。
3. `input_sanitization_middleware.py`、`dynamic_context_middleware.py`、`system_message_coalescing_middleware.py`：理解模型请求前的消息改写。
4. `tool_error_handling_middleware.py`、`tool_result_meta.py`、`tool_progress_middleware.py`：理解工具结果协议。
5. `read_before_write_middleware.py`、`sandbox_audit_middleware.py`、`sandbox/middleware.py`：理解 sandbox 和文件安全。
6. `loop_detection_middleware.py`、`token_budget_middleware.py`、`safety_finish_reason_middleware.py`：理解如何停止坏循环。
7. `durable_context_middleware.py`、`summarization_middleware.py`、`delegation_ledger.py`、`skill_context.py`：理解长上下文如何跨压缩保留。

