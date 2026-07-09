"""TableRAG MCP Server。"""

from __future__ import annotations

from typing import Any

from ..providers import EmbeddingProvider
from .service import TableRAGMCPService
from .settings import TableRAGMCPSettings


_INSTRUCTIONS = """
Use TableRAG to retrieve NL2SQL context before generating SQL. Prefer tablerag_retrieve for
full Evidence, table, column, value, and Join Graph context. Use single-route tools only when
debugging or when the caller explicitly needs a narrower retrieval channel. Mutating tools are
disabled unless the server operator enables their environment flags.
""".strip()


def create_mcp_server(
    settings: TableRAGMCPSettings | None = None,
    embedding_provider: EmbeddingProvider | None = None,
):
    """创建 FastMCP 服务实例。

    Args:
        settings: 可选 MCP 服务配置；为空时从环境变量读取。
        embedding_provider: 可选外部向量服务，由业务或脚本层注入。

    Returns:
        FastMCP 服务实例。
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("TableRAG MCP server requires mcp. Install with: pip install 'table-rag[mcp]'") from exc

    resolved_settings = settings or TableRAGMCPSettings.from_env()
    service = TableRAGMCPService(resolved_settings, embedding_provider=embedding_provider)
    mcp = FastMCP(
        "TableRAG",
        instructions=_INSTRUCTIONS,
        debug=resolved_settings.debug,
        log_level=resolved_settings.log_level,
        host=resolved_settings.host,
        port=resolved_settings.port,
        streamable_http_path=resolved_settings.streamable_http_path,
        sse_path=resolved_settings.sse_path,
        message_path=resolved_settings.message_path,
        json_response=resolved_settings.json_response,
        stateless_http=resolved_settings.stateless_http,
    )

    @mcp.tool(
        name="tablerag_retrieve",
        description="Run the full TableRAG NL2SQL retrieval pipeline and return Evidence, tables, columns, values, and Join Graph context.",
    )
    def tablerag_retrieve(
        query: str,
        evidence_top_k: int = 5,
        table_top_k: int = 10,
        column_top_k: int = 20,
        value_top_k: int = 5,
        join_max_hops: int = 2,
        final_table_top_k: int = 10,
        final_column_top_k: int = 20,
        table_names: list[str] | None = None,
        column_names: list[str] | None = None,
    ) -> dict[str, Any]:
        """执行完整 TableRAG 混合检索。"""
        return service.retrieve(
            query=query,
            evidence_top_k=evidence_top_k,
            table_top_k=table_top_k,
            column_top_k=column_top_k,
            value_top_k=value_top_k,
            join_max_hops=join_max_hops,
            final_table_top_k=final_table_top_k,
            final_column_top_k=final_column_top_k,
            table_names=table_names,
            column_names=column_names,
        )

    @mcp.tool(
        name="tablerag_raw_retrieve",
        description="Run raw multi-route TableRAG retrieval without query parsing or reranking.",
    )
    def tablerag_raw_retrieve(
        query: str,
        schema_query: str | None = None,
        evidence_top_k: int = 5,
        table_top_k: int = 10,
        column_top_k: int = 20,
        value_top_k: int = 5,
        join_max_hops: int = 2,
        table_names: list[str] | None = None,
        column_names: list[str] | None = None,
    ) -> dict[str, Any]:
        """执行 raw 混合召回。"""
        return service.raw_retrieve(
            query=query,
            schema_query=schema_query,
            evidence_top_k=evidence_top_k,
            table_top_k=table_top_k,
            column_top_k=column_top_k,
            value_top_k=value_top_k,
            join_max_hops=join_max_hops,
            table_names=table_names,
            column_names=column_names,
        )

    @mcp.tool(name="tablerag_search_evidences", description="Search business Evidence and SQL-generation rules.")
    def tablerag_search_evidences(query: str, evidence_top_k: int = 5) -> dict[str, Any]:
        """执行 Evidence 单路召回。"""
        return service.search_evidences(query=query, evidence_top_k=evidence_top_k)

    @mcp.tool(name="tablerag_search_tables", description="Search candidate tables from the TableRAG table index.")
    def tablerag_search_tables(query: str, table_top_k: int = 10) -> dict[str, Any]:
        """执行表结构单路召回。"""
        return service.search_tables(query=query, table_top_k=table_top_k)

    @mcp.tool(name="tablerag_search_columns", description="Search candidate columns from the TableRAG column index.")
    def tablerag_search_columns(
        query: str,
        column_top_k: int = 20,
        table_names: list[str] | None = None,
        column_names: list[str] | None = None,
    ) -> dict[str, Any]:
        """执行列字段单路召回。"""
        return service.search_columns(
            query=query,
            column_top_k=column_top_k,
            table_names=table_names,
            column_names=column_names,
        )

    @mcp.tool(name="tablerag_search_values", description="Search real field values from the TableRAG value index.")
    def tablerag_search_values(
        query: str,
        value_top_k: int = 5,
        table_names: list[str] | None = None,
        column_names: list[str] | None = None,
    ) -> dict[str, Any]:
        """执行字段值单路召回。"""
        return service.search_values(
            query=query,
            value_top_k=value_top_k,
            table_names=table_names,
            column_names=column_names,
        )

    @mcp.tool(name="tablerag_expand_join_graph", description="Expand Join Graph paths for candidate tables.")
    def tablerag_expand_join_graph(table_names: list[str], join_max_hops: int = 2) -> dict[str, Any]:
        """根据候选表扩展 Join Graph。"""
        return service.expand_join_graph(table_names=table_names, join_max_hops=join_max_hops)

    @mcp.tool(name="tablerag_validate_index", description="Validate TableRAG index connection, extensions, indexes, and schema version.")
    def tablerag_validate_index() -> dict[str, Any]:
        """校验索引结构。"""
        return service.validate_index()

    @mcp.tool(name="tablerag_initialize_indexes", description="Initialize TableRAG index tables and database indexes when explicitly enabled.")
    def tablerag_initialize_indexes() -> dict[str, Any]:
        """显式初始化索引结构。"""
        return service.initialize_indexes()

    @mcp.tool(name="tablerag_sync_field_values", description="Synchronize configured field-value indexes when explicitly enabled.")
    def tablerag_sync_field_values() -> dict[str, Any]:
        """同步字段值索引。"""
        return service.sync_field_values()

    return mcp


def run_server(
    settings: TableRAGMCPSettings | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> None:
    """启动 MCP 服务。"""
    resolved_settings = settings or TableRAGMCPSettings.from_env()
    server = create_mcp_server(resolved_settings, embedding_provider=embedding_provider)
    mount_path = resolved_settings.mount_path if resolved_settings.mount_path != "/" else None
    server.run(transport=resolved_settings.transport, mount_path=mount_path)
