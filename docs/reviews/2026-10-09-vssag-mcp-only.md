# VSSAG MCP-only 实现评审

> 历史阶段记录：同日新增外置目录 Middleware 与标准插件布局，最新结果见 `2026-10-09-vssag-plugin-middleware.md`。下文“只有两个业务文件”“停止目录自动注入”不再描述当前实现。

评审日期：2026-10-09。当前分支 `feat/knowledge-virtual-directory`。本次交付是离线验证的文档检索 MVP，不是生产数据源/真实模型上线验收。工作区原有其他修改未回滚，未提交、合并或推送。

> 同日追加 SDK 迁移：当前服务 Python 包名为 `vssag_mcp_server`，SDK 源码位于该项目 `extension/`。下文第 4 节是迁移前验证记录，最新独立环境与迁移验证见第 6 节。

## 1、方案与变更范围

用户已明确降低复杂度：全部知识检索走 VSSAG MCP，Bash 和文件工具继续使用 DeerFlow 默认实现。因此停止目录自动注入、Bash 拦截、混合 Shell 解释器开发。

外置包 `examples/deerflow-vssag-mcp/` 只有服务实现和身份 interceptor 两个业务文件：

- `server.py`：注册 `build_vfs/search/grep_file`，调用 SDK 公共 API，管理服务生命周期、串行访问及查询时限。
- `interceptor.py`：通过既有 `mcpInterceptors` 配置，仅向 `vssag` 的工具调用追加可信身份元数据。
- `extensions_config.example.json`：默认禁用；operator 合并进现有配置，保留其他服务、skills 和 middleware。
- `pyproject.toml`：独立可安装包和 stdio CLI，不加入 Harness 必需依赖。

相对 HEAD，`backend/packages/harness/deerflow/` 无本需求代码差异；SDK 源码不修改。旧需求未跟踪的 `deerflow/knowledge` 文件及对应三个旧测试文件已移除，原输入清洗专属标签改动也已撤回。README、backend AGENTS、配置注释、公共 API 指引和原计划/评审同步指向新方案。

原 Python plugin 的 `IsolatedMiddleware` 不是本方案入口；不通过插件中间件短路执行、不改 Agent builder、不插入模型专用目录消息。现有 MCP 标准工具发现、路由与 ToolMessage 才是调用链。

## 2、权限与启动安全

1. 工具参数中不暴露用户、角色、授权属性、thread/run 或根目录。interceptor 从宿主 runtime 取身份，模型试图通过 args 注入身份时拒绝。
2. 私有 stdio 的 `_meta.headers` 携带 ASCII JSON，大小上限 16 KiB；服务独立验证身份，缺失即拒绝。此元数据不具有公共网络认证能力，不可直接对外提供 HTTP。
3. Gateway 尚无共享 KB 授权属性生产者；不凭空赋权。当前依赖 SDK 索引 owner 和实际授权过滤，shared/workspace/source grants 仍需可信业务来源。
4. stdio 可能从模型可写的 workspace 启动。样例通过 `python -I -m` 禁止从 cwd/PYTHONPATH 导入恶意同名模块；CLI 再将自身 cwd 切到 operator 控制的 SDK YAML 目录，防止 SDK 自动读取 workspace dotenv。
5. 上述措施不是操作系统安全沙箱：operator 仍需保证配置、依赖源码、索引目录和凭据不对模型可写，尤其在 LocalSandbox 与 Gateway 共用宿主账号时。

没有直接暴露 SDK 现有 `vssag/mcp.py`：其接口允许调用方提供身份/权限，且缺少本方案 tree 接口。外置服务复用 SDK 的 `build_vfs/search/restore_tool/close`，不修改 SDK、不调用 `restore_bash`。

## 3、多轮、并行与文件输出

- 原 `tool_call_id` 用于结果关联。模型依次取得 tree、搜索、读取、继续回答，或另行调用原生 `write_file`。
- 同轮并行调用沿用 ToolNode 与 MCP；stdio pool 按 server/user/thread/loop 隔离。服务进程内锁定串行访问 SDK，不宣称底层并行吞吐已优化。
- 不混用虚拟与真实路径执行 Shell。此前 `grep ...虚拟路径 > ...工作区文件` 的业务结果通过“MCP 检索 → 原生写文件”实现，不把原始命令作为反馈指令自动执行。
- 服务时限包含锁等待。SDK `ok=False`/异常转换为 MCP error，不回退 Bash、不回显底层连接秘密。
- 测试发现当前 MCP 客户端取消 await 不会自动向服务发送取消通知；服务端可能继续到自己的超时。已验证可协作取消的异步操作到期释放锁并接受后续用户请求，不能声称真实 DB 查询即时终止。
- SDK 的阻塞 I/O、大目录内存开销、数据库取消恢复仍需 SDK 和实际后端补齐。原会话中已存在的知识内容也不会因权限撤回自动清除。

## 4、验证记录

所有命令使用 PowerShell，backend 的既有 Python 3.12.11 环境；通过 `uv run --no-sync --with-editable ...` 临时加入两个可选包，没有改 backend 的依赖声明或锁文件。

### 4.1 本需求及相关路径

- 新测试先红后绿；错误/超时、身份缺失和伪造、上下文大小与类型、大小写 header 覆盖、工具 schema、目录和 owner ACL 均有断言。
- 真实 SDK MemoryIndexStore：Alice/Bob 文档相互不可见，猜测他人路径读取被拒绝。
- 真实 MCP 内存传输 + DeerFlow pooled wrapper + ToolNode：并行 search/read 返回各自的 `tool_call_id`。
- 确定性模型 + `create_agent` + 真实 SDK/MCP + 原生 `write_file` + LocalSandbox 临时目录：目录 → 搜索 → 读取 → 写文件 → 模型收到 OK 完成。仅替换 Sandbox 获取与授权边界，不声称覆盖 Gateway 登录或全量 Middleware。
- 真实 stdio 子进程 CLI：工具发现、匿名拒绝、合法身份空目录查询；workspace 同名 Python 文件和 dotenv 不影响服务启动。
- 使用示例配置、真实 `get_mcp_tools`、真实会话池和真实 stdio 子进程，连续两次从 ToolNode 获取成功结果；只重定向测试数据路径与 pool 实例，不替换传输。
- strict blocking-I/O：同步 SDK factory 读文件在线程执行，异步查询保留当前事件循环；不覆盖真实后端本身。
- 相关集合已得到 **177 passed**；随后新增的真实配置→stdio pool 回归单项 **1 passed**。最终合并运行状态见计划的验证记录。
- Ruff check 通过；Ruff format --check 对 5 个新增 Python 文件全部通过。

### 4.2 广泛回归的非本需求失败

相关扩展集合执行结果：**384 passed，3 failed**。失败位于未修改的测试/执行路径：

| 测试 | 观察到的原因 |
| --- | --- |
| `test_mcp_session_pool.py::test_stdio_server_can_expose_exact_unprefixed_tool_name` | 测试只 mock `MultiServerMCPClient`，非前缀路径改走直接发现并启动 TableRAG；缺少该服务 index DSN |
| `test_mcp_sync_wrapper.py::test_func_patched_mcp_tool_keeps_toolnode_runtime_injection` | 测试返回 str，但工具声明 `content_and_artifact` 要求二元组 |
| `test_input_sanitization_middleware.py::test_denylist_covers_framework_authority_blocks` | 现有 `alias_map/user_query` 标签未分类 |

默认 backend 离线集合运行 `pytest -m 'not live' --ignore=tests/blocking_io tests/ -q --tb=line --maxfail=5`，结果 **2022 passed，37 skipped，3 deselected，5 failed**，达到失败上限停止，未完整跑完全仓：

- DataAgent SOUL 缺少其业务查询闭环规则。
- backend AGENTS 超过指导文件字节预算；HEAD 中原文件也远超 32 KiB，并非此次新增 21 行才越界。
- Lark CLI auth 目录权限和 BoxLite executable-bit 断言不符合本机 Windows 文件模式。
- Windows 创建符号链接缺少系统权限（WinError 1314）。

全 blocking-I/O 集合以 `--maxfail=5` 运行：**70 passed，5 failed**，同样未跑完；失败为 Windows chmod 断言三项、OpenViking 缺少测试所需 API key 一项、Lark CLI 未安装一项。没有为通过测试而改动真实配置、安装业务 CLI、添加生产密钥或修复无关模块。

不能据此宣称全仓测试通过。接手前已删除的 `backend/tests/test_skillscan_native.py` 和其他非本需求修改保留原状。

## 5、交付边界与后续验收

- **已完成**：外置文档检索 MCP、可信 stdio 身份传递、原生文件工具闭环、默认禁用示例和部署文档。
- **未启用**：没有写真实 `config.yaml/extensions_config.json`，没有持久安装或启动生产服务。
- **待用户部署验收**：真实 owner/ACL 数据、数据库与 embedding 后端、Gateway 登录→真实 LLM→检索与文件输出的 UI 全流程。
- **SDK/业务后续项**：共享 KB 权限生产者、SQL grounding 权限与工具、大规模目录分页/输出边界、阻塞 I/O 和数据库取消恢复。当前仅暴露文档检索三个工具，不能称为全部 SQL 能力接入。

部署步骤见 [指南](../guide/vssag-mcp.md)，公共入口见 [API](../guide/used-api.md)，Todo 见 [计划](../plans/2026-10-09-vssag-mcp-only.md)。

## 6、SDK 迁移与服务目录独立化

用户已将 SDK 从 backend 迁至 `examples/deerflow-vssag-mcp/extension/`，并将 Python 服务包改名为 `vssag_mcp_server`。本次保留新布局，修复迁移后的 wheel packages、console script、MCP interceptor 路径、`-m` 启动路径、测试和部署说明，不保留旧名称兼容层。发行包名/命令仍为 `deerflow-vssag-mcp`，不把 Python 目录名与发行名混淆。

### 6.1 隔离边界

- 服务根 `pyproject.toml` 的基础 dependencies 为空；`server` extra 才安装 MCP 与 SDK。Gateway 只需基础包中的标准库 interceptor。
- uv sources 指向本地 `extension/` editable；`.python-version` 指定 3.11；服务自己的 `.venv` 和 `uv.lock` 位于外置目录。
- 实际执行 `uv sync --python 3.11 --extra server`，创建独立 Python 3.11.10 环境；没有修改 `backend/pyproject.toml` 或 `backend/uv.lock`，也未向 backend 持久安装新服务包。
- 本地 `.gitignore` 排除服务虚拟环境、Python/测试缓存和迁移过来的 Worker `node_modules`；不删除用户已有 SDK/Worker 文件。
- Harness、默认 Bash/文件工具实现不变，SDK 业务源码不变；仅更新 SDK AGENTS 中的当前位置。DeerFlow 侧保留集成测试是为了检验真实宿主链路，不是将业务实现移回 backend。
- 仍是私有 stdio，由 DeerFlow 按配置启动子进程；手动在该目录启动不会创建可供 Gateway 连接的 HTTP 常驻服务。

### 6.2 迁移验证

- 先更新迁移测试，观察到 **2 项预期失败**：基础包仍强依赖 SDK、配置仍使用旧包名；修复后变绿。
- SDK/MCP/权限/原生写文件/真实 stdio pool、strict blocking-I/O 以及迁移后的 AGENTS 路径集合：**21 passed**。
- 加上既有 MCP interceptor、runtime headers、默认 Bash 加载、文件大小保护回归后的最终集合：**98 passed**；Ruff check、format --check 对 6 个相关 Python 文件通过。`git diff --check`、本地文档链接、SDK 锁文件来源和旧路径扫描通过。
- 在服务自己的 Python 3.11.10 环境，`python -I -m vssag_mcp_server.server --help` 成功；真实 stdio 子进程发现三个工具、拒绝匿名调用、带合法元数据查询 tree 均通过。确认服务与 SDK 都从迁移后的路径导入，不依赖 backend 包。
- 真实配置保持未改、示例仍默认禁用。上文全仓既有失败未在本轮重新修复，不能把迁移专项通过称为全仓通过或真实业务数据源验收。
