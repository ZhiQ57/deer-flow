# DeerFlow 知识库虚拟目录与 Bash 检索方案评审

> **历史评审，已失效（2026-10-09）**：用户已选择 MCP-only。下文插件、Bash 路由、Worker 缺失及相关测试结论不用于当前实现；SDK Worker 状态也已变化。旧知识库 Harness 实现已移除，当前入口与验证见 [MCP-only 评审](2026-10-09-vssag-mcp-only.md) 和 [部署指南](../guide/vssag-mcp.md)。

## 结论

当前实现采用 DeerFlow 现有扩展与 middleware 骨架完成对接，外部 SDK 只作为可选运行依赖。核心链路是：

1. `MODEL_PHYSICAL` middleware 在每次实际模型请求前获取当前用户可见的 tree，并以临时隐藏消息注入。
2. `TOOL_RAW` middleware 在真实 `bash` callable 前识别逻辑根路径，命中后把完整原始 command 交给 SDK。
3. SDK 响应转换成普通 `ToolMessage`，继续沿用 DeerFlow 的工具错误、预算、进度、收据和 SSE 链路。

本方案不改 Sandbox provider、物理 mount、`PathMapping`、`ThreadState`、Lead/Subagent 主循环，也不涉及 ownmindforge 的 `blockfs` 业务层。

## 1. 现有 DeerFlow 链路复用

### 1.1 扩展加载

`deerflow.knowledge.directory:install` 通过现有 `plugins:` 机制注册一个生命周期 service 和两个 middleware。Harness 本身不导入 SDK；只有 operator 启用该插件时，`KnowledgeService` 才执行：

```python
from vssag import VSSAGAccessContext, VSSAGClient
```

因此未安装外部 SDK 时，DeerFlow 的普通启动、Sandbox 和其他工具不受影响；启用插件但依赖缺失时，由现有扩展加载诊断处理。

### 1.2 Middleware 位置

- 目录注入使用 `Placement.MODEL_PHYSICAL`，表示每次实际 provider 请求都刷新，而不是只在逻辑模型决策前运行一次。
- Bash 路由使用 `Placement.TOOL_RAW`，位于真实工具 callable 边界内侧；命中后不调用下游 `bash_tool`。
- 两个 middleware 都使用 `AgentScope.BOTH`，Lead 和 Subagent 共用同一个 service，但每次调用单独读取 runtime 身份。

这两个位置正好复用现有 `extensions.stack` 的语义锚点，不需要改 Agent 组装顺序或新增 Graph 节点。

## 2. 用户访问权限解析

权限必须分为“身份来源”和“目录裁决”两段处理。

### 2.1 DeerFlow 负责可信身份

`KnowledgeService` 每次调用只从当前 runtime 读取：

```text
user_id
user_role
authz_attributes
thread_id
run_id
```

其中：

- HTTP 请求的 `user_id` 来自 Gateway 服务端认证；
- IM、调度任务使用已解析的 owner 用户；
- Subagent 继承父任务传递的用户、角色、授权属性、thread 和 run；
- 缺省角色复用 DeerFlow 授权配置的 `default_role`；
- 缺少 thread/run 坐标时拒绝本次知识库调用，不创建共享默认上下文。

客户端输入、模型文本、Bash 参数和 operator 根目录都不能改变这些值。`authz_attributes` 通过现有 `normalize_authz_attributes` 复制后再交给 SDK，避免调用期间引用可变的 runtime 容器。

### 2.2 外部 SDK 负责可访问目录

DeerFlow 不根据 `user_id` 拼出目录，也不维护知识库 ACL。调用关系是：

```text
服务端认证身份
    -> runtime.context
    -> SDK AccessContext
    -> SDK build_vfs(root)
    -> 返回当前用户可见 tree
```

tree 只是发现信息，不能作为权限证明。Bash 查询时仍将同一服务端身份重新传给 SDK，由 SDK 对模型手写的路径再次授权：

```text
完整 Bash command
    -> DeerFlow 只判断是否包含逻辑 root
    -> SDK restore_bash(command, access_context, root)
    -> SDK 返回结果或拒绝
```

DeerFlow 不保存当前用户权限；真实 SDK 的权限投影已通过离线测试。共享 SDK 的事件循环资源和 cwd 是否隔离，需要另行验证，不能仅凭访问上下文按调用构造就认定并行安全。

## 3. 目录注入实现

### 3.1 为什么不修改现有动态上下文 middleware

`DynamicContextMiddleware` 负责日期、memory、消息 ID 替换和跨午夜刷新；知识库 tree 的生命周期完全不同：

- 每次模型请求都可能变化；
- 权限撤销必须立即反映；
- 内容不应写入 checkpoint；
- 返回内容属于不可信外部数据。

因此实现一个独立的 `KnowledgeDirectoryMiddleware`，只复用现有 `request.override(messages=...)` 模式。每次调用先删除自身标记的临时消息，再追加新 tree；原始 state 不被修改。

### 3.2 root 传递

配置中的 `/home/mnt/knowledge` 原样作为 SDK 的 `mount_root` 传递。DeerFlow 不拼接 `/vssag`、不转换 Windows host 路径、不加入 Sandbox mount。SDK 自己负责其虚拟子树和 tree 内容。

### 3.3 内容安全与大小

SDK tree 先经过现有 `neutralize_untrusted_tags`，再放入隐藏 `HumanMessage`。DeerFlow 只按字符上限截断返回文本，不解释文件名、不读取文件正文、不重新生成目录。

## 4. Bash 拦截实现

### 4.1 为什么不把分支塞进 `bash_tool`

`bash_tool` 一开始会初始化 Sandbox，再进行路径校验、虚拟路径替换和工作目录前缀。直接修改它会把两套执行语义耦合，并容易绕开现有 middleware 链。

`TOOL_RAW` middleware 在 callable 前短路，命中知识库命令时：

- 不调用 `ensure_sandbox_initialized`；
- 不添加 `/mnt/user-data/workspace` 前缀；
- 不执行本地路径转换；
- 外层授权、审计、错误、预算、进度和收据 middleware 仍然可观察该结果。

### 4.2 DeerFlow 只做路由识别

路由器只检查完整 command 是否包含配置 root 且满足路径边界。它不使用 `shlex`，不拆分 `&&`、管道或重定向，不替换任何字符。命中后完整字符串逐字传给 SDK。

因此下面的命令会进入 SDK：

```bash
grep -n "invoice" /home/mnt/knowledge/vssag/finance.md
cd /home/mnt/knowledge/vssag && rg "timeout" .
```

而只使用相对路径的后续独立调用无法由 DeerFlow 判断其目标目录。第一阶段要求模型使用 tree 中的绝对路径，或在同一个完整 command 内完成 `cd` 和检索。跨调用 cwd 如需支持，必须由 SDK 按 run 隔离，DeerFlow 不增加 cwd 状态。

### 4.3 安全 middleware 的优先级

现有授权和 Sandbox 审计仍然位于外层。知识库路由不绕过高风险命令检测或通用工具授权；外层拒绝时，命令不会到达 SDK。这保证新路径不会成为现有安全策略的旁路。

## 5. 结果如何回到模型

完整链路如下：

```text
AIMessage.tool_calls[n]
    -> KnowledgeCommandMiddleware
    -> SDK restore_bash(完整 command)
    -> ToolMessage(tool_call_id=原始 id)
    -> LangGraph messages
    -> 下一次模型调用
```

SDK 成功响应的终端文本放入 `ToolMessage.content`；拒绝响应使用 `status="error"` 并保留可读错误。SDK 异常或超时转换为通用错误，不把内部堆栈暴露给模型。路由标记、状态、模式和路径只放在现有 `additional_kwargs`，不新增前端事件。

因为返回值是普通 `ToolMessage`，现有外层链继续生效：

- `ToolErrorHandlingMiddleware` 负责标准化工具结果；
- `ToolOutputBudgetMiddleware` 负责输出预算；
- Tool receipt/progress/audit 继续记录；
- Gateway 通过既有 messages/SSE 发送工具结果。

## 6. 多轮、反馈与并行

### 6.1 多轮请求

支持。每次物理模型调用刷新 tree，上一轮 Bash 结果作为普通工具消息保留。临时 tree 不进入历史，因此目录变化或权限撤销会在下一次模型调用生效。

### 6.2 人工反馈、暂停恢复和重试

DeerFlow 会重新经过相同 middleware，并用当前服务端身份重新构造访问上下文。旧临时 tree 在本次请求中被移除。SDK 超时取消后的 Worker 清理尚待修复和验证，不能把这条消息链能力等同于 SDK 完整恢复能力。

### 6.3 同一响应中的并行 Bash

DeerFlow 每个 tool call 独立携带 command、runtime 和 `tool_call_id`。真实 SDK 的同一事件循环、无 `cd` 并行检索和反馈已通过内存索引测试；同一 run 的并行完整 command 共享 SDK cwd，已复现相对路径串目录。真实数据库并发还需要 SDK 连接池与初始化保护。

### 6.4 多 thread 和 Lead/Subagent 并行

两个 agent scope 已注册同一 service，访问上下文按调用重建。真实 SDK 的 `asyncio.Lock`、Worker 子进程流和数据库连接具有事件循环归属；DeerFlow 的 Gateway、隔离 Subagent 和同步桥接可在不同 loop 调用 service。当前不能声明跨 loop 的真实 SDK 并行已支持。

## 7. 代码与配置变更

### 已修改

- `backend/packages/harness/deerflow/knowledge/directory.py`：service、目录 middleware、Bash middleware、扩展注册。
- `backend/packages/harness/deerflow/knowledge/__init__.py`：通用扩展入口。
- `backend/tests/test_knowledge_directory.py`：SDK 边界、权限、同步/异步、错误和并行测试。
- `config.example.yaml`：可选插件配置示例。
- `README.md`：用户配置和行为说明。
- `backend/AGENTS.md`：内部架构约束。

### 明确未修改

- Sandbox provider、`bash_tool`、`PathMapping` 和物理 mount；
- `ThreadState`、Lead/Subagent 主循环和 Gateway 消息协议；
- 外部 SDK 源码；
- ownmindforge `blockfs` 适配层。

## 8. 风险与验证

重点验证以下边界：

1. 客户端不能伪造用户或 root；
2. 命中 root 的拒绝命令不能回退真实 Sandbox；
3. 异步 SDK 初始化不阻塞事件循环；
4. 临时 tree 不进入 state/checkpoint；
5. tree 与 Bash 返回内容都经过非可信内容处理；
6. 并行调用不共享错误的用户或 tool call ID；
7. Subagent 使用父运行的真实身份。

2026-09-24 的验证仅使用 fake SDK。2026-10-08 已加入复制 SDK 的真实配置、访问上下文、内存索引、目录构建和结果恢复测试，具体范围与结果见下文。

## 9. 2026-10-08 SDK 核验与继续开发

### 9.1 已满足的 SDK 契约

| 要求 | 源码与验证 | 结论 |
| --- | --- | --- |
| 公共 SDK 入口 | `vssag/__init__.py` 导出 `VSSAGClient`、`VSSAGAccessContext`；`client.py:60` 提供 `from_env()` | 符合；需先安装 SDK 自身依赖 |
| 接收 DeerFlow 根目录 | `vfs/paths.py:8` 将父路径拼接模块名；真实测试首行是 `/home/mnt/knowledge/vssag/` | 符合，无需 DeerFlow 转换路径 |
| 默认返回 tree | `client.py:555` 返回字符串；`vfbuild/builder.py:112` 调用 tree 渲染 | 模型可见返回值符合，内部仍全量读取正文 |
| 完整 Bash 输入 | `client.py:540` 接收 `command`、访问上下文和 `mount_root` | 方法签名符合；真实 Worker 尚缺失 |
| 用户权限裁剪 | `vssag_schemas/vfs/access_context.py:83` 判断 owner、角色、来源白名单、工作区、知识库和 thread/run | 私有文档及可信工作区授权已验证 |
| 工具反馈 | `VFSResponse` 提供 `ok/content/error/mode/path` | DeerFlow 普通 `ToolMessage` 路径已验证 |

真实 SDK 测试对其他用户的手写路径返回 `file not found`，不会泄露正文；目录返回值只包含树文本。共享工作区测试中的 `workspace_ids` 来自测试模拟的可信服务端上下文，不能代表现有 HTTP Gateway 已生成了这些授权。

### 9.2 已完成的 DeerFlow 开发

1. 保持两个现有语义位置：模型物理调用前注入 tree，真实 Bash callable 前路由完整 command；两个位置均覆盖 Lead/Subagent。
2. 修复嵌套 `authz_attributes` 引用共享；传入 SDK 的列表、字典与服务端 runtime 分离。
3. 将 `knowledge_directory` 加入现有标签清洗名单，防止目录名称或 SDK 反馈闭合框架目录标签；同步补齐框架标签回归测试。
4. 超时配置拒绝 `NaN`、正负无穷值。
5. 配置示例使用 SDK 的真实发行名 `ownmindforge-vssag`；Python 导入名仍是 `vssag`。提供可选依赖组安装方式，不把 SDK 加入 Harness 必选依赖。
6. 增加真实 SDK 离线测试和 DeerFlow SDK 初始化 blocking-I/O 门禁；未修改复制的 SDK 源码。

### 9.3 SDK 需要补齐的部分

以下 SDK 路径均相对于 `backend/packages/extension/vssag/`。

**1. Bash Worker 未交付：当前真实 Bash 执行的直接阻塞。**

`configs/retrieval_config.py:22` 默认指定 `backend/packages/extension/just-bash-worker`。本仓库没有该目录、`worker.mjs` 或 Node 依赖。`vfs/vfsrestore/just_bash.py:89` 必须找到 Worker 与 `node_modules/just-bash` 才会启动。当前真实 `restore_bash()` 返回 Worker 不可用的错误。

请提供 Worker 源码、锁定依赖及安装步骤，或让 SDK 安装包可靠定位随包交付的 Worker；保留既有公开 Bash 请求协议即可。DeerFlow 已验证此错误转为普通工具反馈，且不会回退真实 Sandbox。通过 parser double 的测试不能替代真实 Worker 的语法和管道验证。

**2. 配置加载仍要求宿主项目的阶段 YAML：需要独立 SDK 配置入口。**

`configs/loader.py:299` 按 cwd 查 `.env`、`.env.<stage>`，读取通用 `CONFIG_FILE`，缺省要求 `config.dev.yaml`。在 DeerFlow 的 `backend/` 直接运行 `VSSAGClient.from_env()` 已复现 `FileNotFoundError`。仅安装 Python 包不能创建 client。

现阶段可由 operator 指定专门的 SDK YAML；测试使用临时 YAML 完成真实 `from_env()`。请 SDK 支持专用 `VSSAG_CONFIG_FILE` 或等价的独立配置入口，并明确无 YAML 时是否可用 SDK 环境变量构造配置。保持 `VSSAGClient.from_env()` 的公共签名。DeerFlow 不应靠切换全局 cwd 或修改全局环境来逐请求加载 SDK 配置。

**3. 共享 client 的事件循环归属：当前 Lead/Subagent/同步混用不满足。**

`JustBashParser` 共用一个 `asyncio.Lock`、一个子进程和一组流（`just_bash.py:27`、`:44`、`:119`）；`PgVectorIndexStore` 共用一个 asyncpg 连接（`index_stores/pgvector_store.py:33`、`:44`）。这些资源不能安全地跨线程、跨事件循环使用。DeerFlow 的同步桥接会创建临时 loop，Subagent 可在隔离的持久 loop 执行；现有单一共享 client 策略与之冲突。

请 SDK 按执行 loop 管理自己的资源，或提供满足混合同步/异步调用的明确线程安全生命周期契约，同时保证初始化与关闭在资源所属 loop 完成。只对构造器加 `threading.Lock` 不能修复这个问题。数据库并行应使用每次查询独立获取的连接，并保护首次初始化，避免单连接并发与连接覆盖泄漏。此项来自源码与 DeerFlow 拓扑分析，尚未用真实 Worker/Postgres 联测。

**4. 同一 run 并行 command 的 cwd 竞争：已确定性复现。**

`vfs/vfsrestore/bash.py:21` 保存全局 cwd 字典，`:127` 按 `(context.user_label, mount_root)` 取值。相同用户/thread/run 的并行 tool call 使用同一个键。第一条 `cd <用户文档> && tree . && cat md.md` 等待目录构建时，第二条 `cd <模块根> && pwd` 改变了共享 cwd，第一条继续执行 `cat` 就从错误位置读取并得到 `file not found`。

请让同一完整 command 的 cwd 在本次执行中独立传递。第一阶段不要求跨 tool call 保留 `cd`；DeerFlow 的后续纯相对命令仍不会命中根目录路由。无需新增 DeerFlow cwd 或修改 Bash command。该要求已在真实 SDK 测试中以严格 `xfail` 保留。

**5. 大规模数据库检索目标尚未实现：tree 与 grep 都全量加载正文。**

`vfbuild/builder.py:54` 先 `list_records()`，`:79` 拼接所有可见文档正文；`build_tree()` 先构建完整文件映射再渲染目录。`vfsrestore/bash.py:225` 的 grep/rg 同样构建全部文件，然后在 Python 中遍历每一行。pgvector 的 `list_records()` 取出整个 payload；LanceDB 的 `list_records()` 使用 `to_arrow().to_pylist()` 全表转换。树展开上限、DeerFlow 文本截断只减少模型输入，不能减少数据库读取与宿主内存占用。

请 SDK 目录构建只查询目录所需元数据；命令恢复根据路径和授权约束把检索、分页、上限下推数据库，按需读取具体文件或行范围。当前可用于小规模功能测试，不能据此声明支持大规模知识库。保持原来的 `build_vfs()` 和 `restore_bash()` 两个入口即可。

**6. 异步内部仍有阻塞 I/O，超时取消清理不完整。**

`just_bash.py:89` 在异步方法里同步读取 cwd、检查文件、解析路径和搜索 Node；LanceDB 在 async 方法里同步调用表查询与 Arrow 转换。DeerFlow 已把同步 `from_env()` 放入工作线程，但不能据此保证 SDK 异步方法内部非阻塞。

`JustBashParser.parse()` 只捕获 `Exception`；`asyncio.CancelledError` 不在其中。DeerFlow 超时或用户取消会退出 await，Worker 可能继续解析并留下回复；下一次调用逐行 timeout 也没有整个请求的统一截止时间。请 SDK 将自身同步 I/O 移到工作线程，在取消时清理当前解析/Worker并重新抛出取消，验证下一条请求可继续且关闭不遗留进程。真实 Node 超时取消验证要在 Worker 补齐后进行。

**7. 受限 Bash 语义必须明确：当前静默忽略部分选项。**

`bash.py:330` 跳过未知 `grep/rg` 选项；`:383` 无条件使用大小写不敏感的 Python 正则。已复现 `grep -F 'Invoice.*'` 匹配普通 `Invoice ALICE_ONLY`，不符合固定字符串含义；`-v`、`-l`、计数/上下文选项等也不能按当前代码认定已支持。管道仅实现后续 `head/tail`，返回的是最后一个流水线的结果。

请优先保证计划中的 `grep/rg/tree/ls/cat/head/tail/sed` 常用子集；未支持的选项返回明确拒绝，不能静默当作另一种查询执行。不要求 SDK 执行任意 shell。`grep -F` 需求已保留为严格 `xfail`。

### 9.4 用户权限数据的真实来源

SDK 已支持 `source_ids`、`workspace_ids`、`knowledge_base_ids` 和记录元数据裁剪。DeerFlow Gateway 的 `inject_authenticated_user_context()` 提供可信 `user_id/user_role`，并清除客户端伪造的 `authz_attributes`；当前没有知识库 grant 的服务端生产者。

因此已有 `metadata.user_id` 的私有记录可以直接依据用户身份过滤；共享工作区或知识库需要可信授权数据来源。请明确 SDK 是否可根据 `user_id` 从自身权限库解析最新 grants，再由 `build_vfs/restore_bash` 使用；否则后续必须在 DeerFlow 认证后的服务端入口对接实际 ACL 数据源并生成这些字段。不能把客户端传来的知识库列表视为权限，也不能仅凭角色或目录曾被展示就授权读取。当前未虚构 ACL 数据源或添加硬编码用户映射。

当前 `knowledge_base_ids` 是附加过滤条件：通过该过滤后仍需满足 owner、workspace 或显式 `source_ids` 白名单。它本身不会授权共享知识库内非当前用户拥有的记录。SDK 需要明确“知识库 grants”和“查询缩小范围”的区别，防止服务端提供可访问知识库列表后却仍看不到共享记录。

若后续要包含 SQL Schema 分区，当前 `build_vfs/restore_bash` 只进入文档 VFS，SQL 有单独的 `sql_vfs/restore_sql_bash` 入口。需要 SDK 在同一公开入口内部完成分区组合与路由；DeerFlow 不增加 SQL command 解析或专用路径转换。此项属于 SQL 能力扩展，不阻塞文档 VFS 的现有验证。

### 9.5 验证范围与结果

- `tests/test_knowledge_directory.py`：DeerFlow 原始 command、身份、临时目录消息、错误/取消反馈与路由。
- `tests/test_knowledge_directory_sdk.py`：真实 SDK YAML、AccessContext、MemoryIndexStore、tree 和结果恢复；真实 Worker 缺失测试不替换 parser，其余 Bash 恢复测试只替代语法 parser。
- `tests/blocking_io/test_knowledge_directory.py`：DeerFlow 同步 SDK 初始化在线程执行。
- 扩展位置/加载/组装和 Harness 边界：继续使用现有回归测试。
- 输入清洗全目录漂移检查中现有 `alias_map/user_query` 未分类，与本次知识库改动无关；`knowledge_directory` 已补齐并通过单项覆盖。

本次已把复制 SDK 的 `AGENTS.md` 纳入现有目录指引允许列表；其路径新增引起的门禁失败已修复。未修改 SDK 自身指引。

2026-10-08 实测结果：

| 验证命令或范围 | 结果 |
| --- | --- |
| 知识库、真实 SDK、单项 blocking-I/O、扩展位置/组装/加载、Harness 边界、输入清洗（排除已单独确认失败的全目录标签漂移检查） | `254 passed, 2 xfailed, 1 deselected` |
| SDK 指引允许列表单项回归 | `1 passed` |
| `ruff check` 与 `ruff format --check`，覆盖本次 Python 改动 | 通过 |
| 全部 blocking-I/O（Windows） | `125 passed, 4 failed, 1 skipped`；3 项 POSIX 文件权限断言、1 项 Lark CLI stub 在 Windows 无法发现 |
| backend 默认离线套件，`--maxfail=3` | `688 passed, 3 failed, 20 skipped, 3 deselected` 后停止；未完成全部测试 |

默认套件当时的三项失败是：本地 DataAgent SOUL 内容不满足规则；复制 SDK 指引未登记；既有模块指引超出硬大小限制。第二项在本轮修复并单独回归通过，其余未扩大修复范围。输入清洗全目录漂移检查仍有既有 `alias_map/user_query` 未分类。

真实 SDK 单独运行是 `5 passed, 2 xfailed`。两项严格预期失败只接受断言失败，分别固定 SDK 的 `grep -F` 与并行 cwd 需求；配置或其他异常不会被该标记掩盖。SDK 修复后出现 `XPASS(strict)`，应移除对应标记并继续真实 Worker 与数据库联测。

此处不把 fake parser、内存索引或预期失败计入真实 Worker、真实数据库、多 loop 的成功联测。
