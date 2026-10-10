"""LLM business relation extraction for the SQL Schema graph."""

from __future__ import annotations

import json
import re
import asyncio
from collections.abc import MutableSequence
from typing import Any

from ...prompts import render_prompt
from ...providers.sql_prov.base import table_alias
from ...vssag_schemas.sql import SQLGraphEdge, SQLSchemaCollection, SQLTableSchema

_DIRECTIONS = {"outgoing", "incoming", "bidirectional"}
_CARDINALITIES = {
    "one_to_one",
    "one_to_many",
    "many_to_one",
    "many_to_many",
    "unknown",
}
_GRAPH_BATCH_MAX_CHARS = 24_000
_GRAPH_LLM_TIMEOUT_SECONDS = 180


async def extract_business_graph(
    collection: SQLSchemaCollection,
    llm,
    *,
    allow_real_table_names: bool,
    enabled: bool,
    trace: MutableSequence[dict[str, Any]] | None = None,
) -> SQLSchemaCollection:
    """Use complete-schema batches to create candidate business edges."""

    if not enabled or llm is None:
        return collection
    batches = _table_batches(
        collection.tables,
        allow_real_table_names=allow_real_table_names,
    )
    lookup = _table_lookup(
        collection.tables,
        allow_real_table_names=allow_real_table_names,
    )
    all_edges: list[SQLGraphEdge] = []
    for batch_index, batch in enumerate(batches, start=1):
        batch_text = _schema_text(
            batch,
            allow_real_table_names=allow_real_table_names,
        )
        prompt = render_prompt(
            "graph_relation_extraction.md",
            schema_text=(
                _compact_schema_index(
                    collection.tables,
                    allow_real_table_names=allow_real_table_names,
                )
                + "\n\n"
                + batch_text
            ),
        )
        trace_item: dict[str, Any] = {
            "batch_index": batch_index,
            "batch_count": len(batches),
            "table_names": [table.raw_table_name for table in batch],
            "prompt": prompt,
        }
        if trace is not None:
            trace.append(trace_item)
        try:
            response = await asyncio.wait_for(
                llm.complete(prompt, temperature=0.0, max_tokens=3000),
                timeout=_GRAPH_LLM_TIMEOUT_SECONDS,
            )
            trace_item["raw_response"] = response
            parsed = _parse_json(response)
            edges = _edges_from_payload(
                parsed,
                lookup,
                allow_real_table_names=allow_real_table_names,
            )
            trace_item["parsed_relations"] = [
                edge.model_dump(mode="json") for edge in edges
            ]
            all_edges.extend(edges)
        except Exception as exc:
            trace_item["raw_response"] = ""
            trace_item["error"] = str(exc)
    edges = _dedupe_edges(all_edges)
    return collection.model_copy(
        update={"graph_edges": [*collection.graph_edges, *edges]}
    )


def _table_lookup(
    tables: list[SQLTableSchema],
    *,
    allow_real_table_names: bool,
) -> dict[tuple[str, str], SQLTableSchema]:
    lookup: dict[tuple[str, str], SQLTableSchema] = {}
    for table in tables:
        visible = (
            table.raw_table_name
            if allow_real_table_names
            else table.random_table_name
        )
        lookup[(table.raw_schema_name, visible)] = table
    return lookup


def _table_batches(
    tables: list[SQLTableSchema],
    *,
    allow_real_table_names: bool,
) -> list[list[SQLTableSchema]]:
    batches: list[list[SQLTableSchema]] = []
    current: list[SQLTableSchema] = []
    current_size = 0
    for table in sorted(
        tables,
        key=lambda item: (item.raw_schema_name, item.raw_table_name),
    ):
        table_text = _schema_text(
            [table],
            allow_real_table_names=allow_real_table_names,
        )
        table_size = len(table_text)
        if current and current_size + table_size > _GRAPH_BATCH_MAX_CHARS:
            batches.append(current)
            current = []
            current_size = 0
        current.append(table)
        current_size += table_size
    if current:
        batches.append(current)
    return batches


def _compact_schema_index(
    tables: list[SQLTableSchema],
    *,
    allow_real_table_names: bool,
) -> str:
    lines = ["可关联的全部表名（仅用于定位；关系必须有下方当前批次结构依据）："]
    for table in sorted(
        tables,
        key=lambda item: (item.raw_schema_name, item.raw_table_name),
    ):
        visible = (
            table.raw_table_name
            if allow_real_table_names
            else table.random_table_name
        )
        lines.append(f"- {table.raw_schema_name}.{visible}")
    return "\n".join(lines)


def _schema_text(
    tables: list[SQLTableSchema],
    *,
    allow_real_table_names: bool,
 ) -> str:
    aliases = {
        table.raw_table_name: (
            table.raw_table_name
            if allow_real_table_names
            else table.random_table_name
        )
        for table in tables
    }
    lines: list[str] = []
    for table in sorted(tables, key=lambda item: (item.raw_schema_name, item.raw_table_name)):
        visible_name = aliases[table.raw_table_name]
        columns = "\n".join(
            f"  - {column.name}: {column.data_type}"
            + (f"；{column.comment}" if column.comment else "")
            for column in table.columns
        )
        ddl = table.raw_ddl_text if allow_real_table_names else table.random_ddl_text
        if not allow_real_table_names:
            ddl = _mask_table_names(ddl, aliases)
        lines.extend(
            [
                f"### Schema: {table.raw_schema_name}; Table: {visible_name}",
                f"表说明: {_mask_table_names(table.raw_table_label, aliases)}",
                "字段:",
                columns or "  - （无字段信息）",
                "DDL:",
                ddl,
                "",
            ]
        )
    return "\n".join(lines)


def _dedupe_edges(edges: list[SQLGraphEdge]) -> list[SQLGraphEdge]:
    seen: set[tuple[Any, ...]] = set()
    result: list[SQLGraphEdge] = []
    for edge in edges:
        key = (
            edge.raw_schema_name,
            edge.raw_table_name,
            edge.raw_column_name,
            edge.related_raw_schema_name,
            edge.related_raw_table_name,
            edge.related_raw_column_name,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(edge)
    return result


def _edges_from_payload(
    payload: Any,
    lookup: dict[tuple[str, str], SQLTableSchema],
    *,
    allow_real_table_names: bool,
) -> list[SQLGraphEdge]:
    if isinstance(payload, dict):
        payload = payload.get("relations") or payload.get("edges") or []
    if not isinstance(payload, list):
        return []
    result: list[SQLGraphEdge] = []
    seen: set[tuple[Any, ...]] = set()
    for item in payload:
        if not isinstance(item, dict):
            continue
        source = _resolve_table(
            item,
            lookup,
            prefix="source",
            allow_real_table_names=allow_real_table_names,
        )
        target = _resolve_table(
            item,
            lookup,
            prefix="target",
            allow_real_table_names=allow_real_table_names,
        )
        if source is None or target is None:
            continue
        source_column = _valid_column(source, item.get("source_column"))
        target_column = _valid_column(target, item.get("target_column"))
        if source_column is None or target_column is None:
            source_column = None
            target_column = None
        key = (
            source.raw_schema_name,
            source.raw_table_name,
            source_column,
            target.raw_schema_name,
            target.raw_table_name,
            target_column,
        )
        if source.raw_table_name == target.raw_table_name or key in seen:
            continue
        seen.add(key)
        direction = str(item.get("relation_direction") or "bidirectional").strip().lower()
        cardinality = str(item.get("cardinality") or "unknown").strip().lower()
        if direction not in _DIRECTIONS:
            direction = "bidirectional"
        if cardinality not in _CARDINALITIES:
            cardinality = "unknown"
        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        join_condition = ""
        if source_column and target_column:
            join_condition = (
                f"{source.raw_table_name}.{source_column} = "
                f"{target.raw_table_name}.{target_column}"
            )
        evidence = str(
            item.get("evidence_content")
            or item.get("evidence")
            or item.get("rationale")
            or ""
        ).strip()
        result.append(
            SQLGraphEdge(
                db_type=source.db_type,
                raw_schema_name=source.raw_schema_name,
                related_raw_schema_name=target.raw_schema_name,
                raw_table_name=source.raw_table_name,
                random_table_name=source.random_table_name,
                raw_column_name=source_column,
                random_column_name=source_column,
                related_raw_table_name=target.raw_table_name,
                related_random_table_name=target.random_table_name,
                related_raw_column_name=target_column,
                related_random_column_name=target_column,
                relation_type="candidate",
                relation_direction=direction,
                cardinality=cardinality,
                join_condition=join_condition,
                evidence_source="model_inference",
                evidence_content=evidence,
                confidence=confidence,
                status="active",
            )
        )
    return result


def _resolve_table(
    item: dict[str, Any],
    lookup: dict[tuple[str, str], SQLTableSchema],
    *,
    prefix: str,
    allow_real_table_names: bool,
) -> SQLTableSchema | None:
    name = str(
        item.get(f"{prefix}_table")
        or item.get(f"{prefix}_table_name")
        or ""
    ).strip()
    schema = str(
        item.get(f"{prefix}_schema")
        or item.get(f"{prefix}_schema_name")
        or ""
    ).strip()
    if not name:
        return None
    if schema and (schema, name) in lookup:
        return lookup[(schema, name)]
    matches = [
        table
        for (table_schema, table_name), table in lookup.items()
        if table_name == name
    ]
    if len(matches) == 1:
        return matches[0]
    if allow_real_table_names:
        return None
    alias_matches = [
        table
        for table in lookup.values()
        if table_alias(
            table.db_type,
            table.raw_schema_name,
            table.raw_table_name,
            enabled=False,
        )
        == name
    ]
    return alias_matches[0] if len(alias_matches) == 1 else None


def _valid_column(table: SQLTableSchema, value: Any) -> str | None:
    name = str(value or "").strip()
    if not name:
        return None
    if "." in name:
        name = name.rsplit(".", 1)[-1]
    known = {column.name for column in table.columns}
    return name if name in known else None


def _parse_json(value: str) -> Any:
    text = (value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _mask_table_names(value: str, aliases: dict[str, str]) -> str:
    text = value or ""
    for raw, visible in sorted(aliases.items(), key=lambda item: -len(item[0])):
        text = re.sub(
            rf"(?<![\w$]){re.escape(raw)}(?![\w$])",
            visible,
            text,
            flags=re.IGNORECASE,
        )
    return text


__all__ = ["extract_business_graph"]
