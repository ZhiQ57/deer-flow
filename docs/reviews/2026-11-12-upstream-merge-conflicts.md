# 官方上游合并冲突清单（等待人工裁决）

## 当前状态

- 原 `dev`：`954cbe39b`，未移动。
- 本地 `main`：已快进至官方 `upstream/main` 的 `13e6fc29e54201ce650b5f48743f88444b4d46f5`，并改为跟踪 `upstream/main`。
- 临时集成分支：`chore/sync-upstream-2026-11-12`；正在合并 `main`，没有创建合并提交、没有推送。
- `git diff --name-only --diff-filter=U` 共 34 个路径：33 个双方修改冲突，1 个本地删除/上游修改冲突。
- 本文中“本地”指原 `dev`，“官方”指本次抓取的 `main`。以下建议均未获用户裁决，不代表已经执行。
- 用户要求先判断舍弃范围，冲突解决已暂停。前端智能体页面、请求携带 `agent_name` 的路线属于明确保留要求。

这次同步跨越 1,531 个上游提交，公共祖先为 `d5ae3882b`。大量冲突来自官方重构旧文件，而二次开发恰好在相同位置增加功能；并不表示这些业务功能被官方废弃。自动合并的文件尚未完成语义验证，不能把消除文本冲突等同于集成完成。

已核实的官方背景：本次官方代码的 [resolve_agent_factory](https://github.com/bytedance/deer-flow/blob/13e6fc29e54201ce650b5f48743f88444b4d46f5/backend/app/gateway/services.py#L1341-L1357) 仍明确使用共用 LeadAgent 加 `agent_name` 选择自定义角色；配置加 SOUL 的模式仍被支持。因此不能仅因冲突删除 DataAgent 或认定这条路线已经废弃。官方 [技能指南](https://github.com/bytedance/deer-flow/blob/13e6fc29e54201ce650b5f48743f88444b4d46f5/docs/guide/skills-and-tools.md#L235-L237) 已把 `allowed-tools` 改为兼容元数据，工具权限应由 Agent 配置、授权和运行时 guardrail 控制；旧技能工具策略需要按这一实际变化处理。

## 1、专项智能体配置与 LeadAgent 装配（3 个文件）

文件：

- [agents_config.py](../../backend/packages/harness/deerflow/config/agents_config.py)
- [lead_agent/agent.py](../../backend/packages/harness/deerflow/agents/lead_agent/agent.py)
- [client.py](../../backend/packages/harness/deerflow/client.py)

**本地：**通过 `service_ability` 加入 DataAgent 工具、业务 middleware 和脱敏运行上下文；使用 `mcp_tools` 按最终工具名过滤；`allowable_subagents` 还参与是否开启子代理的计算。客户端将这些配置计入图缓存。

**官方：**加入智能体独立的 `memory_enabled`、模型参数、`mcp_plugins` 选择、技能与工具授权过滤，以及显式的装配结果、middleware 工具声明验证和交互策略。客户端统一按有效用户加载智能体配置并计算缓存。

**原因：**双方都修改了配置字段、模型/工具装配、middleware 参数及缓存键等相同位置。

**舍弃影响：**直接采用官方整段会丢失本地业务能力装配及工具名白名单；直接保留本地旧段会丢失官方的用户隔离、MCP 安装选择和新授权装配流程。`service_ability` 与 `memory_enabled` 本身可以并存；`mcp_tools` 和 `mcp_plugins` 分别控制工具名和 MCP 安装，不是同名替代字段。

**待裁决：**

- 是否本次先保留现有 `service_ability` 能力，待专项执行方案确定后单独迁移？建议保留，避免同步过程中提前删除 DataAgent 功能。
- 是否将本地 `allowable_subagents` 迁移为官方 `allowed_subagents`？建议明确迁移后删除旧入口，同时重新确定“配置子代理列表是否自动开启 task”的规则，避免增加长期兼容层。本次尚未修改真实智能体配置。
- 是否保留 `mcp_tools` 这层工具名白名单？建议若业务仍依赖它，就与官方 `mcp_plugins` 选择共同生效，不能把安装选择误当作逐工具授权。

**还需验证：**自动合并后的客户端存在重复读取智能体配置、业务上下文写入旧位置、子代理开关再次被原配置覆盖等交叉风险，不能只删除冲突标记。

## 2、MCP 工具发现和 stdio 会话池（1 个文件）

文件：[mcp/tools.py](../../backend/packages/harness/deerflow/mcp/tools.py)。

**本地：**在旧 `get_mcp_tools` 大函数内按服务创建 client、处理 `tool_name_prefix`，并允许无前缀 stdio 工具复用池。

**官方：**将准备 client、发现工具、包装工具及逐服务结果拆开；加入个人 MCP 凭据、binding/ownership domain、审批元数据和过期绑定处理。新版已经按来源服务池化 stdio 工具，并支持发现时关闭工具名前缀。

**原因：**本地修改的旧函数被官方整体拆分，旧函数体已经不能原样放进新版调用结构。

**舍弃影响：**保留旧大块会绕过或破坏新的个人域、binding 和任务管线；舍弃旧实现不应同时删除“无前缀工具”“自定义 cwd/temp”“会话池复用”等实际能力。client constructor 与发现阶段如何应用前缀仍需一起验证。

**建议裁决：**舍弃旧内联发现和按可见名称判断池归属的实现，由官方新管线承接；保留业务所需的前缀、工作目录和池行为，并测试混合前缀、重叠服务名及个人/部署同名服务。

## 3、SQL 子代理动态工具注入（1 个文件）

文件：[task_tool.py](../../backend/packages/harness/deerflow/tools/builtins/task_tool.py)。

**本地：**增加 `SubagentToolContext`、`build_registered_subagent_tools`，支持执行 SQL 子代理时注入其专属工具。

**官方：**同一导入区域增加同步工具适配器，新版委派流程还增加能力及运行边界控制。

**原因：**双方在相邻导入位置插入不同依赖，文本冲突本身不是功能互斥。

**舍弃影响：**删本地入口可能失去专属工具注入；删官方同步适配依赖可能使新版调用链缺失名称。

**建议裁决：**先保留两者；若计划废弃整个本地 SQL 子代理注入路线，应在专项路径重构中连同注册和调用方一起移除。

## 4、内置业务工具导出与装配（2 个文件）

文件：

- [builtins/__init__.py](../../backend/packages/harness/deerflow/tools/builtins/__init__.py)
- [tools/tools.py](../../backend/packages/harness/deerflow/tools/tools.py)

**本地：**导出 `publish_query_labels`、`ask_intent_approval`、`entity_extract`，保留实体抽取工具装配。

**官方：**增加跨会话 artifact 展示、运行状态检查及新的工具装配入口，并调整部分旧导入。

**原因：**双方都扩展相同的 import、`__all__` 和工具列表。

**舍弃影响：**整段选边会丢失对应工具，或留下已使用但未导入的名称。这不是必须删除某个业务工具的冲突。

**建议裁决：**保留双方仍被使用的工具；删除仅因官方重构而失去调用方的旧导入。业务工具是否后续外置，单独决定。

## 5、中断子任务的持久化状态（2 个文件）

文件：

- [durable_context_middleware.py](../../backend/packages/harness/deerflow/agents/middlewares/durable_context_middleware.py)
- [test_durable_context_middleware.py](../../backend/tests/test_durable_context_middleware.py)

**本地：**新 run 出现时，把旧 run 遗留的 `in_progress` 任务修补为 `failed`；还尝试从历史 SQL 错误结果补写终态。

**官方：**根据本轮起始用户消息、既有消息边界以及任务回复归属识别遗留任务；对确实被停止而未产出结果的任务记为 `cancelled`，并避免错误处理恢复运行及重复工具调用 ID。

**原因：**双方修复同一个“旧任务永久进行中”的问题，但使用了不同的归属规则和终态。辅助函数签名也已变化，旧代码不能直接拼接。

**舍弃影响：**直接保留本地宽泛修补会与官方的取消/恢复规则冲突；直接删除本地历史 SQL 处理需确认旧会话中的 `SQL_*` 错误仍能恢复为失败。`failed` 与 `cancelled` 的断言不能同时用于同一个停止场景。

**建议裁决：**采用官方的运行归属和 `cancelled` 语义，舍弃“所有旧 run 未完成任务一律 failed”的旧补丁；保留或迁移历史 SQL 错误回归。此项需要明确状态语义。

## 6、Gateway SQL 服务与新版启动生命周期（3 个文件）

文件：

- [gateway/app.py](../../backend/app/gateway/app.py)
- [gateway/services.py](../../backend/app/gateway/services.py)
- [test_gateway_services.py](../../backend/tests/test_gateway_services.py)

**本地：**手动 SQL HTTP router；SQL Run 上下文的 prepare/register/release；运行成功、失败、取消时释放绑定。

**官方：**新增健康检查依赖、知识范围准入、工具审批恢复/回滚、producer lease 转移，以及覆盖取消等 `BaseException` 的任务挂载清理。

**原因：**导入相邻冲突，以及双方都修改了“准入成功 → 创建 worker task → 挂载或失败清理”的同一临界区；测试也在同一位置追加不同场景。

**舍弃影响：**丢本地会影响前端手动 SQL 入口和 SQL Run 绑定；丢官方会丢失新审批、租约及取消清理逻辑。仅删某方导入会使自动合并的后文出现未定义名称。

**建议裁决：**保留官方生命周期骨架，将本地 SQL 注册/释放接入同一生命周期；两套回归都保留。是否保留手动 SQL 执行本身可由用户决定，不能仅通过选一段 imports 隐式删除。

## 7、人工输入协议与审批历史（2 个文件）

文件：

- [agents/human_input.py](../../backend/packages/harness/deerflow/agents/human_input.py)
- [runtime/journal.py](../../backend/packages/harness/deerflow/runtime/journal.py)

**本地：**回复类型包含 `flow_id`；隐藏的 `ask_intent_approval` 回复允许持久化，以恢复审批历史。旧 journal 还维护按工具名列出的 reconciliation 名单。

**官方：**新增待回复人工输入提取；journal 增加观测/写入常量和辅助函数，并用通用的本轮可见工具结果 reconciliation 替代旧固定名单。

**原因：**导入、模块常量同位置扩展，同时底层 reconciliation 实现被替换。

**舍弃影响：**丢本地 `flow_id` 和业务回复来源，会影响审批匹配与刷新后的已回答状态；丢官方整段会使后文依赖的观测/清理函数缺失。保留已无调用方的旧名单也不能恢复功能。

**建议裁决：**保留 `flow_id` 和 `ask_intent_approval` 隐藏回复持久化；采用官方通用 reconciliation，删除旧固定工具名常量。

## 8、智能体聊天页、输入区与右侧面板（3 个文件）

文件：

- [智能体聊天页](../../frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx)
- [input-box.tsx](../../frontend/src/components/workspace/input-box.tsx)
- [chat-box.tsx](../../frontend/src/components/workspace/chats/chat-box.tsx)

**本地：**DataAgent 审批模式展示、`agent_name` 接线、`SqlExecutionProvider` 和 SQL 结果面板。

**官方：**知识范围控件、模型/工具审批及目标状态接线；消息/工具详情 Provider 和 Details 面板；统一的 `InputMode` 类型。

**原因：**双方在同一页面状态、组件 props、Provider 包装和右面板选择区域插入新功能。

**舍弃影响：**删本地会丢 DataAgent SQL 页面能力；删官方会丢知识范围和消息详情等新增功能。智能体页面与 `agent_name` 按用户要求必须保留。

**待裁决：**

- SQL 与 Details 面板是否都保留？建议保留，在同一可调整宽度面板中互斥切换，最后一次用户选择优先。
- DataAgent 审批模式菜单当前只是本地展示，占位状态没有后端持久化能力。是否继续展示？可以保留明确的展示状态，或删除该占位控件，避免误解为已生效配置。

## 9、DataAgent 消息卡与官方卡片/附件渲染（3 个文件）

文件：

- [human-input-card.tsx](../../frontend/src/components/workspace/messages/human-input-card.tsx)
- [message-list.tsx](../../frontend/src/components/workspace/messages/message-list.tsx)
- [messages/utils.ts](../../frontend/src/core/messages/utils.ts)

**本地：**多问题意图审批、答案回填、`intent_approval_answers`、查询标签/审批/结果分组、内部业务载荷隐藏，以及 task 结果回归原子任务分组。

**官方：**工具审批参数摘要、会话附件与 Markdown 链接上下文；定时任务卡片、来源提示和同轮去重。

**原因：**双方都扩展统一消息联合类型、分组条件和卡片 JSX，而不是使用各自独立的聊天运行时。

**舍弃影响：**选官方整段会丢 DataAgent 专用卡片与结果归属；选本地整段会丢官方定时任务卡、附件链接与工具审批展示。

**建议裁决：**保留两边卡片和分组能力；沿用共享消息流。

**另有需要澄清的既有语义：**自动合并的 `core/messages/human-input.ts` 同时保留 `request_id + flow_id` 精确匹配，以及普通可见用户消息关闭最近未回答卡片的 fallback。后者在原 dev 已存在，不能说是这次官方新增。旧前端指南中“有卡片禁用输入”和“允许普通输入绕过”两段相互矛盾。DataAgent 审批是否必须通过结构化卡片回复，需要单独裁决，不能趁合并悄悄改变。

## 10、SQL 子任务结果与新版引用报告（1 个文件）

文件：[subtask-card.tsx](../../frontend/src/components/workspace/messages/subtask-card.tsx)。

**本地：**`sanitizeSubtaskDisplayText` 清理 SQL 子代理内部载荷和错误。

**官方：**`resolveRenderedSubtask` 合并实时/历史终态；报告使用已验证的知识引用和 Markdown 链接。

**原因：**双方修改了同一结果展示代码和导入。

**舍弃影响：**丢本地可能直接显示内部业务载荷；丢官方会退化历史终态与引用展示。

**建议裁决：**保留官方结果归并和报告渲染，在渲染前继续清理业务载荷。

## 11、本地化命名空间（3 个文件）

文件：

- [locales/types.ts](../../frontend/src/core/i18n/locales/types.ts)
- [locales/en-US.ts](../../frontend/src/core/i18n/locales/en-US.ts)
- [locales/zh-CN.ts](../../frontend/src/core/i18n/locales/zh-CN.ts)

**本地：**`sqlExecution` 类型和中英文文案。

**官方：**`browserStream`、`skillUsage` 类型和文案。

**原因：**在字典同一位置增加不同命名空间，无业务含义冲突。

**舍弃影响：**删除任一侧会缺失对应 UI 文案和类型。

**建议裁决：**全部并列保留。

## 12、SQL Markdown 与知识引用测试（1 个文件）

文件：[markdown-content.test.ts](../../frontend/tests/unit/components/workspace/messages/markdown-content.test.ts)。

**本地：**SQL 执行 Provider 与 i18n 测试包装；完整/流式 SQL 代码块及执行可用性测试。

**官方：**新的 i18n 测试注入及知识引用、缺失/恶意引用链接的整条 Markdown 渲染回归。

**原因：**双方在同一测试导入和辅助方法区域追加不同测试。

**建议裁决：**两套测试都保留，统一测试包装；没有应通过选边删除的功能。

## 13、LeadAgent 模型/权限测试脚手架（1 个文件）

文件：[test_lead_agent_model_resolution.py](../../backend/tests/test_lead_agent_model_resolution.py)。

**本地：**增加 DataAgent 装配测试并导入 `tag_mcp_tool`；相邻区域还保留旧技能工具策略测试脚手架。

**官方：**模型和装配测试已演进，并删除旧技能策略脚手架区域。当前官方将 skill 的 `allowed-tools` 作为兼容元数据，实际工具范围由 Agent 配置与授权控制。

**原因：**本地在官方删除/整理的同一导入和辅助类区域追加依赖。

**舍弃影响：**整段删除会连带丢失 DataAgent 测试依赖；整段保留旧脚手架则可能留下已移除的依赖和不再匹配新策略的断言。

**建议裁决：**保留 DataAgent 有效回归，采用官方现有测试结构，删除不再被使用的旧策略脚手架。不要通过测试文件重新引入旧运行时权限机制。

## 14、SkillScan 本机替代实现与官方测试（2 个文件）

文件：

- [test_skill_review_core.py](../../backend/tests/test_skill_review_core.py)
- [test_skillscan_native.py](../../backend/tests/test_skillscan_native.py)

**本地：**因终端安全软件隔离原 scanner，在 `skillscan/__init__.py` 放入保守替代实现；扫描启用时对安装/写入统一阻断。增加包入口 fallback 测试。`test_skillscan_native.py` 在本地提交 `bde657326` 中被删除。

**官方：**继续维护完整原生扫描测试；review 测试改为只扫描 Git 跟踪的 skill 文件，避免 `__pycache__` 等本机构建残留干扰，并增加资源引用解析测试。

**原因：**一个文件是本地删除/官方继续修改；另一个是包入口 fallback 测试与官方测试重构在同一位置交叉。

**舍弃影响：**恢复官方测试而继续保留“扫描全部阻断”的本地包入口，会产生行为不一致；继续删官方测试会失去扫描能力的回归覆盖。该包入口是自动合并保留下来的相关代码，本身没有列入 34 个文本冲突，但必须一起裁决。

**待裁决：**恢复官方扫描器和测试，还是暂时保留本机统一阻断的替代实现？建议恢复官方方案；需先确认安全软件隔离问题已解决，不能把它当成已解决。两套语义不能原样同时保留。

## 15、Docker Gateway 关闭热重载与优雅退出（1 个文件）

文件：[dev-entrypoint.sh](../../docker/dev-entrypoint.sh)。

**本地：**`DEER_FLOW_GATEWAY_RELOAD=0` 时走无热重载的 uvicorn 分支，用于源码映射部署；另保留 Docker 日志输出选择。

**官方：**增加 `--timeout-graceful-shutdown 10`，约束 SSE 连接对停止过程的拖延。

**原因：**本地在启动命令前增加条件分支，官方在同一启动区加入退出配置。

**舍弃影响：**删本地会丢关闭 reload 的部署能力；只保留本地分支则无 reload 路线缺少官方退出超时参数。

**建议裁决：**两者都保留，并确保两个启动分支都带退出超时。无需舍弃功能。

## 16、指南与 README 结构调整（5 个文件）

文件：

- [根 AGENTS.md](../../AGENTS.md)
- [README.md](../../README.md)
- [backend/AGENTS.md](../../backend/AGENTS.md)
- [frontend/AGENTS.md](../../frontend/AGENTS.md)
- [mcp/AGENTS.md](../../backend/packages/harness/deerflow/mcp/AGENTS.md)

**本地：**中文开发/Git 规定；DataAgent、SQL、VSSAG/VFSSQL 约束；Windows 三容器和 Linux 源码映射部署说明。

**官方：**把大型指南拆到最近模块的 `AGENTS.md`；README 改为索引，详细内容迁到主题指南，并增加扩展优先等开发规定。

**原因：**官方整体移动/重写长段内容，本地仍在旧结构中追加说明，因此冲突看起来很大。

**舍弃影响：**全选官方会丢项目特有规则与接入说明；全选本地会回退官方文档重构，保留大量过时重复内容，部分链接也需要重新校验。

**建议裁决：**采用官方组织结构；保留本地中文开发/Git 规则和专项功能约束，将细节放到正确主题文档并保留入口链接。此项舍弃的是重复旧说明，不是对应运行能力。

## 已做的局部编辑与验证状态

暂停前，仅对以下 10 个冲突文件做了未提交、未手动暂存的字段/导入等局部编辑；未执行舍弃整项能力的操作：

1. 后端 `agents/human_input.py`：合并两边所需 typing/collections 导入。
2. 后端 `config/agents_config.py`：同时保留 `service_ability` 与 `memory_enabled`。
3. 后端 `builtins/task_tool.py`：同时保留动态工具 provider 与同步适配器导入。
4. 后端 `tools/tools.py`：保留实体抽取导入，移除该冲突块内旧的 `cancel_batch` 导入。
5. 前端三份 locales：并列保留 `sqlExecution`、`browserStream`、`skillUsage`。
6. 前端 `input-box.tsx`：仅合并首处导入，采用已存在的官方 `InputMode` 类型。
7. 前端 `human-input-card.tsx`：仅合并 React 导入。
8. 前端 `subtask-card.tsx`：仅合并结果清理与官方终态归并导入。

索引仍将全部 34 个路径标为未合并；Git 自动合并的其它上游文件已经由 merge 暂存，这不同于手动确认冲突已解决。

尚未运行测试或声明集成通过。曾尝试 `uv sync --locked` 同步已有后端环境，因 Windows 无法移除 `asyncpg/pgproto/pgproto.cp312-win_amd64.pyd`（拒绝访问，os error 5）失败；可能已有环境进程占用，但未进一步确认，也未停止进程。后续验证需先完成或改用隔离环境进行依赖同步。前端未安装依赖、未运行检查或测试。

## 建议用户按编号裁决的范围

- **保留现有业务能力：**1（暂保留 `service_ability`）、3、4、6、7、8、9、10、11、12。
- **可以替换旧实现：**2（旧 MCP 发现函数）、5（旧一律 failed 补丁）、7（旧 reconciliation 名单）、13（失效测试脚手架）、16（重复旧文档）。
- **需明确选择：**1（旧子代理字段与工具名白名单是否迁移/保留）、8（审批模式占位控件）、9（普通输入是否能关闭业务审批卡）、14（官方 scanner 或本机阻断替代）。
- **可共同保留：**15（关闭 reload 能力和退出超时），以及 8 中 SQL/Details 两类面板。

用户确认前，不继续冲突解决、不创建合并提交、不移动 `dev`，专项智能体执行路径重构也不实施。

## 最终处理结果（2026-11-12）

用户已逐项裁决 16 组冲突，全部冲突文件已解决并暂存，`git ls-files -u` 为空。最终处理与验证结果：

- 专项智能体保留前端智能体页、`agent_name` 请求标识、SQL 结果面板与 DataAgent 卡片；子代理字段迁移为官方 `allowed_subagents`，删除 `allowable_subagents`、`mcp_tools` 工具名白名单及旧子代理工具注入链。
- 官方 MCP 发现/会话池、HITL 审批、运行生命周期、子任务终态归并与知识引用报告全部接入；手动 Gateway SQL 路由、SQL run context 的 prepare/register/release 与本地业务审批保留。
- 删除 `subagents/tool_provider.py`、`sql_execution/tool_provider.py`、`test_gateway_sql_tool_provider.py`、`test_mcp_agent_allowlist.py` 及 `filter_mcp_tools`。
- 后端定向回归 942 passed / 22 skipped（使用 `config.test.yaml`）；ruff check/format 通过；前端 `pnpm check` 与 68 个相关单测通过。
- 依赖已通过 `uv sync --locked --extra postgres` 和 `pnpm install --frozen-lockfile` 同步。
- 合并提交为 `69448cb6b`，已快进本地 `dev`，未推送远端。
