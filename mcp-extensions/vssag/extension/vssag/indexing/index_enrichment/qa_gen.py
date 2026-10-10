"""Optional LLM question/answer enrichment for SQL tables."""

from __future__ import annotations

import json
import re

from ...prompts import render_prompt
from ...vssag_schemas.sql import SQLSchemaCollection


async def generate_table_qa(collection: SQLSchemaCollection, llm, *, enabled: bool) -> SQLSchemaCollection:
    if not enabled or llm is None:
        return collection
    tables = []
    for table in collection.tables:
        prompt = render_prompt(
            "sql_qa_generation.md",
            table_summary=table.raw_schema_summary,
            ddl=table.raw_ddl_text,
        )
        try:
            response = await llm.complete(prompt, temperature=0.0)
            parsed = _parse_json(response)
            qa_text = "\n".join(
                f"Q: {item.get('question', '')}\nA: {item.get('answer', '')}"
                for item in parsed
                if isinstance(item, dict)
            )
        except Exception:
            # QA is an optional enrichment step. A missing/unsupported chat
            # model must not prevent Schema collection and retrieval.
            qa_text = ""
        tables.append(table.model_copy(update={"qa_text": qa_text or table.qa_text}))
    return collection.model_copy(update={"tables": tables})


def _parse_json(value: str):
    text = (value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)
