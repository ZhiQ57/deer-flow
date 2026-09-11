# sql-execute MCP Server

这是独立的只读 SQL MCP Server。工具名为 `sql_execute`，模型只需传入：

```json
{"sql": "SELECT * FROM text2sql.orders LIMIT 10"}
```

工具返回：

```json
{
  "ok": true,
  "sql": "SELECT * FROM text2sql.orders LIMIT 10",
  "columns": ["id"],
  "rows": [{"id": 1}],
  "row_count": 1,
  "returned_row_count": 1,
  "truncated": false,
  "empty": false,
  "content": "当前执行SQL为:\nSELECT * FROM text2sql.orders LIMIT 10\nSQL结果为:\n{\"columns\":[\"id\"],\"rows\":[{\"id\":1}]}"
}
```

`rows` 是服务端数据库游标读取并经过 JSON 安全转换后的真实查询行，不是只包含
行数、列数或数据量的二次摘要。SQL 预算和只读边界属于 MCP Server 配置，不属于
DeerFlow AgentLoop。
