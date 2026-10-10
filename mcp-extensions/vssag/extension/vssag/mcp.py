"""Optional MCP server exposing VSSAG's generic retrieval operations."""

from __future__ import annotations

from .vssag_schemas.retriever.request import SearchRequest
from .vssag_schemas.sql import SQLFeedback, SQLScope, SQLToolRequest
from .vssag_schemas.vfs.access_context import VSSAGAccessContext
from .vssag_schemas.vfs.request import VFSRequest


def create_mcp_server(client):
    try:
        from fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("Install ownmindforge-vssag[mcp] to enable MCP") from exc

    server = FastMCP("vssag")

    @server.tool
    async def search(
        query: str,
        user_id: str,
        user_role: str,
        authz_attributes: dict,
        thread_id: str,
        run_id: str,
        top_k: int = 10,
    ) -> dict:
        response = await client.search(
            SearchRequest(
                query=query,
                context=VSSAGAccessContext(
                    user_id=user_id,
                    user_role=user_role,
                    authz_attributes=authz_attributes,
                    thread_id=thread_id,
                    run_id=run_id,
                ),
                top_k=top_k,
            )
        )
        return response.model_dump(mode="json")

    @server.tool
    async def grep_file(
        mode: str,
        path: str,
        user_id: str,
        user_role: str,
        authz_attributes: dict,
        thread_id: str,
        run_id: str,
        value: str | None = None,
        mount_root: str | None = None,
    ) -> dict:
        request = VFSRequest(
            mode=mode,
            path=path,
            value=value,
            context=VSSAGAccessContext(
                user_id=user_id,
                user_role=user_role,
                authz_attributes=authz_attributes,
                thread_id=thread_id,
                run_id=run_id,
            ),
            mount_root=mount_root,
        )
        response = await client.restore_tool(request)
        return response.model_dump(mode="json")

    @server.tool
    async def search_sql_schema(
        query: str,
        top_k: int = 20,
        include_values: bool = True,
        include_evidence: bool = True,
        include_graph: bool = True,
        user_id: str = "anonymous",
        schema_names: list[str] | None = None,
        table_names: list[str] | None = None,
        excluded_columns: dict[str, list[str]] | None = None,
    ) -> dict:
        response = await client.search_sql_schema(
            query,
            top_k=top_k,
            include_values=include_values,
            include_evidence=include_evidence,
            include_graph=include_graph,
            user_id=user_id,
            schema_names=schema_names,
            table_names=table_names,
            excluded_columns=excluded_columns,
        )
        return response.model_dump(mode="json")

    @server.tool
    async def grep_sql_schema(
        mode: str,
        path: str,
        value: str | None = None,
        user_id: str = "anonymous",
        schema_names: list[str] | None = None,
        table_names: list[str] | None = None,
        excluded_columns: dict[str, list[str]] | None = None,
    ) -> dict:
        response = await client.restore_sql_tool(
            SQLToolRequest(
                mode=mode,
                path=path,
                value=value,
                scope=SQLScope(
                    user_id=user_id,
                    schema_names=schema_names or [],
                    table_names=table_names or [],
                    excluded_columns=excluded_columns or {},
                ),
            )
        )
        return response.model_dump(mode="json")

    @server.tool
    async def record_sql_feedback(
        query: str,
        generated_sql: str = "",
        execution_result: str = "",
        used_tables: list[str] | None = None,
        join_conditions: list[str] | None = None,
        confidence: float = 0.85,
    ) -> dict:
        edges = await client.record_sql_feedback(
            SQLFeedback(
                query=query,
                generated_sql=generated_sql,
                execution_result=execution_result,
                used_tables=used_tables or [],
                join_conditions=join_conditions or [],
                confidence=confidence,
            )
        )
        return {"edges": [edge.model_dump(mode="json") for edge in edges]}

    return server
