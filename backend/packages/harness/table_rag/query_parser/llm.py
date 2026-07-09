"""基于外部 LLM provider 的查询扩展适配。"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from ..providers.llms import LLMProvider, call_llm
from ..schemas import ParsedQuery, QueryExpansion
from .base import QueryExpansionProvider

PromptBuilder = Callable[[str, ParsedQuery | None], str]
ResponseParser = Callable[[str], QueryExpansion]


class LLMQueryExpansionProvider(QueryExpansionProvider):
    """基于外部 LLM 的查询扩展提供器。"""

    def __init__(
        self,
        llm_provider: LLMProvider,
        prompt_builder: PromptBuilder,
        *,
        response_parser: ResponseParser | None = None,
        llm_kwargs: dict[str, Any] | None = None,
    ):
        """初始化 LLM 查询扩展提供器。

        Args:
            llm_provider: 外部注入的 LLM provider。
            prompt_builder: 提示词构造函数。
            response_parser: 可选 LLM 返回解析函数；为空时使用默认 JSON/列表解析。
            llm_kwargs: 传给外部 LLM provider 的固定参数。

        Returns:
            None。
        """
        self.llm_provider = llm_provider
        self.prompt_builder = prompt_builder
        self.response_parser = response_parser or parse_llm_query_expansion
        self.llm_kwargs = dict(llm_kwargs or {})

    def expand(self, query: str, parsed_query: ParsedQuery | None = None) -> QueryExpansion:
        """调用外部 LLM 生成查询扩展结果。"""
        prompt = self.prompt_builder(query, parsed_query)
        response_text = call_llm(self.llm_provider, prompt, **self.llm_kwargs)
        return self.response_parser(response_text)


def parse_llm_query_expansion(text: str) -> QueryExpansion:
    """解析 LLM 返回的查询扩展文本。

    Args:
        text: LLM 返回文本。

    Returns:
        查询扩展结果。
    """
    payload = _load_json_like(text)
    if isinstance(payload, dict):
        return QueryExpansion(
            metrics=_string_list(payload.get("metrics")),
            dimensions=_string_list(payload.get("dimensions")),
            entities=_string_list(payload.get("entities")),
            filters=_string_list(payload.get("filters")),
            time_expressions=_string_list(payload.get("time_expressions")),
            keywords=_dedupe(
                [
                    *_string_list(payload.get("keywords")),
                    *_string_list(payload.get("schema_keywords")),
                    *_string_list(payload.get("field_names")),
                    *_string_list(payload.get("candidate_columns")),
                ]
            ),
        )
    if isinstance(payload, list):
        return QueryExpansion(keywords=_string_list(payload))
    return QueryExpansion(keywords=_parse_plain_keyword_list(text))


def _load_json_like(text: str) -> Any:
    """尝试读取 JSON 结构；失败时返回 None。"""
    cleaned = str(text).strip()
    if not cleaned:
        return None
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


def _string_list(value: object) -> list[str]:
    """把 LLM 返回字段转换为字符串列表。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        return []
    return _dedupe(str(item).strip() for item in value if str(item).strip())


def _parse_plain_keyword_list(text: str) -> list[str]:
    """兼容解析非 JSON 的简单列表文本。"""
    cleaned = str(text).strip().strip("[]")
    parts = [
        item.strip().strip("'\"")
        for line in cleaned.splitlines()
        for item in line.split(",")
    ]
    return _dedupe(part for part in parts if part)


def _dedupe(values) -> list[str]:
    """按原始顺序去重。"""
    return list(dict.fromkeys(value for value in values if value))


__all__ = [
    "LLMQueryExpansionProvider",
    "PromptBuilder",
    "ResponseParser",
    "parse_llm_query_expansion",
]
