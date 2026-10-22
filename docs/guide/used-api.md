# DeerFlow 知识库与 SQL Schema 集成公共入口

2026-10-09 纠偏：先由外置 Middleware 注入授权 tree，与原 Sandbox 目录上下文共同可见；模型随后通过 MCP 检索。MCP 仅替代检索执行方式，不取消目录上下文。Bash 保持默认。旧 `deerflow.knowledge:install` 与 `deerflow.knowledge.directory` 已删除，不保留兼容入口。

## 1、外置包入口

- 路径：`mcp-extensions/vssag/`；发行名 `deerflow-vssag-mcp`，导入名 `vssag_mcp_server`。
- SDK：同目录 `extension/`，发行名 `ownmindforge-vssag`、导入名 `vssag` 不变；uv sources 使用本地 editable 来源。该目录只读，业务缺口交 SDK 维护者处理。
- 服务环境：从该目录运行 `uv sync --python 3.11 --extra server`；Gateway 只安装无依赖基础包以导入 interceptor，不安装 server extra。
- 生产接入：DeerFlow 根据启用的 stdio 配置按需启动 MCP 子进程，无需手工运行服务。独立调试仍可使用 `uv run --no-sync --env-file .env.dev python -I -m vssag_mcp_server.server --config <绝对SDK配置路径> --root /home/mnt/knowledge`；无 HTTP 监听端口。
- `vssag_mcp_server.server.create_server(*, mount_root, timeout_seconds, client_factory=None)`：三个只读 MCP 工具；factory 仅用于测试替换外部后端。
- `vssag_mcp_server.interceptor:build_context_interceptor()`：经既有 `mcpInterceptors` 配置加载，只给 `vssag` 附加可信上下文，不拦截 Agent/Bash/文件工具。
- `middlewares.vssag_directory:VssagDirectoryMiddleware(max_tree_chars=16000, timeout_seconds=45)`：经既有 `middlewares` 加载；每次模型请求前通过私有 stdio MCP 获取 tree，用 `request.override(messages=...)` 注入临时 HumanMessage。保留原 system/Sandbox/tools，不写入历史，不合成 AI tool call。
- 后台目录调用复用 `build_mcp_tool_interceptors`、`build_context_interceptor`、`get_session_pool` 与 `call_pooled_session_tool`，作用域和正常工具相同：服务、user/thread、事件循环。显式检查当前 Agent 能力、技能策略、配置的 AuthorizationProvider/GuardrailProvider；任何失败均不加载目录，不假称经过 ToolNode。
- 首轮工具被 deferred 时，使用框架 system 的 `available-deferred-tools` 判断是否属于当前 Agent；不会运行或晋升 `tool_search`。后续检索仍由模型正常发现并调用工具。自定义组装若不提供该框架声明，须让目录工具在 `request.tools` 可见，否则拒绝加载。
- 目录加载日志仅记录 `injected`、`injected_truncated` 或 `unavailable`；有宿主 `__run_journal` 时写入 `middleware:vssag_directory`，不记录目录/正文/凭据。总超时包含排队、配置、权限和 MCP；异步取消原样传播。
- Codex portable 布局：根 `plugin.json`、`mcp.json`、`skills/knowledge-retrieval/SKILL.md`，`hooks/hooks.json` 只声明 SessionStart；DeerFlow middleware 不注册进 Codex schema。
- 样例：根 `extensions_config.example.json` 包含默认禁用服务及接入说明；`mcp-extensions/vssag/extensions_config.example.json` 给出 Middleware、interceptor、服务三项合并片段（APP_ENV=dev）。根默认列表不主动导入可选包，不新增 `plugins:` 配置。
- stdio 接入指南只说明外置包安装、配置和验证，不启动其他服务；Gateway 沿用用户自己的启动方式。目录迁移后需重新安装可编辑基础包，更新 VSSAG_MCP_PYTHON/VSSAG_MCP_CONFIG，并重启 Gateway 读取新环境。手工 stdio 进程不能供 DeerFlow 通过 URL 连接或复用。

## 2、MCP 工具契约

| 原始工具 | 默认模型可见名 | 参数 |
| --- | --- | --- |
| `build_vfs` | `vssag_build_vfs` | 无模型参数 |
| `search` | `vssag_search` | query，top_k（1–200），strategy（keyword/vector/hybrid/graph） |
| `grep_file` | `vssag_grep_file` | mode（dirs/full/range/lines/heading/block），path，value |

身份、权限、thread/run、根目录不在工具参数中。目录返回 tree 字符串，检索/读取返回 SDK JSON 结构。失败变为 MCP `isError`，原链路生成关联原 `tool_call_id` 的 `ToolMessage`。

## 3、SDK 调用契约

```python
from vssag import SearchRequest, VFSRequest, VSSAGAccessContext, VSSAGClient

client = VSSAGClient.from_env()
context = VSSAGAccessContext(
    user_id=user_id,
    user_role=user_role,
    authz_attributes=authz_attributes,
    thread_id=thread_id,
    run_id=run_id,
)
tree = await client.build_vfs(context, mount_root=root)
hits = await client.search(SearchRequest(context=context, query=query))
result = await client.restore_tool(VFSRequest(context=context, mount_root=root, mode="full", path=path))
await client.close()
```

服务在线程初始化 client，生命周期内锁定串行访问。`root` 原样交给 SDK，不注册 Sandbox。保存结果是下一次独立 `write_file` 调用。

## 4、身份与部署边界

`request.runtime.context` → 外置 interceptor → `X-DeerFlow-VSSAG-Context` → 既有 stdio `_meta.headers` → SDK context。仅信任私有 stdio 父进程，不提供公共 HTTP 认证。元数据上限 16 KiB；缺失/非法时拒绝。Gateway 尚无共享 KB 授权属性生产者；默认 `{}` 依赖 SDK 实际 owner 等规则，不能伪造用户 grant。

安装、凭据和验收见 [部署指南](vssag-mcp.md)。最新状态见 [计划](../plans/2026-10-09-vssag-plugin-middleware.md) 与 [评审](../reviews/2026-10-09-vssag-plugin-middleware.md)。本文仅记录该集成入口，不替代其他模块 API 文档。

## 5、VFSSQL HTTP MCP 配置入口

`extensions_config.json -> mcpServers.vfssql` 通过现有 HTTP MCP adapter 和 `OAuthTokenManager` 接入独立服务，不增加 SDK 导入入口、middleware 或 interceptor。OAuth 采用 `token_url`、`grant_type=client_credentials`、`client_id`、`client_secret=$VFSSQL_OAUTH_CLIENT_SECRET`、`scope=vfssql:read`；当前 Keycloak 接受宿主既有 `client_secret_post` 表单。令牌获取、缓存及到期前重新申请均由宿主现有实现完成。

| 原始工具 | 默认模型可见名 | 业务参数 |
| --- | --- | --- |
| `vfs_tree_folder` | `vfssql_vfs_tree_folder` | path（可空）、show_summary |
| `vfs_find_file` | `vfssql_vfs_find_file` | folder_path、patterns、type 等 |
| `vfs_grep_file` | `vfssql_vfs_grep_file` | querys（必填数组）、paths、show_line、is_recursive、top_k |
| `vfs_cat_read` | `vfssql_vfs_cat_read` | file_path、show_line |
| `vfs_sed` | `vfssql_vfs_sed` | file_path、operation、行范围或替换预览参数 |
| `vfs_bash_execute` | `vfssql_vfs_bash_execute` | command；受限虚拟目录操作 |
| `sql_execute` | `vfssql_sql_execute` | sql、parameters、max_rows；服务控制是否允许执行 |
| `sql_learn` | `vfssql_sql_learn` | request；经授权提交待审核候选，服务控制是否启用 |

调用身份来自 Keycloak 后台 Client 的 Access Token，不从模型业务参数读取。MCP 服务校验 issuer/audience/scope，执行、学习与数据权限以服务当前配置和响应为准。完整地址必须包含 `/mcp`；默认挂载根为 `/home/vfssql`，单 Schema 时目录工具默认可展示其 Schema 根，其路径不属于 DeerFlow Sandbox。当前八工具契约以 2026-10-22 的真实发现为准，旧四工具不再调用。

服务当前 issuer 为 `http://127.0.0.1:8333/realms/mcp`，取令牌的 token endpoint 必须与该精确 issuer 对齐；向 localhost 请求时当前 Keycloak 会签发不同的 iss，MCP 将拒绝该令牌。

安装和验收见 [接入指南](vfssql-http-mcp.md)、[当前修复计划](../plans/2026-10-22-vfssql-discovery.md) 与 [当前评审](../reviews/2026-10-22-vfssql-discovery.md)。本集成未改变 Harness 的公共 Python API。
