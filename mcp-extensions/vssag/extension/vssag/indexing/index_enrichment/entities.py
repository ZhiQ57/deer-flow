from __future__ import annotations

import json
import re

from ...prompts import render_prompt


def extract_entities(text: str, limit: int = 15) -> list[dict[str, str]]:
    words = re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Z][A-Za-z0-9_-]{2,}", text or "")
    seen: set[str] = set()
    entities: list[dict[str, str]] = []
    for word in words:
        key = word.lower()
        if key in seen:
            continue
        seen.add(key)
        entities.append({"name": word, "entity_type": "concept", "description": ""})
        if len(entities) >= limit:
            break
    return entities


async def extract_entities_with_llm(
    text: str,
    llm,
    limit: int = 15,
) -> list[dict[str, str]]:
    """使用提示词抽取实体；模型输出无效时回退到本地抽取。"""

    if llm is None:
        return extract_entities(text, limit=limit)
    prompt = render_prompt("entity_extraction.md", content=text)
    try:
        response = await llm.complete(prompt, temperature=0.0)
        payload = _parse_json(response)
        if not isinstance(payload, list):
            raise ValueError("实体抽取结果必须是 JSON 数组")
        entities = []
        seen: set[str] = set()
        for item in payload:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            entities.append(
                {
                    "name": name,
                    "entity_type": str(item.get("entity_type", "concept")).strip()
                    or "concept",
                    "description": str(item.get("description", "")).strip(),
                }
            )
            if len(entities) >= max(1, int(limit)):
                break
        return entities or extract_entities(text, limit=limit)
    except Exception:
        return extract_entities(text, limit=limit)


def _parse_json(value: str):
    text = (value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


__all__ = ["extract_entities", "extract_entities_with_llm"]
