# TableRAG MCP Tool Contract

## Primary Retrieval

`tablerag_retrieve`

- Input: `query` plus optional `*_top_k`, `join_max_hops`, `final_*_top_k`, `table_names`, and `column_names`.
- Output: `ok`, `operation`, and `result`.
- Use `result.evidences`, `result.tables`, `result.columns`, `result.values`, and `result.join_graphs` together as SQL-generation context.

`tablerag_raw_retrieve`

- Input: `query`, optional `schema_query`, and top-k options.
- Output: raw multi-route recall without query parsing or reranking.
- Use only for diagnostics or when the caller explicitly wants pre-rerank evidence.

## Single-Route Retrieval

`tablerag_search_evidences`

- Use for business rules, metric definitions, SQL constraints,口径, or canonical terminology.

`tablerag_search_tables`

- Use for candidate table discovery.

`tablerag_search_columns`

- Use for candidate metric, dimension, filter, and key columns.
- Pass `table_names` when table scope is already known.

`tablerag_search_values`

- Use for field-value alignment, such as user text values, aliases, product names, customer names, regions, statuses, and categories.
- Pass `table_names` or `column_names` when scope is known.

`tablerag_expand_join_graph`

- Input: `table_names` and optional `join_max_hops`.
- Use before drafting multi-table SQL.

## Admin Tools

`tablerag_validate_index`

- Safe health check for extensions, index tables, indexes, and schema version.

`tablerag_initialize_indexes`

- Mutating operation. Only call when explicitly requested and enabled by server operator.

`tablerag_sync_field_values`

- Mutating operation. Only call when explicitly requested and enabled by server operator.

## Error Handling

All tools return `ok=false` with `error.type` and `error.message` on failure. If retrieval fails because configuration or PostgreSQL capabilities are missing, call `tablerag_validate_index` before suggesting SQL.
