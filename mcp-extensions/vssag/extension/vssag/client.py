"""Public VSSAG client facade."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any, Callable, Mapping

from .config import VSSAGConfig
from .configs.model_config import ModelProviderConfig
from .cdc.field_value_cdc_runtime import SQLFieldValueCDCRuntime
from .cdc.kafka_stream import KafkaFieldValueStream
from .cdc.mysql_binlog import MySQLBinlogFieldValueSource
from .cdc.postgres_wal import PostgresWALFieldValueSource
from .cdc.redis_stream import RedisFieldValueStream
from .errors import ConfigurationError, RetrievalError
from .indexing.index_pipeline import IndexPipeline
from .indexing.sql_pipeline import SQLSchemaIndexPipeline
from .indexing.index_stores.lancedb_sql_schema_store import LanceDBSQLSchemaStore
from .indexing.index_stores.lancedb_store import LanceDBIndexStore
from .indexing.index_stores.memory_store import MemoryIndexStore
from .indexing.index_stores.pgvector_store import PgVectorIndexStore
from .indexing.index_stores.sql_schema_store import SQLSchemaStore
from .parsing.parse_pipeline import DocumentParser
from .providers.embedder_prov import OpenAIEmbedder
from .providers.llm_prov import OpenAILLMProvider
from .providers.rerank_prov import DashScopeReranker, VolcengineReranker
from .retrieval.hybrid_pipeline import HybridRetriever
from .retrieval.sql.search import SQLSchemaRetriever
from .traces.tracing import TraceRecorder
from .providers.sql_prov.mysql import MySQLSchemaProvider
from .providers.sql_prov.postgres import PostgreSQLSchemaProvider
from .providers.sql_prov.sqlite import SQLiteSchemaProvider
from .vfs.vfsrestore.sql_service import SQLVFSRestoreService
from .vfs.vfsrestore.service import VFSRestoreService
from .vssag_schemas.index.content import ContentRecord
from .vssag_schemas.parse.document import ParsedDocument
from .vssag_schemas.retriever.request import SearchRequest
from .vssag_schemas.retriever.response import SearchResponse
from .vssag_schemas.sql import (
    SQLFeedback,
    SQLEvidence,
    SQLEvidenceCandidate,
    SQLFieldValueAnnotation,
    SQLSchemaCollection,
    SQLScope,
    SQLSearchResponse,
    SQLToolRequest,
    SQLToolResponse,
)
from .vssag_schemas.vfs.request import VFSRequest
from .vssag_schemas.vfs.response import VFSResponse
from .vssag_schemas.vfs.access_context import VSSAGAccessContext


class VSSAGClient:
    """Compose parsing, indexing, retrieval and both VFS restore modes."""

    @classmethod
    def from_env(
        cls,
        *,
        setting_overrides: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> "VSSAGClient":
        """从默认配置创建客户端，并覆盖本次客户端实例的 Setting。

        Args:
            setting_overrides: 与 ``VSSAGConfig`` 字段同构的运行时覆盖。
            **kwargs: 客户端依赖注入参数，例如 store、embedder 或 llm。

        Returns:
            使用最终 Setting 初始化的 VSSAG 客户端。
        """

        config = VSSAGConfig.from_env(overrides=setting_overrides)
        return cls(config=config, **kwargs)

    def __init__(
        self,
        config: VSSAGConfig | None = None,
        *,
        store=None,
        embedder=None,
        llm=None,
        reranker=None,
    ):
        self.config = config or VSSAGConfig.from_env()
        self.store = (
            store
            if store is not None
            else self._build_store()
            if self.config.index_enabled
            else MemoryIndexStore()
        )
        self.embedder = (
            embedder if embedder is not None else self._build_embedder()
        )
        self.llm = llm if llm is not None else self._build_llm()
        if (
            self.config.sql_ability.enabled
            and self.config.sql_ability.target_sources.field_value_index
            .llm_annotation.enabled
            and self.llm is None
        ):
            raise ValueError(
                "vssag 启用字段值 LLM 标注时必须配置 LLM API Key"
                "或注入可用 LLM"
            )
        self.parser = DocumentParser(self.config, llm=self.llm)
        self.indexer = IndexPipeline(
            self.store,
            self.embedder,
            embedding_enabled=self.config.embedding_enabled,
            graph_enabled=self.config.graph_index_enabled,
            graph_max_entities=self.config.graph_max_entities,
        )
        self.retriever = HybridRetriever(
            self.store,
            self.embedder,
            reranker=(
                reranker
                if reranker is not None
                else self._build_reranker()
            ),
            text_enabled=self.config.text_retriever_enabled,
            vector_enabled=(
                self.config.embedding_retriever_enabled
                and self.embedder is not None
            ),
            graph_enabled=self.config.graph_enabled,
            text_weight=self.config.text_retriever_weight,
            vector_weight=self.config.embedding_retriever_weight,
            graph_weight=self.config.graph_weight,
        )
        self.vfs = VFSRestoreService(self.store, self.config)
        self.traces = TraceRecorder()
        self.sql_provider = None
        self.sql_store = None
        self.sql_retriever = None
        self.sql_vfs = None
        self.sql_indexer = None
        self.sql_embedder = None
        if self.config.index_enabled and self.config.sql_ability.enabled:
            self._initialize_sql_components()

    def _build_store(self):
        if self.config.vector_backend == "lancedb":
            return LanceDBIndexStore(
                self.config.lancedb_uri,
                graph_path=self.config.graph_store_path,
            )
        if self.config.vector_backend == "pgvector":
            return PgVectorIndexStore(
                self.config.pgvector_dsn,
                dimension=self.config.embedding_dimension,
                embedding_enabled=self.config.embedding_enabled,
            )
        if self.config.vector_backend == "memory":
            return MemoryIndexStore()
        raise ValueError(
            "unsupported VSSAG vector backend: "
            f"{self.config.vector_backend!r}"
        )

    def _build_embedder(self):
        if not self.config.embedding_enabled:
            return None
        provider_name, provider = self._resolve_model_provider(
            self.config.embedding_provider
        )
        if provider_name not in {
            "openai",
            "qwen",
            "dashscope",
            "aliyun",
            "bailian",
            "volcengine",
            "ark",
            "doubao",
        }:
            raise ConfigurationError(
                "Unsupported Embedding provider: "
                f"{provider_name!r}. Configure an OpenAI-compatible provider."
            )
        if not provider.api_key:
            raise ConfigurationError(
                f"Embedding provider {provider_name!r} requires an API key"
            )
        return OpenAIEmbedder(
            provider.api_key,
            model_name=self.config.embedding_model or provider.model,
            dimension=self.config.embedding_dimension,
            base_url=provider.base_url or None,
            send_dimensions=provider_name
            not in {"volcengine", "ark", "doubao"},
        )

    def _build_llm(self):
        if not self.config.model.api_key and not any(
            provider.api_key
            for provider in self.config.model.providers.values()
        ):
            return None
        _provider_name, provider = self._resolve_model_provider()
        if not provider.api_key:
            return None
        return OpenAILLMProvider(
            provider.api_key,
            model_name=self.config.llm_model or provider.model,
            base_url=provider.base_url or None,
            extra_body=provider.extra_body,
        )

    def _build_reranker(self):
        if not self.config.rerank_model:
            return None
        provider_name, provider = self._resolve_model_provider(
            self.config.model.rerank_provider
        )
        if not provider.api_key:
            raise ConfigurationError(
                f"Rerank provider {provider_name!r} requires an API key"
            )
        if provider_name in {"qwen", "dashscope", "aliyun", "bailian"}:
            return DashScopeReranker(
                provider.api_key,
                self.config.rerank_model,
                endpoint=provider.rerank_base_url or None,
            )
        if provider_name in {"volcengine", "ark", "doubao"}:
            endpoint = provider.rerank_base_url or (
                f"{provider.base_url.rstrip('/')}/rerank"
                if provider.base_url
                else ""
            )
            return VolcengineReranker(
                provider.api_key,
                self.config.rerank_model,
                endpoint=endpoint,
            )
        raise ConfigurationError(
            "The configured Rerank provider has no supported native adapter: "
            f"{provider_name!r}. OpenAI Chat/Embedding compatibility does not "
            "define a generic Rerank endpoint."
        )

    def _resolve_model_provider(self, provider_name: str = ""):
        """解析模型供应商配置，统一处理默认供应商和大小写。

        Args:
            provider_name: 显式供应商名称；为空时使用默认供应商。

        Returns:
            ``(规范化供应商名称, 供应商配置)``。

        Raises:
            ConfigurationError: 供应商未在配置中定义。
        """

        requested = (
            provider_name.strip().lower()
            if provider_name
            else self.config.model.default_provider.strip().lower()
        )
        for name, provider in self.config.model.providers.items():
            if name.strip().lower() == requested:
                return requested, provider
        if (
            requested == self.config.model.default_provider.strip().lower()
            and (self.config.model.api_key or self.config.model.base_url)
        ):
            return requested, ModelProviderConfig(
                api_key=self.config.model.api_key,
                base_url=self.config.model.base_url,
            )
        raise ConfigurationError(
            f"Model provider {requested!r} is not configured"
        )

    def _initialize_sql_components(self) -> None:
        sql_config = self.config.sql_ability
        target = sql_config.target_sources
        provider_type = target.type.lower()
        provider_cls = {
            "sqlite": SQLiteSchemaProvider,
            "postgres": PostgreSQLSchemaProvider,
            "postgresql": PostgreSQLSchemaProvider,
            "mysql": MySQLSchemaProvider,
        }.get(provider_type)
        if provider_cls is None:
            raise ValueError(f"unsupported SQL target source type: {target.type}")
        if provider_type == "sqlite" and not target.dsn:
            raise ValueError(
                "vssag.index.sql_ability.target_sources.dsn is required for sqlite"
            )
        self.sql_provider = provider_cls(
            target.dsn,
            allow_real_table_names=sql_config.security.allow_real_table_names,
        )
        service = sql_config.retriever_service
        store_type = service.type.lower()
        embedding = service.index.embedding
        vector_engine = embedding.engine.strip().lower()
        if store_type in {"sqlite", "memory"}:
            if embedding.enabled and vector_engine == "lancedb":
                self.sql_store = LanceDBSQLSchemaStore(
                    service.dsn or ":memory:",
                    lancedb_uri=embedding.lancedb_uri,
                    namespace=service.namespace,
                    embedding_dimension=embedding.dimension,
                    embedding_model=embedding.model_name,
                )
            elif not embedding.enabled or vector_engine in {
                "sqlite",
                "json",
                "memory",
                "pgvector",
            }:
                self.sql_store = SQLSchemaStore(
                    service.dsn or ":memory:",
                    namespace=service.namespace,
                    embedding_dimension=embedding.dimension
                    if embedding.enabled
                    else 0,
                    embedding_model=embedding.model_name,
                )
            else:
                raise ValueError(
                    f"unsupported SQL vector engine {embedding.engine!r} for {service.type}"
                )
        elif store_type in {"postgres", "postgresql", "pgvector"}:
            from .indexing.index_stores.postgres_sql_schema_store import (
                PostgresSQLSchemaStore,
            )

            if embedding.enabled and vector_engine != "pgvector":
                raise ValueError(
                    f"PostgreSQL SQL Schema indexes require pgvector, got {embedding.engine!r}"
                )
            self.sql_store = PostgresSQLSchemaStore(
                service.dsn,
                namespace=service.namespace,
                auto_create_tables=service.auto_create_tables,
                embedding_dimension=service.index.embedding.dimension
                if service.index.embedding.enabled
                else 0,
                embedding_model=service.index.embedding.model_name,
            )
        else:
            raise ValueError(f"unsupported SQL retriever service type: {service.type}")
        self.sql_embedder = self._build_sql_embedder()
        self.sql_retriever = SQLSchemaRetriever(
            self.sql_store,
            sql_config,
            embedder=self.sql_embedder,
        )
        self.sql_vfs = SQLVFSRestoreService(self.sql_store, sql_config)
        self.sql_indexer = SQLSchemaIndexPipeline(
            self.sql_provider,
            self.sql_store,
            sql_config,
            llm=self.llm,
            embedder=self.sql_embedder,
        )

    def _build_sql_embedder(self):
        embedding = self.config.sql_ability.retriever_service.index.embedding
        if not embedding.enabled:
            return None
        if (
            getattr(self.embedder, "dimension", None) == embedding.dimension
            and getattr(self.embedder, "model_name", None) == embedding.model_name
        ):
            return self.embedder
        provider_name, provider = self._resolve_model_provider(
            embedding.provider
            or self.config.embedding_provider
        )
        if not provider.api_key:
            raise ConfigurationError(
                f"SQL Embedding provider {provider_name!r} requires an API key"
            )
        if provider_name not in {
            "openai",
            "qwen",
            "dashscope",
            "aliyun",
            "bailian",
            "volcengine",
            "ark",
            "doubao",
        }:
            raise ConfigurationError(
                f"Unsupported SQL Embedding provider: {provider_name!r}"
            )
        return OpenAIEmbedder(
            provider.api_key,
            dimension=embedding.dimension,
            model_name=embedding.model_name,
            base_url=embedding.base_url or provider.base_url or None,
            send_dimensions=provider_name
            not in {"volcengine", "ark", "doubao"},
        )

    async def parse_document(self, source, *, filename: str | None = None, parser: str = "auto", options: dict[str, Any] | None = None):
        if not self.config.enabled:
            raise RuntimeError("VSSAG capability is disabled")
        if not self.config.parsing_enabled:
            raise RuntimeError("VSSAG document parsing is disabled")
        trace_id = self.traces.start(
            "parse",
            {"filename": filename or "", "parser": parser},
        )
        try:
            document = await self.parser.parse(
                source,
                filename=filename,
                parser=parser,
                options=options,
            )
            self.traces.end(
                trace_id,
                {
                    "source_name": document.source_name,
                    "items": len(document.items),
                    "sections": len(document.sections),
                },
            )
            return document
        except Exception as exc:
            self.traces.end(trace_id, error=str(exc))
            raise

    async def index_record(self, record: ContentRecord):
        if not self.config.enabled:
            raise RuntimeError("VSSAG capability is disabled")
        if not self.config.index_enabled:
            raise RuntimeError("VSSAG document indexing is disabled")
        trace_id = self.traces.start(
            "index",
            {"record_id": record.record_id, "source_id": record.source_id},
        )
        try:
            payload = await self.indexer.index_record(record)
            self.traces.end(
                trace_id,
                {
                    "record_id": payload.record_id,
                    "embedding_dimension": payload.embedding_dimension,
                    "references": len(payload.reference_ids),
                    "entities": len(payload.entity_ids),
                },
            )
            return payload
        except Exception as exc:
            self.traces.end(trace_id, error=str(exc))
            raise

    async def index_records(self, records: Iterable[ContentRecord]):
        if not self.config.enabled:
            raise RuntimeError("VSSAG capability is disabled")
        if not self.config.index_enabled:
            raise RuntimeError("VSSAG document indexing is disabled")
        return await self.indexer.index_records(records)

    async def index_parsed_document(
        self,
        document: ParsedDocument,
        *,
        source_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        """Index parser output without imposing an application block model."""
        source = source_id or document.source_name
        base_metadata = dict(document.metadata)
        base_metadata.update(metadata or {})
        records = [
            ContentRecord(
                record_id=item.item_id,
                source_id=source,
                text=item.text,
                title=item.text if item.type == "heading" else None,
                heading_path=item.heading_path or None,
                position=item.position,
                metadata={**base_metadata, **item.metadata, "content_type": item.type},
            )
            for item in document.items
            if item.text.strip()
        ]
        return await self.index_records(records)

    async def delete_record(self, record_id: str):
        if not self.config.enabled:
            raise RuntimeError("VSSAG capability is disabled")
        if not self.config.index_enabled:
            raise RuntimeError("VSSAG document indexing is disabled")
        return await self.indexer.delete_record(record_id)

    async def rebuild_source(self, source_id: str, records: Iterable[ContentRecord]):
        if not self.config.enabled:
            raise RuntimeError("VSSAG capability is disabled")
        if not self.config.index_enabled:
            raise RuntimeError("VSSAG document indexing is disabled")
        return await self.indexer.rebuild_source(source_id, records)

    async def search(self, request: SearchRequest | str, **kwargs) -> SearchResponse:
        if not self.config.enabled:
            raise RuntimeError("VSSAG capability is disabled")
        if isinstance(request, str):
            request = SearchRequest(query=request, **kwargs)
        trace_id = self.traces.start(
            "retrieval",
            {"query": request.query, "strategy": request.strategy},
        )
        try:
            try:
                response = await asyncio.wait_for(
                    self.retriever.search(request),
                    timeout=self.config.max_query_seconds,
                )
            except asyncio.TimeoutError as exc:
                raise RetrievalError(
                    f"retrieval timed out after {self.config.max_query_seconds:g}s"
                ) from exc
            response.trace_id = trace_id
            self.traces.end(
                trace_id,
                {"total": response.total, "mode": response.mode},
            )
            return response
        except Exception as exc:
            self.traces.end(trace_id, error=str(exc))
            raise

    async def restore_tool(self, request: VFSRequest) -> VFSResponse:
        """Restore a structured Tool-Use request through vfsrestore."""
        return await self.vfs.restore_tool(request)

    async def restore_bash(
        self,
        command: str,
        context: VSSAGAccessContext,
        *,
        mount_root: str | None = None,
    ) -> VFSResponse:
        """拦截 VSSAG 文档 Bash 指令并返回终端风格文本。"""

        return await self.vfs.restore_bash(
            command,
            context,
            mount_root=mount_root,
        )

    async def build_vfs(
        self,
        context: VSSAGAccessContext,
        *,
        mount_root: str | None = None,
    ) -> str:
        """通过用户权限构建受限展开的 VSSAG 虚拟文件目录树。

        Args:
            context: 当前调用方的 VSSAG 访问上下文。
            mount_root: 外部应用提供的虚拟目录父路径。

        Returns:
            受限展开的虚拟文件目录树字符串。
        """

        return await self.vfs.build_tree(context, mount_root=mount_root)

    def _require_sql(self):
        if not self.config.sql_ability.enabled or self.sql_store is None:
            raise RuntimeError("VSSAG SQL Schema 召回功能未启用")
        return self.sql_store

    async def collect_sql_schema(
        self,
        *,
        strategy: str = "standard",
        progress_callback: Callable[[dict[str, Any]], Any] | None = None,
        control_callback: Callable[[dict[str, Any]], Any] | None = None,
    ) -> SQLSchemaCollection:
        """Collect configured database metadata and update SQL indexes.

        Args:
            strategy: SQL 索引策略，支持 fast、standard、full。
            progress_callback: Optional synchronous or asynchronous progress
                callback invoked by the SDK pipeline.
            control_callback: 管理员控制回调，返回 continue、pause 或 cancel。

        Returns:
            Completed SQL Schema collection.
        """

        if self.sql_indexer is None:
            self._require_sql()
        return await self.sql_indexer.collect(
            strategy=strategy,
            progress_callback=progress_callback,
            control_callback=control_callback,
        )

    async def pause_sql_field_value_collection(self, job_id: str) -> bool:
        """暂停字段值采集任务并保留可恢复 checkpoint。

        Args:
            job_id: 字段值采集任务 ID。

        Returns:
            任务成功进入暂停状态时返回 True。
        """

        if self.sql_store is None:
            self._require_sql()
        pause = getattr(self.sql_store, "pause_collection_job", None)
        return bool(pause is not None and await pause(job_id))

    async def cancel_sql_field_value_collection(self, job_id: str) -> bool:
        """取消运行中或等待恢复的字段值采集任务。

        Args:
            job_id: 字段值采集任务 ID。

        Returns:
            成功取消返回真；任务不存在或已经结束返回假。
        """

        if self.sql_store is None:
            self._require_sql()
        return await self.sql_store.cancel_collection_job(job_id)

    def create_sql_field_value_cdc_runtime(self):
        """按当前配置创建字段值 CDC 源、运行时和源名称。

        Returns:
            ``(source, runtime, source_name)``，调用方负责在停止时关闭
            source 和 runtime.transport。

        Raises:
            RuntimeError: SQL 索引未启用，或 CDC 未启用。
            ValueError: CDC 数据库类型或传输方式不支持。
        """

        if self.sql_store is None or self.sql_indexer is None:
            self._require_sql()
        sql_config = self.config.sql_ability
        target = sql_config.target_sources
        field_value_config = target.field_value_index
        cdc = field_value_config.cdc
        if not cdc.enabled:
            raise RuntimeError("vssag 字段值 CDC 未启用")
        provider_type = target.type.lower()
        if provider_type in {"postgres", "postgresql"}:
            source = PostgresWALFieldValueSource(
                target.dsn,
                field_value_config.database_id,
                cdc.postgres_wal,
            )
            source_name = cdc.postgres_wal.slot_name
        elif provider_type == "mysql":
            source = MySQLBinlogFieldValueSource(
                target.dsn,
                field_value_config.database_id,
                cdc.mysql_binlog,
            )
            source_name = f"mysql-binlog-{cdc.mysql_binlog.server_id}"
        else:
            raise ValueError("vssag 字段值 CDC 仅支持 PostgreSQL 或 MySQL")
        if cdc.transport == "kafka":
            transport = KafkaFieldValueStream(cdc.kafka)
        elif cdc.transport == "redis_stream":
            transport = RedisFieldValueStream(cdc.redis_stream)
        else:
            raise ValueError(
                f"vssag 不支持字段值 CDC transport: {cdc.transport}"
            )
        runtime = SQLFieldValueCDCRuntime(
            self.sql_store,
            transport,
            field_value_config,
            llm=self.llm,
            model_name=getattr(self.llm, "model_name", "unknown"),
            allow_real_table_names=(
                sql_config.security.allow_real_table_names
            ),
            embedder=self.sql_embedder,
            embedding_model_name=(
                sql_config.retriever_service.index.embedding.model_name
            ),
        )
        return source, runtime, source_name

    async def search_sql_schema(
        self,
        query: str,
        *,
        top_k: int | None = None,
        include_values: bool = True,
        include_evidence: bool = True,
        include_graph: bool = True,
        scope: SQLScope | None = None,
        user_id: str = "anonymous",
        schema_names: list[str] | None = None,
        table_names: list[str] | None = None,
        excluded_columns: dict[str, list[str]] | None = None,
    ) -> SQLSearchResponse:
        if self.sql_retriever is None:
            self._require_sql()
        scope = scope or SQLScope(
            user_id=user_id,
            schema_names=schema_names or [],
            table_names=table_names or [],
            excluded_columns=excluded_columns or {},
        )
        return await self.sql_retriever.search(
            query,
            top_k=top_k,
            include_values=include_values,
            include_evidence=include_evidence,
            include_graph=include_graph,
            scope=scope,
        )

    async def resolve_sql_table_name(
        self,
        table_name: str,
        *,
        scope: SQLScope,
    ) -> str | None:
        """将随机表名解析为真实表名，并校验访问范围。"""

        if self.sql_store is None:
            self._require_sql()
        return await self.sql_store.resolve_table_name(table_name, scope=scope)

    async def compile_sql(self, sql: str, *, scope: SQLScope) -> str:
        """编译使用虚拟表名的 SQL 文本，不执行 SQL。"""

        if self.sql_store is None:
            self._require_sql()
        return await self.sql_store.compile_sql(sql, scope=scope)

    async def restore_sql_tool(self, request: SQLToolRequest) -> SQLToolResponse:
        if self.sql_vfs is None:
            self._require_sql()
        return await self.sql_vfs.restore_tool(request)

    async def grep_schema(
        self,
        mode: str,
        value: str | None,
        path: str,
        *,
        user_id: str = "anonymous",
        schema_names: list[str] | None = None,
        table_names: list[str] | None = None,
        excluded_columns: dict[str, list[str]] | None = None,
    ) -> SQLToolResponse:
        """Compatibility facade matching the design document's grep_schema API."""

        return await self.restore_sql_tool(
            SQLToolRequest(
                mode=mode,
                value=value,
                path=path,
                scope=SQLScope(
                    user_id=user_id,
                    schema_names=schema_names or [],
                    table_names=table_names or [],
                    excluded_columns=excluded_columns or {},
                ),
            )
        )

    async def restore_sql_bash(
        self,
        command: str,
        scope: SQLScope,
    ) -> SQLToolResponse:
        if self.sql_vfs is None:
            self._require_sql()
        return await self.sql_vfs.restore_bash(command, scope)

    async def record_sql_feedback(self, feedback: SQLFeedback):
        if self.sql_indexer is None:
            self._require_sql()
        return await self.sql_indexer.record_feedback(feedback)

    async def add_sql_evidence_candidate(
        self,
        candidate: SQLEvidenceCandidate,
    ) -> int:
        if self.sql_store is None:
            self._require_sql()
        return await self.sql_store.add_evidence_candidate(candidate)

    async def list_sql_field_value_annotations(
        self,
        *,
        status: str = "pending",
        limit: int = 100,
    ) -> list[SQLFieldValueAnnotation]:
        """列出字段值 LLM 标注审核记录。

        Args:
            status: pending、approved、rejected 或 disabled。
            limit: 最大返回数量。

        Returns:
            字段值标注记录。
        """

        if self.sql_store is None:
            self._require_sql()
        return await self.sql_store.list_field_value_annotations(
            status=status,
            limit=limit,
        )

    async def review_sql_field_value_annotation(
        self,
        annotation_id: int,
        *,
        status: str,
        aliases: list[str] | None = None,
        reason: str | None = None,
    ) -> SQLFieldValueAnnotation | None:
        """审核字段值 LLM 标注。

        Args:
            annotation_id: 标注记录 ID。
            status: approved、rejected 或 disabled。
            aliases: 审核确认的别名。
            reason: 审核原因。

        Returns:
            更新后的标注；记录不存在时返回空值。
        """

        if self.sql_store is None:
            self._require_sql()
        return await self.sql_store.review_field_value_annotation(
            annotation_id,
            status=status,
            aliases=aliases,
            reason=reason,
        )

    async def list_sql_evidence_candidates(
        self,
        *,
        status: str = "pending",
        limit: int = 100,
    ) -> list[SQLEvidenceCandidate]:
        if self.sql_store is None:
            self._require_sql()
        return await self.sql_store.list_evidence_candidates(
            status=status,
            limit=limit,
        )

    async def review_sql_evidence_candidate(
        self,
        candidate_id: int,
        *,
        status: str,
        evidence_type: str = "rule",
        description: str = "",
    ) -> SQLEvidence | None:
        if self.sql_store is None:
            self._require_sql()
        return await self.sql_store.review_evidence_candidate(
            candidate_id,
            status=status,
            evidence_type=evidence_type,
            description=description,
        )

    async def update_sql_graph(
        self,
        edge_id: int,
        *,
        relation_type: str,
        status: str = "active",
    ) -> None:
        if self.sql_store is None:
            self._require_sql()
        await self.sql_store.update_graph_status(
            edge_id,
            relation_type=relation_type,
            status=status,
        )

    async def close(self):
        await self.vfs.close()
        close = getattr(self.store, "close", None)
        if close:
            await close()
        if self.sql_provider is not None:
            await self.sql_provider.close()
        if self.sql_store is not None:
            await self.sql_store.close()

    async def __aenter__(self) -> "VSSAGClient":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self.close()


async def parse_document(source, **kwargs):
    return await DocumentParser().parse(source, **kwargs)
