# VFSSQL 更新后工具发现问题

## 1、根因与证据

用户使用既有 `DeerFlow: Windows本地调试 Gateway（VFSSQL OAuth）` 启动，实际密钥、配置路径和启用状态均正确。问题发生在 MCP HTTP 握手之前的令牌验签：

| 项目 | 升级后的 MCP 服务 | 原 DeerFlow 配置 |
| --- | --- | --- |
| Keycloak issuer | `http://127.0.0.1:8333/realms/mcp` | 通过 `http://localhost:8333/.../token` 取令牌 |
| 实际 Token `iss` | 只接受上列精确值 | `http://localhost:8333/realms/mcp` |
| MCP initialize | 使用匹配令牌时 HTTP 200 | 使用原令牌时 HTTP 401 invalid_token |

Keycloak 当前依据请求 hostname 生成 issuer。localhost 和 127.0.0.1 虽到达同一 Keycloak，签发令牌的 iss 仍不同。原流程取令牌 HTTP 200，audience 含 mcp-api、scope 含 vfssql:read，但服务端正确拒绝了 issuer 不匹配的 Token。

宿主 `get_mcp_tools()` 按服务隔离跳过失败发现，返回空列表；缓存随后保留空列表，模型自然没有 VFSSQL 工具。提示词或重试查找无法弥补认证失败。未发现 VS Code 密钥加载故障，也不需要关闭 issuer 校验。

## 2、修复范围

- 实际 extensions_config.json 的 token_url 改为当前 issuer 下的 `http://127.0.0.1:8333/realms/mcp/protocol/openid-connect/token`；保留 Client、密钥环境引用、scope 与其他服务器配置。
- 保留用户 routing 关键词并补充 VFSSQL/vfssql 等服务名匹配词；旧关键词不包含服务名，认证恢复后会影响延迟工具的自动加载偏好。
- 同步默认禁用配置示例、接入指南、公共工具入口及 live 测试。服务已删除旧四工具；当前发现六个 vfs 文件工具、sql_execute 和 sql_learn。
- 文件工具业务结果检查 `status=success`，不再以旧 `ok` 字段和旧调用名验收。Gateway 测试按调用 ID 核对真实读取路径及最终引用，SQL 执行和知识提交不在本次诊断调用范围内。
- 未修改 Harness 生产 Python 代码、远端 VFSSQL 仓库、Keycloak 设置、用户的 Gateway 进程或用户已暂存的 .env/config.dev.yaml。

## 3、验证

1. 使用与 VS Code 相同的本地密钥文件、工作目录及项目 uv 环境，复现原发现结果为空。
2. 对比两种地址签发的 Token 元数据，确认只有 iss 不匹配；匹配地址下 MCP initialize 200。
3. 修复实际 JSON 后，DeerFlow 真实适配器发现全部八工具，真实 ToolNode 完成 tree、find、cat、grep、sed 和强制客户端缓存过期后的 Keycloak 重新取令牌。
4. 现有用户 Gateway 无需替代服务，配置文件签名变更触发重新发现。真实模型已成功执行四个目录与文件检索工具。
5. 相关 MCP 离线回归 289 passed；最终 live 验收 2 passed（158 秒），包含当前 Gateway 的真实账号模型对话和真实 ToolNode。模型运行状态 success，目录、查找、读取、grep 均成功，最终引用真实读取路径。Ruff check/format 与 git diff --check 通过。

首轮多步模型验收已完成目录、查找、读取，但多轮 tool_search 消耗预算后触发 `GraphRecursionError`，审计事件明确记录 100 步上限；这与工具发现恢复是两个独立结果。验收提示改为模型可见的完整工具名，并在单次测试请求中使用 256 步预算，不修改应用全局预算。

诊断状态和测试日志保存在被忽略的 backend/.deer-flow/vfssql-fix-20261022/，不保存 Access Token 或真实密钥。本轮没有重跑上次约 35 分钟且存在既有失败的全量基线；不能将相关回归通过表述为仓库全量测试通过。

## 4、版本与交付

从 dev 新建 fix/vfssql-oauth-issuer，快进复用尚未合并的前置接入提交 55d88eda0。首次 fetch 连接重置，已有 origin/dev 引用未领先本地 dev；本次只提交修复文件，不包含用户暂存内容，不合并或推送长期 dev 分支。
