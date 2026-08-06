"""Structured human-input message metadata helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, NotRequired, TypedDict

HUMAN_INPUT_RESPONSE_KEY = "human_input_response"


class HumanInputTextResponse(TypedDict):
    version: Literal[1]
    kind: Literal["human_input_response"]
    source: str
    request_id: str
    flow_id: NotRequired[str]       # ADD: 意图标签审批需要唯一ID
    response_kind: Literal["text"]
    value: str


class HumanInputOptionResponse(TypedDict):
    version: Literal[1]
    kind: Literal["human_input_response"]
    source: str
    request_id: str
    flow_id: NotRequired[str]
    response_kind: Literal["option"]
    option_id: str
    value: str


HumanInputResponse = HumanInputTextResponse | HumanInputOptionResponse


def _non_empty_string(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def read_human_input_response(additional_kwargs: Mapping[str, object] | None) -> HumanInputResponse | None:
    """Read a valid human-input response payload from message metadata."""
    if not additional_kwargs:
        return None
    raw = additional_kwargs.get(HUMAN_INPUT_RESPONSE_KEY)
    if not isinstance(raw, Mapping):
        return None
    if raw.get("version") != 1 or raw.get("kind") != "human_input_response":
        return None

    source = _non_empty_string(raw.get("source"))
    request_id = _non_empty_string(raw.get("request_id"))
    flow_id = _non_empty_string(raw.get("flow_id"))
    value = _non_empty_string(raw.get("value"))
    if source is None or request_id is None or value is None:
        return None

    response_kind = raw.get("response_kind")
    if response_kind == "text":
        response: HumanInputTextResponse = {
            "version": 1,
            "kind": "human_input_response",
            "source": source,
            "request_id": request_id,
            "response_kind": "text",
            "value": value,
        }
        # ADD: 增加 flow_id 
        if flow_id is not None:
            response["flow_id"] = flow_id
        return response
    if response_kind == "option":
        option_id = _non_empty_string(raw.get("option_id"))
        if option_id is None:
            return None
        response: HumanInputOptionResponse = {
            "version": 1,
            "kind": "human_input_response",
            "source": source,
            "request_id": request_id,
            "response_kind": "option",
            "option_id": option_id,
            "value": value,
        }
        # ADD: 增加 flow_id 
        if flow_id is not None:
            response["flow_id"] = flow_id
        return response
    return None
