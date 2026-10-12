# VFSSQL HTTP MCP 接入

DeerFlow 通过既有 HTTP MCP 适配器与 OAuth 客户端凭据流程连接独立 VFSSQL 服务。MCP 服务自己访问索引，DeerFlow 无需安装 VFSSQL SDK，也无需新增 middleware 或 interceptor。

## 1、地址与鉴权

| 配置 | 本机验收值 |
| --- | --- |
| MCP endpoint | `http://127.0.0.1:8765/mcp` |
| Keycloak issuer | `http://localhost:8333/realms/mcp` |
| Token endpoint | `http://localhost:8333/realms/mcp/protocol/openid-connect/token` |
| 后台 Client ID | `backend-mcp-server` |
| Grant | `client_credentials` |
| Scope | `vfssql:read` |
| Access Token audience | `mcp-api`，由 Keycloak Audience Mapper 写入 |

MCP 根地址 `/` 不提供协议接口，必须包含 `/mcp`。issuer 保持服务配置的精确地址，不能把 `localhost` 擅自替换成 `127.0.0.1`。跨机器部署使用实际 HTTPS 地址及 Host 设置。

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

扩展配置热更新由既有文件签名与缓存失效处理；编辑 JSON 不需要另建取令牌服务。HTTP MCP 调用不访问 Sandbox 的本地文件系统，`sql_bash` 只操作 VFSSQL 服务的虚拟目录。

## 3、工具与检索范围

| 模型可见工具 | 用途 |
| --- | --- |
| `vfssql_capabilities` | 查询虚拟根目录和服务能力 |
| `vfssql_search_sql_schema` | 检索表结构及字段值上下文 |
| `vfssql_sql_bash` | 在服务虚拟目录执行受限 `ls`、`tree`、`cat` 等读取 |
| `vfssql_grep_sql_schema` | 检索虚拟 Markdown 内容 |

推荐对话：`请用 VFSSQL 检索 <已索引表名> 的表结构，读取命中 desc.md，说明用途和字段，并引用完整虚拟路径。`

能力响应中的 `sql_execution: false` 表示服务不能执行 SQL 或返回数据库业务行；本集成验收的是索引检索和表结构读取。`data_authorization: not_enforced` 表示 VFSSQL 当前尚未实现客户端或表字段数据隔离。DeerFlow 用户不会自动映射为 Keycloak 主体；本配置统一使用后台 Client 的身份。

## 4、验收

显式 live 测试通过实际扩展配置加载凭据，驱动真实 DeerFlow ToolNode；Gateway 对话测试还调用真实模型和应用运行链路。运行方式见 `backend/tests/test_vfssql_mcp_live.py`，结果记录见 [Review](../reviews/2026-10-12-vfssql-http-mcp.md)。live 测试默认跳过，不进入离线 CI。

```powershell
Set-Location D:\A-PythonWork\AOpenGithub\deer-flow\backend
$env:DEER_FLOW_RUN_LIVE_TESTS = '1'
$env:VFSSQL_E2E_QUERY = '<真实已索引表名>'
uv run --no-sync --env-file ../.env --env-file ../.env.vfssql.local `
  python -m pytest tests/test_vfssql_mcp_live.py -q
```

完整登录对话验收额外需要 `VFSSQL_E2E_GATEWAY_URL`（如 `http://127.0.0.1:2026`）、`VFSSQL_E2E_EMAIL`、`VFSSQL_E2E_PASSWORD`、`VFSSQL_E2E_MODEL`。凭据只从环境变量获取，不提交到测试源码；缺少时仅跳过该项。该测试会创建当前账号的验收线程、调用真实模型并触发应用正常的记忆处理，应在测试环境显式执行。

应用每个模型回合会经过多个 middleware 节点；Gateway 对话沿用应用默认 `recursion_limit`，不要套用简化图的 12/24 步预算。HTTP 200 的 `runs/wait` 还可能包含失败运行的部分状态，验收必须核对对应 run 的 `status=success` 和真实工具消息。
