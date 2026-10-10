# VSSAG 目录上下文纠偏评审

日期：2026-10-09。分支：`feat/knowledge-virtual-directory`。本次继续原需求分支，没有提交、合并或推送，没有恢复已有的 `test_skillscan_native.py` 删除。

## 操作指南精简复核（2026-10-09）

- 按用户反馈重新编写 `docs/guide/vssag-mcp.md`，从 34060 字节缩减至 8174 字节，删除重复安装路径、实现原理和历史验收叙述；首次操作只有安装、配置路径和凭据、合并 JSON、检查并启动、网页测试五步，最后单列日常启动及排障。
- 明确每步编辑哪个实际文件、执行哪组 PowerShell、预期看到什么。SDK 凭据统一使用服务 `.env.dev`，不再并列多套环境变量传递方案；保留三项接入配置、`--skip-install`、文档索引与权限前提。
- 本轮只改文档。五个 PowerShell 代码块语法、JSON 与示例配置一致性（仅 enabled 不同）、四个路径存在性、文档链接、UTF-8 和空白检查通过；未重启服务、未调用生产 SDK/模型、未修改真实配置或 SDK。下方测试数量是此前验收记录，不是本轮重新执行。

## 网页联调配置补充复核（2026-10-09）

用户指出根 `extensions_config.example.json` 没有接入配置，复核属实。此前专用目录示例和离线测试存在，但不足以指导从根配置启动网页；本轮补齐，不把已有离线闭环当作生产网页验收。

- 根示例新增默认禁用的 `mcpServers.vssag` 与合法 JSON `$comment`，明确必须合并 Middleware、身份 interceptor、MCP 服务三项。根全局列表仍留空，避免默认安装被迫导入未安装的可选包；专用示例给出实际类/函数路径，两份服务配置完全一致。
- 专用示例显式设置 `APP_ENV=dev`，对应可信 SDK YAML 旁的 `.env.dev`。只保留环境变量引用和说明，不写入或打印密钥，不覆盖根真实 JSON、YAML 或 dotenv。
- 当时在 `docs/guide/vssag-mcp.md` 补齐 Windows 双环境安装、两套 YAML 区分、路径变量、完整合并 JSON、已有主配置覆盖规则、可信凭据来源、原启动器 `--skip-install`、三轮网页提示词、权限验证、日志证据与排障；现已整理为顺序操作步骤。根 `make dev` 默认同步依赖可能清理额外安装包，是该轮确认并写明的实际启动风险；不修改核心或启动脚本。
- 本轮检查时，根真实 JSON 未注册 vssag，原 Gateway 环境不能直接导入外置包；根 `.env` 已指定 `config.dev.yaml`，不能误把缺少根 `config.yaml` 当作不可启动。按照指南完成安装、合并和重启后才进入人工网页验证。

### 本轮实际验证

1. 两个新增根示例/真实配置加载测试先复现 `KeyError: vssag`，补齐后通过；插件配置测试文件共 **16 passed**。
2. 定向 MCP、目录中间件、插件配置和 blocking-I/O 测试 **69 passed**。真实 DeerFlow 池化 stdio 测试现在同时从根示例和专用示例启动，覆盖工具发现、宿主目录注入、原 ToolNode 两轮反馈与同会话复用；确定性模型/内存索引的检索与文件写入闭环也重新执行。
3. 使用用户现有服务解释器与 SDK 配置执行 live 启动检查 **1 passed**，覆盖初始化、三个工具发现和匿名拒绝。没有把该结果表述为有权限用户的生产数据问答；未主动执行生产建表、灌数、文档查询或外部模型请求，SDK 初始化自身副作用仍属于既有边界。
4. 宿主配置加载、MCP interceptor 与配置重载回归 **66 passed**；隔离安装环境中 `python -I` 可导入 Middleware/interceptor；现有独立服务 `--help` 成功。未持久替用户修改 Gateway 依赖环境。
5. 指南 1 个 JSON 代码块解析通过，除 `enabled` 外与专用示例服务配置一致；10 个 PowerShell 代码块语法解析通过。Ruff check、format --check 和 `git diff --check` 通过。
6. SDK 捆绑目录前后 **3724 个非缓存文件、0 修改/新增/删除**。新增规则只写外置项目 AGENTS，不写 SDK。
7. 额外运行配置及指导文档检查时为 **77 passed、1 failed**：`test_repository_guidance_stays_below_hard_budgets_and_avoids_doc_indexes` 因 `backend/AGENTS.md` 超预算失败。HEAD 已有 292182 字节，限制为 32768 字节；本轮新增说明已放回外置目录规则，未扩大范围拆分既有文档，未声称全仓库测试通过。

**仍待人工验收**：实际 Gateway/UI、真实登录身份、有权限且已索引的文档、外部模型的有命中检索问答。本轮未替用户启用真实配置或重启正在使用的服务。此前的“文档表缺失、SQL 索引有记录”仅是历史数据预检；当前 wrapper 不接 SQL 数据域，仍需 SDK/数据维护方完成相应数据与权限契约，不能靠启用 MCP 配置解决。

## 1、结论与修复

原实现把目录准备做成合成 AI 工具调用，再经 ToolNode 返回 tree。这虽然可能让首次真正推理看到目录，但误将宿主环境准备变成模型行为，并使目录进入历史。纠偏后维持用户原目标：**授权目录 tree 先进入模型上下文，模型根据目录选择路径，再通过 MCP 检索**。

- 外置 `middlewares/vssag_directory.py` 每次 `wrap_model_call/awrap_model_call` 获取目录，调用 `request.override(messages=...)` 后再进入模型。
- tree 是带 provenance、自有 ID/标记的临时 HumanMessage；位于系统消息之后、对话之前，不写回 graph state/checkpoint，不拆开 AI/Tool 配对，不修改原 system prompt。
- 原 Sandbox workspace/uploads/outputs 提示保留并与 tree 共同可见。知识库逻辑路径不是实体 mount，不创建对应磁盘文件。
- 目录后台查询使用独立 MCP 配置、原 MCP 拦截链、user/thread/loop 会话池；检索/读取仍走原模型工具链，保存结果仍为独立 `write_file`。
- 代码只在外置 Middleware 及测试纠偏；Harness/Sandbox/Bash 核心、SDK 和真实配置不修改。

## 2、权限、并发和信任边界

1. 身份继续复用外置 interceptor 的运行时校验，拒绝缺失/非法身份，不接受模型参数中的用户或 grants。
2. 宿主目录操作不经过 ToolNode，因而显式检查当前 Agent 能力、技能策略、配置的 AuthorizationProvider 和 GuardrailProvider。权限服务异常同样不读取，即便普通工具策略为 fail-open。
3. 目录工具 deferred 时，只有当前可见 `tool_search` 和框架 system 内精确的 deferred 声明同时存在，才允许后台准备目录；不调用发现工具，不晋升任何工具。用户/工具文本不能提供该声明。
4. SDK 仍负责 tree 和实际读取的记录级 ACL；目录可见不是文件读取授权。共享知识库 grants 的可信生产者仍需宿主业务服务提供。
5. 每次请求刷新，不缓存 tree 或 current_user；同一实例并行请求按当次身份处理。后台查询与正常工具复用同一异步池作用域。同步入口使用独立事件循环，不承诺跨同步调用复用进程。
6. 超时包括宿主准备与 MCP 等待；异步取消继续传播。失败仅提示目录不可用，不泄露异常详情，不把错误或空返回解释成空知识库。
7. 清洗框架标签和不可信边界；字符预算溢出明确标记不完整。SDK 自身返回受限展开 tree，不能将注入成功描述为全量目录成功。
8. 有 `__run_journal` 时仅记录宿主注入状态；不写目录、正文或凭据，不伪造工具执行记录。

### 已知边界

- 自定义嵌入 Agent 只传入任意 `GuardrailMiddleware` 实例而未配置宿主 provider 时，外置 Middleware 无法遍历或自动复用该实例。访问控制必须配置为宿主 provider；不承诺任意第三方 `wrap_tool_call` 自动覆盖宿主上下文准备。
- 自定义组装省略框架 deferred 声明时，必须让目录工具直接可见，否则安全拒绝加载。框架 prompt 仅用于能力范围判断，实际用户授权仍由 provider/SDK 执行。
- 独立 MCP 配置每次重读以识别禁用；修改服务可执行程序/配置参数时须遵循原 MCP cache reset/服务重启流程，不能让已存活的旧 stdio 进程自动切配置。Middleware 不另建一套配置/进程管理系统。
- 数据权限变化影响后续查询；历史中已返回的普通检索结果不自动撤回。没有实现生产历史数据删除或审计持久化强一致保证。

## 3、测试证据

- TDD：先添加首轮目录、并行隔离、错误、清洗和取消 5 项测试，旧实现全部失败；随后替换实现通过。
- 真实协议：stdio 子进程发现工具、后台加载 tree、后续 ToolNode 调用复用同一 ClientSession。
- 真实 SDK：隔离的 MemoryIndexStore，alice/bob ACL、跨用户拒绝、并行检索与 ToolMessage 对应关系。
- 真实 DeerFlow factory + 原 lead prompt：首次推理包含 tree 与原 `working_directory`；确定性模型根据 tree 根路径读取、检索、写真实临时工作区并回答。
- 多轮并发：同一 Agent/checkpointer、两用户两轮；目录不写入 history，不产生自动 build_vfs 的 ToolMessage。
- 安全回归：服务禁用、身份缺失、Agent 能力排除、技能拒绝、授权过滤/执行拒绝、provider 异常、guardrail 拒绝、结果异常、截断、重试、取消、伪造标记保护。
- 严格 blocking-I/O：服务 SDK 初始化和宿主配置/工作区准备均在线程执行；不替 SDK 的真实数据库实现背书。
- 真实配置验收重新执行：MCP 初始化、三个工具发现、匿名拒绝 **1 passed**；未查询业务记录、未建表、未灌数据、未调用外部 LLM。
- 扩展回归曾得到 **421 passed, 2 failed**；两个失败在不加载 VSSAG 测试的独立进程仍可复现：TableRAG 子进程缺少模块/索引 DSN，以及框架标签分类遗漏 `alias_map/user_query`。未扩展范围修改这些无关模块。
- 最终定向回归 **66 passed**，包含两项 strict blocking-I/O 测试；最后扩展回归 **422 passed, 2 deselected**（显式排除上述两个独立复现失败）。不声称全仓库测试全部通过。
- Ruff check 与 format --check（5 个修改的 Python 文件）、`git diff --check`、skill `quick_validate.py` 通过。
- SDK 捆绑 3724 个受检非缓存文件 SHA-256 复核 **0 变化**；未格式化或修补 SDK 源码/依赖声明。服务安装和测试使用已有 SDK，不将缓存/生成物作为项目改动提交。

## 4、SDK 维护者的后续目标（仅记录，不修改）

`examples/deerflow-vssag-mcp/extension/` 是其他项目的 SDK 捆绑目录，本任务只读取和调用。

### 4.1 SQL / 统一目录公共契约

SDK 已有 SQL VFS builder、`search_sql_schema`、`restore_sql_tool` 等部件；不是要求重新实现 SQL 检索。需要由 SDK 维护者确认/提供稳定公共入口：接收宿主逻辑根路径与可信权限上下文，输出 tree；tree 路径可直接交给对应结构化读取接口；目录、搜索、读取采用一致的用户授权边界。不能直接把 SQLScope 的默认 anonymous 或任意 schema/table 过滤条件当作生产授权。

当前外置 MCP wrapper 只调用文档 `build_vfs/search/restore_tool`，未接 SQL 数据域。此前只读预检见到 SQL 索引有记录而文档表不存在；因此不能把文档空目录当作整个知识库为空，也不能声称现有 SQL 数据已完成问答。SDK 公共身份/路径契约明确后，再扩展外置 MCP 注册与测试，不修改 Harness。

### 4.2 生产只读与错误语义

- 配置 PostgreSQL 后，缺驱动/连接失败/缺表必须可辨识地失败；不得静默退回内存并报告空检索成功。
- 目录和检索只读路径不隐式执行建表 DDL；初始化迁移与在线检索分离。
- 超时和取消后连接恢复、同步阻塞 I/O 与单连接并发约束需由 SDK 提供可验证行为。

### 4.3 大目录边界

保留 tree 展示；受限展开/条数限制应清楚标识，按路径展开需要稳定语义。现有 MCP `grep_file(mode="dirs", path=...)` 可供模型继续浏览；不能要求一次把无限规模目录全部塞进模型上下文。

共享 KB 权限属性的生产者属于 DeerFlow 宿主业务认证/权限接入，不应倒置为让 SDK 自行信任模型输入。
