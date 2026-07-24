# TableRAG MCP Tool Contract

## 唯一工具

`sqlrag_retrieve`

工具名必须严格等于 `sqlrag_retrieve`，不得添加 MCP Server 前缀。

所有调用都返回 `ok`、`operation` 和 `result`；失败时返回
`ok=false`、`error.type` 和 `error.message`。

## operation

### `hybrid-search`

- 输入：完整自然语言 `query`，以及可选 `*_top_k`、`join_max_hops`、`final_*_top_k`、`table_names` 和 `column_names`。
- 输出：包含 `evidences`、`tables`、`columns`、`values` 和 `join_graphs` 的完整结果对象。
- 普通自然语言转 SQL 场景默认首选。

### `search-evidences`

- 输入：`queries`，包含 1-8 个独立关键词或短语；可选 `evidence_top_k`。
- 用于补充业务规则、指标定义、SQL 约束、口径或标准术语。

### `search-tables`

- 输入：`queries`，包含 1-8 个独立关键词或短语；可选 `table_top_k`。
- 用于补充候选表。

### `search-columns`

- 输入：`queries`，包含 1-8 个独立关键词或短语；可选 `column_top_k`、`table_names` 和 `column_names`。
- 用于补充指标、维度、过滤字段和 Join Key。

### `search-values`

- 输入：`queries`，包含 1-8 个独立关键词或短语；可选 `value_top_k`、`table_names` 和 `column_names`。
- 用于真实字段值、别名、商品、客户、地区、状态和分类值对齐。

### `expand-join-graph`

- 输入：`table_names` 和可选 `join_max_hops`。
- 不传 `query` 或 `queries`。
- 用于生成多表 SQL 前补充可靠的关联路径。

## 参数约束

- `query` 只用于 `hybrid-search`。
- 四类 `search-*` 只使用 `queries`；每个元素会单独执行一次关键词召回，再由服务端融合去重。
- 不要把完整问题放入单个 `queries` 元素，也不要把多个概念拼成一个长字符串。
- MCP 不提供 raw 召回、索引校验、索引初始化或字段值同步方法。
