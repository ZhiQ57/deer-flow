---
name: table-rag-agent
description: Use when Codex or another LLM agent needs TableRAG NL2SQL/Text2SQL context through the TableRAG MCP server before generating, reviewing, debugging, or explaining SQL; selecting candidate tables/columns; resolving user-mentioned field values; or using Evidence and Join Graph retrieval results.
---

# TableRAG Agent

## Workflow

Use the TableRAG MCP server before SQL generation whenever the user asks a data question and the relevant schema, business rule, field value, or join path is not already certain.

1. Call `tablerag_retrieve` first for normal NL2SQL work.
2. Read `result.evidences` as business rules and SQL-generation constraints.
3. Read `result.tables` as candidate tables, not guaranteed final truth.
4. Read `result.columns` as candidate metrics, dimensions, filters, and join keys.
5. Read `result.values` to align user phrases with real database values.
6. Read `result.join_graphs` for join paths before drafting multi-table SQL.
7. Generate SQL only after explaining which retrieved evidence, tables, columns, values, and joins you used.

## Tool Selection

- Use `tablerag_retrieve` for complete context with query parsing and reranking.
- Use `tablerag_raw_retrieve` when debugging recall quality or comparing raw multi-route results.
- Use `tablerag_search_evidences` when the question is mainly about business definitions,口径, constraints, or metric rules.
- Use `tablerag_search_tables` when only candidate tables are needed.
- Use `tablerag_search_columns` when table choice is known but metric/filter fields are unclear.
- Use `tablerag_search_values` when the user mentions real-world entities, regions, product names, customer names, statuses, or aliases.
- Use `tablerag_expand_join_graph` when candidate tables are known and join paths are needed.
- Use `tablerag_validate_index` for health checks before debugging retrieval failures.

## Safety Rules

- Do not invent tables, columns, field values, joins, or Evidence that the MCP result did not return.
- Treat low-score results as hints; ask for confirmation or run narrower tools when retrieval is ambiguous.
- Do not call `tablerag_initialize_indexes` or `tablerag_sync_field_values` unless the user explicitly asks for index administration or synchronization.
- Do not bypass the MCP server to connect directly to the database unless the user explicitly asks for backend debugging.
- Preserve sensitive DSNs and connection details; never include them in user-facing SQL reasoning.

## References

Read `references/mcp-tool-contract.md` when you need exact tool inputs, expected result fields, or fallback behavior.
