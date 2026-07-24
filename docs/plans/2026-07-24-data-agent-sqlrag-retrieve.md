# DataAgent SQLRAG 单工具适配计划

## 目标

按照新版 TableRAG MCP 检索合同，把正式与实验性 DataAgent 从旧的多工具接口迁移到唯一工具 `sqlrag_retrieve`，并通过 `operation` 选择六种只读检索能力，减少模型工具 Schema 上下文。

## 约束

- MCP 工具名必须严格等于 `sqlrag_retrieve`，不得接受 Server 前缀或旧工具名。
- `hybrid-search` 只使用完整自然语言 `query`。
- 四类 `search-*` 只使用 1–8 个独立关键词或短语组成的 `queries`。
- `expand-join-graph` 只使用 `table_names`。
- raw 召回、索引校验、索引初始化和字段值同步不再进入 DataAgent 工具面。
- 默认 MCP Server 行为保持向后兼容；只有显式配置 `tool_name_prefix=false` 的 Server 才关闭前缀。
- 最新 `dev` 已通过提交 `397aca05` 同步 vendored TableRAG 单工具实现；本分支负责 DeerFlow/DataAgent 适配，不重复复制外部仓库源码。

## TODO

### 1. 开发准备

- [X] 1.1 检查 `dev`、工作区和现有 DataAgent/MCP 实现。
- [X] 1.2 从本地最新 `dev` 创建 `refactor/data-agent-sqlrag-retrieve` 分支。
- [X] 1.3 阅读新版 TableRAG MCP 示例、工具签名和统一返回结构。
- [X] 1.4 开发期间 `dev` 前进后重新合并最新 `dev`，纳入 vendored TableRAG 单工具实现。

### 2. MCP 工具名适配

- [X] 2.1 为 MCP Server 配置增加默认开启的 `tool_name_prefix` 字段。
- [X] 2.2 按 Server 独立创建 MCP 客户端，并把关闭前缀的 stdio 工具继续接入持久会话池。
- [X] 2.3 更新 Gateway 配置响应、示例配置和 MCP 使用文档。
- [X] 2.4 将本地 TableRAG Server 配置为 `tool_name_prefix=false`，移除已废弃环境变量。

### 3. 正式 DataAgent

- [X] 3.1 建立唯一工具名、六种 operation 和单路结果集合映射。
- [X] 3.2 在 Gateway Lead Agent 与嵌入式 `DeerFlowClient` 中过滤旧工具、前缀工具和重复同名工具。
- [X] 3.3 工具缺失、重复或被授权策略移除时 fail closed。
- [X] 3.4 更新 TableRAG middleware，只登记精确工具名并串行化同一模型响应中的检索调用。
- [X] 3.5 在 service state 中登记 operation、关键词列表、合并 operation 和统一 Evidence registry。
- [X] 3.6 数据源绑定只读取 TableRAG 索引 DSN。

### 4. 实验性 DataAgent 与 Skill

- [X] 4.1 删除旧多工具常量、后缀匹配和管理工具白名单。
- [X] 4.2 更新工具过滤、检索成功判断、状态摘要、调试事件和测试数据。
- [X] 4.3 更新 DataAgent 提示词、SOUL、公共 Skill 和 MCP 合同说明。

### 5. 验证与交付

- [X] 5.1 增加 SQLRAG 适配单元测试，覆盖精确名称、六种 operation、状态投影和 fail-closed。
- [X] 5.2 增加无前缀 stdio MCP 工具仍使用会话池的回归测试。
- [X] 5.3 运行 Ruff、Python 编译、DataAgent 定向测试、MCP 定向测试和公共 Skill 测试。
- [X] 5.4 复核 Windows 平台既有失败与 `dev` 基线收集错误，确认不由本次修改引入。
- [X] 5.5 更新 README、后端架构说明、公共 API 指南、计划和评审文档。
