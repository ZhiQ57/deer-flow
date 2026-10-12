# VFSSQL HTTP MCP 接入评审与验收

日期：2026-10-12（Asia/Shanghai）。分支：`feat/vfssql-http-mcp`。

## 1、实现结论

DeerFlow 已通过既有 Streamable HTTP MCP 和 OAuth 客户端凭据流程连接运行中的 VFSSQL 服务。实际扩展配置从 VS Code 的 `oauth.clientId` 改为 DeerFlow 的 `token_url`、`grant_type`、`client_id`、`client_secret`、`scope`；后台客户端为 `backend-mcp-server`，scope 为 `vfssql:read`，Access Token 的 audience 由 Keycloak Mapper 写入。

复用宿主 `OAuthTokenManager` 的取令牌、缓存、到期前重新申请与请求头注入。当前 Keycloak 接受宿主的 `client_secret_post` 表单。没有修改 Harness 的生产 Python 代码、公开 API 或远端 VFSSQL 仓库，也没有增加 SDK、middleware 或认证桥接服务。

实际 `extensions_config.json` 保留其他扩展配置；Client Secret 放在被 Git 忽略的 `.env.vfssql.local`，JSON 只保留环境变量引用，清除目标服务器的静态 Authorization 头。根 `.env` 的用户修改不纳入提交。

默认禁用示例、独立的 VS Code VFSSQL OAuth 调试入口、使用指南、公共入口文档和显式 live 测试已同步。

## 2、实际验收

| 验证 | 结果 |
| --- | --- |
| MCP 健康与 Keycloak discovery | 可达 |
| Keycloak 客户端凭据取令牌 | 成功 |
| DeerFlow 工具发现 | 发现四个预期工具，默认 `vfssql_` 前缀 |
| 真实 LangGraph ToolNode | capabilities、tree、search、cat、grep 全部成功；搜索命中非空表结构，读取非空 `desc.md` |
| 令牌生命周期 | 缓存内重复调用只请求一次；强制客户端缓存过期后再次请求真实 Keycloak 成功 |
| Gateway / Nginx 真实账号对话 | 登录、创建线程、真实模型选择工具与生成回答成功，run 状态为 `success` |
| 网页端对话 | 在用户已登录的 `http://localhost:2026` 页面提交；保持页面的 Ultra 模式与 `deepseek-v4-flash-0731-cloud` 模型；实际调用四个 VFSSQL 工具并显示最终回答 |

网页对话命中一张实际索引表，读取其 `desc.md`，显示 14 个字段、类型、主键与完整虚拟路径。额外检查持久化 run 状态与 ToolMessage：`tool_search`、`vfssql_capabilities`、`vfssql_search_sql_schema`、`vfssql_sql_bash`、两次 `vfssql_grep_sql_schema` 均为成功；最终回答正确区分已记载字段信息与未记载的关联。

截图、完整工具消息和业务内容仅保存在被 Git 忽略的 `backend/.deer-flow/vfssql-e2e/`，不进入源码提交。网页运行耗时约 1 分 36 秒。此前登录页未跳转的页面状态在用户登录后不再阻挡验收；本次未修改登录逻辑或 Nginx 配置。

## 3、测试与环境限制

| 测试/检查 | 结果 |
| --- | --- |
| 初始 MCP OAuth/配置/凭据离线基线 | 230 passed |
| MCP OAuth/配置/凭据/Headers/缓存/路由相关最终离线回归 | 289 passed、1 skipped |
| 最终 live 验收 | 2 passed，包含真实 ToolNode 和真实账号 Gateway 对话 |
| Ruff check、Ruff format --check、git diff --check | 通过 |
| 根配置示例与 VFSSQL 调试环境解析 | 通过；仅加载本地密钥文件时，应用仍正确解析根 `.env` 和实际扩展配置 |
| 全量离线基线 | 15,668 passed、150 failed、270 skipped、3 deselected；耗时约 35 分钟 |
| 严格 blocking-I/O 基线 | 122 passed、6 failed、2 skipped |

全量失败分布在 49 个现有测试文件，包括扩展依赖/管理、Lark CLI、网页相对链接、SubAgent 提示词、SQL executor 等；MCP 本次相关回归没有失败。严格 I/O 的六项失败涉及 Windows 文件权限断言、既有动态上下文超时断言和未安装的 Lark CLI。详细输出保存在本地忽略目录。不能将本次检索接入验收通过表述为整个仓库全量测试通过；没有为本接入修改这些无关模块。

联调时还发现并处理了两项验收环境问题：Windows 默认 ProactorEventLoop 不兼容 Psycopg 异步连接，按既有 VS Code 设置改为 SelectorEventLoop；一个已有内网模型不可达，API 验收使用可达的已配置模型。简化图的 24 步预算不足以覆盖完整 middleware 链，最终 Gateway 对话沿用应用默认运行步数。`runs/wait` 的 HTTP 200 不能替代 run 状态核对。

## 4、评审意见与交付状态

- 配置层接入保持原有认证与工具调用链，未削弱服务端 issuer、audience、scope 或有效期验证。
- live 测试显式 opt-in，读取真实凭据环境变量，不把 Client Secret、Token 或账号密码写入测试源码。检索为空、工具失败、run 失败或回答不引用真实命中路径均不能通过。
- `sql_execution=false`：当前验收范围为索引检索与表结构读取，不提供数据库业务行查询；文档中的行数是索引元数据。
- `data_authorization=not_enforced`：后台客户端共享索引访问权限，本次没有实现用户或表字段隔离。
- `client_credentials` 重新取令牌验收采用强制客户端缓存过期，仍调用真实 Keycloak；未宣称实际等待整个 JWT 生命周期。
- 保留用户已有 `.env`、`config.dev.yaml` 修改及用户运行的服务；本任务运行中的 Gateway 已加载本地 OAuth 密钥，方便继续查看验收对话。下一次 VS Code 启动使用新增 VFSSQL OAuth 入口。
- 第二次 `git fetch origin dev` 成功，本地 dev 未落后 origin/dev。接入变更使用中文规范提交到功能分支；因全量基线仍有失败，按“测试通过后合并”的仓库要求暂不合并或推送 origin/dev。
