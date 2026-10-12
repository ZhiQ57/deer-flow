# VFSSQL HTTP MCP 接入计划

目标：复用 DeerFlow 的 Streamable HTTP 与 OAuth 客户端凭据流程，连接已运行的 VFSSQL MCP，并以真实模型对话完成索引检索与表结构读取。

## 1、调研与基线

- [X] 1.1 阅读仓库、MCP、配置和测试模块指南，核对 VFSSQL 实现及既有开发记录。
- [X] 1.2 核对 `/mcp` 端点、Keycloak issuer、`backend-mcp-server` 与 `vfssql:read`；从本地 dev 建立 `feat/vfssql-http-mcp`。
- [X] 1.3 执行离线测试基线并核对现有应用启动配置。

## 2、配置接入

- [X] 2.1 修正实际 `extensions_config.json`，使用 DeerFlow 支持的 OAuth 字段和自动令牌更新，保留其他扩展配置。
- [X] 2.2 将客户端密钥保存在被 Git 忽略的本机配置中，仅引用环境变量，不保存固定 Access Token。
- [X] 2.3 补充默认禁用的配置示例和部署指南，说明只读检索工具的边界。

## 3、验收与评审

- [X] 3.1 通过 DeerFlow 的工具发现与真实 ToolNode 验证四个工具、表检索、读取和令牌重新获取。
- [X] 3.2 通过真实账号 Gateway 和已登录网页的实际对话验证工具选择、检索、读取及回答引用。
- [X] 3.3 完成相关离线回归、全量基线、格式检查与代码评审，记录实际结果和环境限制。

## 4、交付

- [X] 4.1 更新使用入口及 Review 文档，保留可复现的验收方法，确认凭据和临时结果不进入 Git。
- [X] 4.2 使用中文规范提交接入配置示例、验收测试、调试入口和文档，保留功能分支。
- [ ] 4.3 全量测试通过后合并本地 dev 并推送 origin/dev。全量基线存在 150 项失败、严格 I/O 基线存在 6 项失败，本轮不满足合并条件。

## 当前发现

实际 VFSSQL 配置沿用了 VS Code 的 `oauth.clientId` 字段；DeerFlow 使用 `token_url`、`grant_type`、`client_id`、`client_secret`、`scope`，已有自动缓存及到期前刷新能力。MCP `/healthz` 与 Keycloak discovery 可达；Gateway 当前未在 8001 监听。根 `.env` 已有用户修改且被 Git 跟踪，不能在其中新增真实密钥。本机现有 Python 环境为 3.12，符合项目 `requires-python >=3.12`，继续复用该 uv 环境。

首次 `git fetch origin dev` 因连接重置失败，第二次重试成功；本地 dev 比 origin/dev 多 2 个提交，无落后提交。

最终验收：相关 MCP 离线回归 289 passed / 1 skipped，live 测试 2 passed，网页实际运行成功并显示 14 个字段及完整引用路径。详见 [Review](../reviews/2026-10-12-vfssql-http-mcp.md)。
