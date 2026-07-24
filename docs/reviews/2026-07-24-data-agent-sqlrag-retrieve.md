# DataAgent SQLRAG 单工具适配评审

## 结论

DataAgent 已迁移到新版 TableRAG MCP 单工具合同。正式 Gateway、嵌入式客户端和实验性 DataAgent 都只接受名称严格等于 `sqlrag_retrieve` 的 MCP 工具；旧十工具合同、带 Server 前缀的名称和重复同名工具均 fail closed。

## 合同核对

| operation | 参数合同 | DataAgent 处理 |
|---|---|---|
| `hybrid-search` | 完整自然语言 `query` | 登记五类结果集合和 query |
| `search-evidences` | 1–8 个独立关键词组成的 `queries` | 数组归档到 `evidences` |
| `search-tables` | 1–8 个独立关键词组成的 `queries` | 数组归档到 `tables` |
| `search-columns` | `queries`，可附表列范围 | 数组归档到 `columns` |
| `search-values` | `queries`，可附表列范围 | 数组归档到 `values` |
| `expand-join-graph` | `table_names` | 数组归档到 `join_graphs` |

raw 召回、索引校验、索引初始化和字段值同步已从工具、提示词、Skill、示例配置和状态判断中移除。

## 设计评审

- MCP Server 新增 `tool_name_prefix`，默认值为 `true`，所以其他 Server 的现有工具名保持不变。
- TableRAG 示例配置显式设置 `tool_name_prefix=false`，模型看到的名称为 `sqlrag_retrieve`，不会生成 `tablerag_sqlrag_retrieve`。
- MCP 加载器按 Server 独立传递前缀开关；无前缀 stdio 工具仍通过 DeerFlow 持久会话池执行，没有退化成每次重建进程。
- `DataAgentServiceAbility.filter_tools()` 在授权前后各执行一次：首次删除旧合同，第二次确认授权或 Skill 策略没有移除唯一检索工具。
- Gateway Lead Agent 与 `DeerFlowClient` 使用同一过滤规则，避免 HTTP 与嵌入式运行路径漂移。
- `TableRagStageMiddleware` 和 service state 再次校验精确工具名与六种 operation，不能通过直接调用状态辅助函数绕过合同。
- 同一模型响应中的多个 `sqlrag_retrieve` 调用只保留第一个，补充检索必须在看到上一条结果后串行执行。
- 多次检索合并时保留 `operations`、`queries`、`keyword_queries` 和统一 registry，检索摘要包含这些字段。
- 数据源绑定不再读取已废弃的 TableRAG 业务源 DSN，只读取索引 DSN；真实 SQL 执行 DSN 仍由 DataAgent service ability 单独管理。

## 范围边界

本次提交只适配 DeerFlow。新版 TableRAG 实现位于外部仓库 `D:\A-AICodeWork\TableRAG`，没有把该仓库的源码批量复制到 DeerFlow 的 vendored `backend/packages/harness/table_rag`。如果部署依赖 DeerFlow 内置的 vendored TableRAG，而不是已升级的外部包，需要单独同步对应版本后再做真实 MCP 联调。

## 验证结果

- SQLRAG 适配与嵌入式客户端定向测试：15 passed。
- MCP 配置、缓存、拦截器、OAuth、路由、同步包装、名称校验和无前缀会话池测试：119 passed。
- 公共 Skill 测试：51 passed。
- 本次修改 Python 文件 Ruff check/format：通过。
- 本次修改 Python 文件编译检查：通过。
- `git diff --check`：通过。
- 本地忽略配置检查：`tool_name_prefix=false`，保留索引 DSN，已移除 source DSN、初始化开关和字段值同步开关。

## 已知基线问题

- `tests/test_mcp_session_pool.py` 在 Windows 上有 2 个既有平台断言失败：POSIX `0o700` 权限位和使用 `/` 的 `.endswith(".mcp/tmp")`。本次新增的无前缀会话池测试通过，失败断言所在实现未修改。
- `tests/test_mcp_file_migration.py` 在 Windows 上有 15 个既有 POSIX 路径/URI 断言失败；该测试文件和相关路径转换代码不在本次 diff 中。
- 正式 DataAgent 旧测试文件在当前 `dev` 基线存在收集错误：缺少 `resolve_service_ability` 和 `DataAgentTurnResetMiddleware`。本次新增适配测试使用现有可运行入口验证单工具合同，没有扩大范围修复这些历史缺口。

## 评审结论

变更满足“单一工具、无冗余前缀、六种只读 operation、减少模型工具 Schema 上下文”的目标。默认 MCP 行为兼容，DataAgent 对合同漂移采用 fail-closed，适合合并到 `dev`。
