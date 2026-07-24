"""TableRAG MCP Server。"""

from __future__ import annotations

from typing import Annotated, Any

try:
    from pydantic import Field
except ImportError:  # pragma: no cover - MCP extra installs pydantic

    def Field(**_: Any) -> None:
        """MCP 可选依赖缺失时的 schema 元数据占位。"""
        return None


from ..providers import EmbeddingProvider
from .service import TableRAGMCPOperation, TableRAGMCPService
from .settings import TableRAGMCPSettings

_INSTRUCTIONS = """
仅使用 sqlrag_retrieve。operation=hybrid-search 接收完整自然语言问题 query，执行查询解析、Evidence/表/列/字段值/Join Graph 多路召回、融合、重排序、Join Graph 表补全并组装最终上下文。
operation=search-evidences/search-tables/search-columns/search-values 必须接收 queries 列表；列表中的每个单关键词或短语会独立并行执行 BM25 等单路检索，再进行 RRF 与关键词覆盖率融合。operation=expand-join-graph 只接收 table_names。
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
        name="sqlrag_retrieve",
        description=(
            "统一 TableRAG 检索接口。hybrid-search 使用完整 query，执行查询解析、Evidence/表/列/字段值/Join Graph 多路召回、融合、重排序、Join Graph 表补全并组装最终 NL2SQL 上下文。"
            "search-evidences、search-tables、search-columns、search-values 必须使用 queries 列表；每个元素是一个独立关键词或短语，服务端会并行执行单关键词 BM25/模糊/向量检索后融合。"
            "不能把多个关键词拼成一个元素。expand-join-graph 使用 table_names。"
        ),
    )
    def sqlrag_retrieve(
        operation: Annotated[
            TableRAGMCPOperation,
            Field(description="检索方法：hybrid-search=完整问题混合检索；search-evidences/tables/columns/values=对应单路关键词并行检索；expand-join-graph=按候选表扩展关联路径。"),
        ] = TableRAGMCPOperation.HYBRID_SEARCH,
        query: Annotated[
            str | None,
            Field(description="仅用于 hybrid-search。填写完整自然语言问题，例如‘查询最近一个月华东区域销售额最高的客户’；不要用于 search-* 的关键词并行检索。"),
        ] = None,
        queries: Annotated[
            list[str] | None,
            Field(
                description=(
                    "仅用于 search-evidences/search-tables/search-columns/search-values。填写 1-8 个独立关键词或短语；"
                    "每个元素分别并行执行一次单关键词 BM25/模糊/向量召回，再按 RRF 与关键词覆盖率去重融合。"
                    "例如 [‘华东区域’, ‘销售额’, ‘客户’]。不要把完整问题或多个关键词合并到一个元素中。"
                ),
                min_length=1,
                max_length=8,
            ),
        ] = None,
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
        """执行 operation 指定的统一 TableRAG 操作。"""
        return service.execute(
            operation=operation,
            query=query,
            queries=queries,
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
