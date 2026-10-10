from __future__ import annotations

import re

from ...prompts import render_prompt


def extract_keywords(text: str, limit: int = 12) -> list[str]:
    words = re.findall(r"[\u4e00-\u9fff]{2,8}|[A-Za-z][A-Za-z0-9_-]{2,}", text or "")
    stop = {"这是", "我们", "可以", "以及", "the", "and", "with", "from", "that"}
    result: list[str] = []
    seen: set[str] = set()
    for word in words:
        key = word.lower()
        if key in stop or key in seen:
            continue
        seen.add(key)
        result.append(word)
        if len(result) >= limit:
            break
    return result


async def extract_keywords_with_llm(
    text: str,
    llm,
    limit: int = 12,
) -> list[str]:
    """使用提示词抽取关键词；模型输出无效时回退到本地抽取。"""

    if llm is None:
        return extract_keywords(text, limit=limit)
    prompt = render_prompt(
        "keyword_extraction.md",
        content=text,
        topn=max(1, int(limit)),
    )
    try:
        response = await llm.complete(prompt, temperature=0.0)
        values = re.split(r"[,，\n]+", response or "")
        keywords: list[str] = []
        seen: set[str] = set()
        for value in values:
            keyword = value.strip().strip("-*`'\" ")
            key = keyword.lower()
            if not keyword or key in seen:
                continue
            seen.add(key)
            keywords.append(keyword)
            if len(keywords) >= max(1, int(limit)):
                break
        return keywords or extract_keywords(text, limit=limit)
    except Exception:
        return extract_keywords(text, limit=limit)


__all__ = ["extract_keywords", "extract_keywords_with_llm"]
