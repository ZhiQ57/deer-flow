"""LanceDB-backed vector storage for the SQL Schema index.

The relational SQL Schema records remain in the SQLite store while the
configured LanceDB engine stores table, field-value and evidence embeddings.
This keeps the local deployment layout from the design document: SQLite for
metadata/relationships and LanceDB for vector retrieval.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ...vssag_schemas.sql import (
    SQLEvidence,
    SQLFieldValue,
    SQLSchemaCollection,
    SQLTableSchema,
)
from .sql_schema_store import SQLSchemaStore


class LanceDBSQLSchemaStore(SQLSchemaStore):
    """SQLite metadata store with LanceDB vector tables."""

    backend = "lancedb"

    def __init__(
        self,
        dsn: str,
        *,
        lancedb_uri: str,
        namespace: str = "sql_schema",
        embedding_dimension: int = 0,
        embedding_model: str = "",
    ):
        # The parent is used for relational records only. Vector records are
        # maintained in LanceDB and therefore must not create SQLite vectors.
        super().__init__(
            dsn,
            namespace=namespace,
            embedding_dimension=0,
            embedding_model=embedding_model,
        )
        self.lancedb_uri = lancedb_uri or "./data/vssag.lancedb"
        self.embedding_dimension = max(0, int(embedding_dimension))
        self.embedding_model = embedding_model
        self._lance = None
        self._pa = None
        self._tables: dict[str, Any] = {}

    def _ensure_lance(self) -> None:
        if self._lance is not None and self._pa is not None:
            return
        try:
            import lancedb
            import pyarrow as pa
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "LanceDB SQL Schema 向量索引需要 lancedb，请安装 "
                "ownmindforge-vssag[lancedb]"
            ) from exc
        Path(self.lancedb_uri).parent.mkdir(parents=True, exist_ok=True)
        self._lance = lancedb.connect(self.lancedb_uri)
        self._pa = pa

    def _table_name(self, kind: str) -> str:
        if kind not in {"table", "value", "evidence"}:
            raise ValueError(f"unsupported SQL embedding kind: {kind}")
        return f"nl2sql_{kind if kind != 'value' else 'field_value'}_embeddings_{self.embedding_dimension}"

    def _open_table(self, kind: str):
        self._ensure_lance()
        table_name = self._table_name(kind)
        if table_name in self._tables:
            return self._tables[table_name]
        try:
            table = self._lance.open_table(table_name)
        except Exception:
            table = None
        self._tables[table_name] = table
        return table

    def _make_rows(self, items, vectors, *, kind: str, model_name: str):
        if len(items) != len(vectors):
            raise ValueError(f"{kind} embedding count does not match payload count")
        rows = []
        for item, vector in zip(items, vectors, strict=True):
            values = [float(value) for value in vector]
            if self.embedding_dimension and len(values) != self.embedding_dimension:
                raise ValueError(
                    f"{kind} embedding dimension {len(values)} != configured {self.embedding_dimension}"
                )
            item_id = self._resolve_item_id(item, kind)
            if item_id is None:
                continue
            rows.append(
                {
                    "index_id": int(item_id),
                    "model_name": model_name,
                    "payload_json": json.dumps(
                        item.model_dump(mode="json"),
                        ensure_ascii=False,
                    ),
                    "vector": values,
                }
            )
        return rows

    def _resolve_item_id(self, item, kind: str) -> int | None:
        if getattr(item, "id", None) is not None:
            return int(item.id)
        with self._connect() as connection:
            if kind == "table":
                row = connection.execute(
                    """
                    SELECT id FROM nl2sql_table_indexs
                    WHERE database_id=? AND db_type=?
                      AND raw_schema_name=? AND raw_table_name=?
                    LIMIT 1
                    """,
                    (
                        item.database_id,
                        item.db_type,
                        item.raw_schema_name,
                        item.raw_table_name,
                    ),
                ).fetchone()
            elif kind == "value":
                row = connection.execute(
                    """
                    SELECT id
                    FROM nl2sql_field_value_indexs
                    WHERE database_id=? AND schema_name=?
                      AND raw_table_name=? AND column_name=? AND raw_value=?
                    LIMIT 1
                    """,
                    (
                        item.database_id,
                        item.schema_name,
                        item.raw_table_name,
                        item.column_name,
                        item.raw_value,
                    ),
                ).fetchone()
            else:
                row = connection.execute(
                    """
                    SELECT id FROM nl2sql_evidence_indexs
                    WHERE evidence_type=? AND evidence_content=?
                    ORDER BY id
                    LIMIT 1
                    """,
                    (item.evidence_type, item.evidence_content),
                ).fetchone()
        return int(row["id"]) if row else None

    async def replace_collection(
        self,
        collection: SQLSchemaCollection,
        *,
        changed_tables: set[tuple[str, str]] | None = None,
    ) -> dict[str, int]:
        configured_dimension = self.embedding_dimension
        self.embedding_dimension = 0
        try:
            result = await super().replace_collection(
                collection,
                changed_tables=changed_tables,
            )
        finally:
            self.embedding_dimension = configured_dimension
        await asyncio.to_thread(self._prune_vectors_sync)
        return result

    async def reconcile_collection_job(
        self,
        collection: SQLSchemaCollection,
    ) -> None:
        """校准字段值运行状态并删除已经失效的 LanceDB 向量。

        Args:
            collection: 已完整采集的字段值任务结果。
        """

        await super().reconcile_collection_job(collection)
        await asyncio.to_thread(self._prune_vectors_sync)

    async def upsert_embeddings(
        self,
        *,
        tables: Sequence[tuple[SQLTableSchema, Sequence[float]]] = (),
        values: Sequence[tuple[SQLFieldValue, Sequence[float]]] = (),
        evidence: Sequence[tuple[SQLEvidence, Sequence[float]]] = (),
        model_name: str | None = None,
    ) -> None:
        await asyncio.to_thread(
            self._upsert_embeddings_sync,
            tables,
            values,
            evidence,
            model_name or self.embedding_model or "default",
        )

    async def filter_field_values_requiring_embeddings(
        self,
        values: Sequence[SQLFieldValue],
        *,
        model_name: str,
    ) -> list[SQLFieldValue]:
        """筛出 LanceDB 中新增或检索文本变化的字段值。

        Args:
            values: 本次采集的字段值。
            model_name: Embedding 模型名称。

        Returns:
            需要生成或更新向量的字段值。
        """

        return await asyncio.to_thread(
            self._filter_field_values_requiring_embeddings_sync,
            list(values),
            model_name,
        )

    def _filter_field_values_requiring_embeddings_sync(
        self,
        values: list[SQLFieldValue],
        model_name: str,
    ) -> list[SQLFieldValue]:
        if not values or not self.embedding_dimension:
            return values
        table = self._open_table("value")
        if table is None:
            return values
        existing = {}
        for row in table.to_arrow().to_pylist():
            if str(row.get("model_name") or "") != model_name:
                continue
            payload = json.loads(str(row.get("payload_json") or "{}"))
            existing[int(row["index_id"])] = str(
                payload.get("retrieval_text")
                or payload.get("raw_value")
                or ""
            )
        result = []
        for value in values:
            value_id = self._resolve_item_id(value, "value")
            if (
                value_id is None
                or existing.get(value_id)
                != (value.retrieval_text or value.raw_value)
            ):
                result.append(value)
        return result

    def _upsert_embeddings_sync(self, tables, values, evidence, model_name: str) -> None:
        if not self.embedding_dimension:
            return
        groups = (
            ("table", tables),
            ("value", values),
            ("evidence", evidence),
        )
        for kind, payloads in groups:
            if not payloads:
                continue
            items = [item for item, _vector in payloads]
            vectors = [vector for _item, vector in payloads]
            rows = self._make_rows(
                items,
                vectors,
                kind=kind,
                model_name=model_name,
            )
            if not rows:
                continue
            table = self._open_table(kind)
            arrow_table = self._pa.Table.from_pylist(rows)
            if table is None:
                table_name = self._table_name(kind)
                self._tables[table_name] = self._lance.create_table(
                    table_name,
                    data=arrow_table,
                )
                continue
            ids = sorted({int(row["index_id"]) for row in rows})
            id_clause = ", ".join(str(item) for item in ids)
            model_literal = _escape_literal(model_name)
            table.delete(
                f"index_id IN ({id_clause}) AND model_name = '{model_literal}'"
            )
            table.add(arrow_table)

    async def search_vector(
        self,
        query_vector: Sequence[float],
        *,
        kind: str,
        limit: int = 20,
        model_name: str | None = None,
    ):
        return await asyncio.to_thread(
            self._search_vector_sync,
            list(query_vector),
            kind,
            limit,
            model_name or self.embedding_model or "default",
        )

    def _search_vector_sync(self, query_vector, kind: str, limit: int, model_name: str):
        if not self.embedding_dimension:
            return []
        values = [float(value) for value in query_vector]
        if len(values) != self.embedding_dimension:
            raise ValueError(
                f"query embedding dimension {len(values)} != configured {self.embedding_dimension}"
            )
        table = self._open_table(kind)
        if table is None:
            return []
        model_literal = _escape_literal(model_name)
        rows = (
            table.search(values)
            .metric("cosine")
            .where(f"model_name = '{model_literal}'")
            .limit(
                max(100, int(limit) * 4)
                if kind == "value"
                else max(1, int(limit))
            )
            .to_list()
        )
        active_value_ids: set[int] | None = None
        if kind == "value" and rows:
            index_ids = sorted({int(row["index_id"]) for row in rows})
            placeholders = ",".join("?" for _ in index_ids)
            with self._connect() as connection:
                active_value_ids = {
                    int(row["value_index_id"])
                    for row in connection.execute(
                        f"""
                        SELECT value_index_id
                        FROM nl2sql_field_value_runtime_states
                        WHERE status='active'
                          AND value_index_id IN ({placeholders})
                        """,
                        tuple(index_ids),
                    ).fetchall()
                }
        result = []
        for row in rows:
            if (
                active_value_ids is not None
                and int(row["index_id"]) not in active_value_ids
            ):
                continue
            payload = json.loads(row["payload_json"])
            if kind == "table":
                item = SQLTableSchema.model_validate(payload)
            elif kind == "value":
                item = SQLFieldValue.model_validate(payload)
            else:
                item = SQLEvidence.model_validate(payload)
            result.append((item, float(row.get("_distance", 0.0))))
        # LanceDB returns distance; the retriever expects a larger-is-better
        # score, so convert cosine distance to a bounded similarity score.
        return [
            (item, 1.0 - score)
            for item, score in result[: max(1, int(limit))]
        ]

    def _prune_vectors_sync(self) -> None:
        if self.embedding_dimension <= 0:
            return
        with self._connect() as connection:
            valid_ids = {
                "table": {
                    int(row["id"])
                    for row in connection.execute(
                        "SELECT id FROM nl2sql_table_indexs"
                    ).fetchall()
                },
                "value": {
                    int(row["id"])
                    for row in connection.execute(
                        """
                        SELECT value.id
                        FROM nl2sql_field_value_indexs AS value
                        JOIN nl2sql_field_value_runtime_states AS state
                          ON state.value_index_id=value.id
                        WHERE state.status='active'
                        """
                    ).fetchall()
                },
                "evidence": {
                    int(row["id"])
                    for row in connection.execute(
                        "SELECT id FROM nl2sql_evidence_indexs"
                    ).fetchall()
                },
            }
        for kind, ids in valid_ids.items():
            table = self._open_table(kind)
            if table is None:
                continue
            rows = table.to_arrow().to_pylist()
            for row in rows:
                if int(row["index_id"]) not in ids:
                    table.delete(f"index_id = {int(row['index_id'])}")

    async def close(self) -> None:
        self._tables.clear()
        self._lance = None
        self._pa = None
        await super().close()


def _escape_literal(value: str) -> str:
    return str(value).replace("'", "''")
