# VFSSQL HTTP MCP 接入

DeerFlow 通过既有 HTTP MCP 适配器与 OAuth 客户端凭据流程连接独立 VFSSQL 服务。MCP 服务自己访问索引，DeerFlow 无需安装 VFSSQL SDK，也无需新增 middleware 或 interceptor。

## 1、地址与鉴权

| 配置 | 本机验收值 |
| --- | --- |
| MCP endpoint | `http://127.0.0.1:8765/mcp` |
| Keycloak issuer | `http://127.0.0.1:8333/realms/mcp` |
| Token endpoint | `http://127.0.0.1:8333/realms/mcp/protocol/openid-connect/token` |
| 后台 Client ID | `backend-mcp-server` |
| Grant | `client_credentials` |
| Scope | `vfssql:read` |
| Access Token audience | `mcp-api`，由 Keycloak Audience Mapper 写入 |

MCP 根地址 `/` 不提供协议接口，必须包含 `/mcp`。Token endpoint 必须取自服务当前 issuer 对应的 OIDC discovery；升级后重新核对服务的 `mcp-server.yaml -> issuer`，不能把 `localhost` 和 `127.0.0.1` 当成同一个 issuer。Keycloak 当前会依据请求 hostname 签发不同的 `iss`：取令牌成功不代表令牌能通过 MCP 验签。跨机器部署使用实际 HTTPS 地址及 Host 设置。

根 `extensions_config.example.json` 提供默认禁用的 `mcpServers.vfssql`。将该项合并到实际 `extensions_config.json` 并设置 `enabled: true`，保留其他扩展项。DeerFlow 使用 `oauth.token_url`、`grant_type`、`client_id`、`client_secret`、`scope`；VS Code 的 `oauth.clientId` 不能用于 DeerFlow 后台取令牌。

`client_secret` 引用 `$VFSSQL_OAUTH_CLIENT_SECRET`。进程启动前设置该环境变量；本机可以保存到被 Git 忽略的 `.env.vfssql.local`，并通过 uv 的 `--env-file` 加载。不要把 Client Secret 放入 MCP Bearer 头，也不要把短期 Access Token 固定写入配置。

`OAuthTokenManager` 向 Keycloak 发送 `client_secret_post` 表单，缓存 Access Token，并在到期前 60 秒重新申请。当前 Keycloak 客户端接受此方式。服务端继续校验 issuer、audience、有效期和 scope；DeerFlow 不覆盖这些验证。

## 2、Windows Gateway 启动

现有 Windows uv 环境满足本项目的 Python 3.12 要求。先确保 VFSSQL 与 Keycloak 已启动，再从 PowerShell 执行：

```powershell
Set-Location D:\A-PythonWork\AOpenGithub\deer-flow\backend
uv run --no-sync --env-file ../.env --env-file ../.env.vfssql.local `
  python -m uvicorn app.gateway.app:app `
  --host 127.0.0.1 --port 8001 --loop asyncio:SelectorEventLoop
```

使用 PostgreSQL 异步驱动时需要 SelectorEventLoop，默认 Windows ProactorEventLoop 会导致 Psycopg 连接失败。VS Code 选择 `DeerFlow: Windows本地调试 Gateway（VFSSQL OAuth）`，该入口从 `.env.vfssql.local` 加载 MCP 密钥；应用配置模块继续通过既有 `load_dotenv()` 加载根 `.env` 的常规配置。新环境变量需要重启 Gateway 才会进入进程。

扩展配置热更新由既有文件签名与缓存失效处理；编辑 JSON 后新对话会重新发现工具，不需要另建取令牌服务。HTTP MCP 文件调用只操作 VFSSQL 服务的虚拟目录。

## 3、工具与检索范围

| 模型可见工具 | 用途 |
| --- | --- |
| `vfssql_vfs_tree_folder` | 查看目录和可选目录摘要 |
| `vfssql_vfs_find_file` | 按名称 glob、类型与更新时间查找条目 |
| `vfssql_vfs_grep_file` | 按 `querys` 普通文本数组检索正文，可用 `paths` 缩小范围 |
| `vfssql_vfs_cat_read` | 通过 `file_path` 读取原文 |
| `vfssql_vfs_sed` | 读取行范围或查看替换预览，不写回文件 |
| `vfssql_vfs_bash_execute` | 受限虚拟 Bash 操作补充 |
| `vfssql_sql_execute` | 受控只读 SQL 执行，是否启用以服务配置和实际响应为准 |
| `vfssql_sql_learn` | 提交待审核知识候选，须有用户授权且服务已开启学习 |

推荐对话：`请用 VFSSQL 查看目录，用 vfs_find_file 找到 <已索引表名>，读取其 desc.md，并用 vfs_grep_file 核对字段。引用完整虚拟路径，不执行 SQL，不提交知识候选。`

截至 2026-10-22，服务提供上述八个工具，旧 `capabilities/search_sql_schema/sql_bash/grep_sql_schema` 已删除，客户端不得继续调用旧名。本集成诊断仅执行目录和文件读取，不测试 SQL 执行或提交知识候选。SQL 执行与学习能力、数据权限以当前服务实际配置和工具响应为准；索引行数不等于实时业务查询结果。DeerFlow 用户不会自动映射为 Keycloak 主体，本配置统一使用后台 Client 的身份。

## 4、验收

显式 live 测试通过实际扩展配置加载凭据，验证八工具发现并驱动真实 DeerFlow ToolNode；Gateway 对话测试还调用真实模型和应用运行链路。运行方式见 `backend/tests/test_vfssql_mcp_live.py`，新版发现修复见 [Review](../reviews/2026-10-22-vfssql-discovery.md)。2026-10-12 的历史验收对应旧四工具，不能作为升级后验收依据。live 测试默认跳过，不进入离线 CI。

```powershell
Set-Location D:\A-PythonWork\AOpenGithub\deer-flow\backend
$env:DEER_FLOW_RUN_LIVE_TESTS = '1'
$env:VFSSQL_E2E_QUERY = '<真实已索引表名>'
uv run --no-sync --env-file ../.env --env-file ../.env.vfssql.local `
  python -m pytest tests/test_vfssql_mcp_live.py -q
```

完整登录对话验收额外需要 `VFSSQL_E2E_GATEWAY_URL`（如 `http://127.0.0.1:2026`）、`VFSSQL_E2E_EMAIL`、`VFSSQL_E2E_PASSWORD`、`VFSSQL_E2E_MODEL`。凭据只从环境变量获取，不提交到测试源码；缺少时仅跳过该项。该测试会创建当前账号的验收线程、调用真实模型并触发应用正常的记忆处理，应在测试环境显式执行。

应用每个模型回合会经过多个 middleware 节点，不要套用简化图的 12/24 步预算。本文件的多步 live 对话在单次请求中使用 `recursion_limit=256`，不修改应用全局预算。HTTP 200 的 `runs/wait` 还可能包含失败运行的部分状态，验收必须核对对应 run 的 `status=success` 和真实工具消息。

## 5、取令牌成功但模型没有工具

先检查 MCP 握手的 HTTP 状态。issuer 不一致时，Keycloak 仍可返回 200 并签发 Token，但 MCP 返回 401 invalid_token；DeerFlow 按服务隔离跳过失败服务，工具列表为空，并缓存该结果。此时无法通过提示词让模型发现工具。

对齐 token endpoint 后，保存实际 `extensions_config.json` 以触发缓存失效，再开新对话验证。若还没有工具，查看 Gateway 日志中的 `Skipping MCP server 'vfssql' after tool discovery failed` 及其底层 HTTP 状态。MCP routing 关键词应包含 `VFSSQL`，方便以服务名查询时自动加载延迟工具；这项偏好不能替代认证成功。
