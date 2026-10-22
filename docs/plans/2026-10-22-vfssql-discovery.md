# VFSSQL 更新后工具发现修复计划

## 1、定位

- [X] 1.1 核对 VS Code 环境、实际扩展配置和升级后的 VFSSQL 服务配置。
- [X] 1.2 复现 OAuth 取令牌成功但 MCP 握手 401；对比两种 issuer，确认 localhost 与 127.0.0.1 不一致。
- [X] 1.3 从 dev 创建 fix/vfssql-oauth-issuer，并快进已有 HTTP 接入前置提交 55d88eda0；保留用户已暂存的 .env 和 config.dev.yaml。

## 2、修复

- [X] 2.1 让实际配置及配置示例的 token endpoint 对齐服务的精确 issuer，补全 VFSSQL 工具发现关键词。
- [X] 2.2 更新 live 验收及接入文档到当前八工具契约，不保留已删除工具的调用。

## 3、验证与交付

- [X] 3.1 通过现有 Gateway 环境完成真实工具发现和只读目录/文件检索；保留认证检查。
- [X] 3.2 通过实际 Gateway/模型对话确认发现恢复，不启动替代 Gateway 或修改远端服务。
- [X] 3.3 执行相关离线测试（289 passed）、live 验收（2 passed）、格式检查和评审；记录结果并提交本次文件，保留用户暂存修改。

## 初步证据

- 旧 token endpoint 使用 localhost:8333，Keycloak discovery 与 Access Token 的 iss 都是 localhost；当前 MCP 服务 mcp-server.yaml 的 issuer 是 127.0.0.1。
- 使用旧令牌初始化 MCP 返回 401 invalid_token；仅把取令牌地址对齐 127.0.0.1 后，初始化返回 200。audience 与 vfssql:read scope 均存在，密钥加载正常。
- DeerFlow 工具发现会跳过握手失败的服务并返回空工具列表，随后缓存空列表；不会注册 tool_search 或 VFSSQL 工具。
- 升级后的服务已删除旧 capabilities/search_sql_schema/sql_bash/grep_sql_schema，现提供六个 VFS 文件工具及 sql_execute/sql_learn，验收必须随契约更新。
- 首次远程 fetch 连接重置；已有 origin/dev 引用未领先本地 dev。
