"""Qwen 显式提示词缓存 Provider 测试。"""

# ADD: Qwen 显式提示词缓存需求新增测试

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import ValidationError


def _make_model(**kwargs):
    """创建不访问网络的 Qwen 测试模型。

    Args:
        **kwargs: 覆盖 Provider 默认配置的参数。

    Returns:
        配置为 DashScope 兼容地址的 Qwen 模型实例。
    """
    from deerflow.models.qwen_provider import QwenChatModel

    return QwenChatModel(
        model="qwen3.6-plus",
        api_key="test-key",
        api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        **kwargs,
    )


def _find_system_message(payload: dict) -> dict:
    """从请求载荷中查找系统消息。

    Args:
        payload: Provider 生成的请求载荷。

    Returns:
        请求载荷中的首个系统消息。
    """
    return next(message for message in payload["messages"] if message["role"] == "system")


def test_prompt_caching_is_disabled_by_default() -> None:
    """默认关闭时不得改变系统消息结构，保持现有配置兼容。"""
    model = _make_model()

    payload = model._get_request_payload(
        [
            SystemMessage(content="稳定系统提示词"),
            HumanMessage(content="用户问题"),
        ]
    )

    system_message = _find_system_message(payload)
    assert system_message["content"] == "稳定系统提示词"


def test_enabled_prompt_caching_marks_string_system_message() -> None:
    """启用后应把字符串系统消息转换为带缓存断点的文本块。"""
    model = _make_model(enable_prompt_caching=True)

    payload = model._get_request_payload(
        [
            SystemMessage(content="稳定系统提示词"),
            HumanMessage(content="用户问题"),
        ]
    )

    system_message = _find_system_message(payload)
    assert system_message["content"] == [
        {
            "type": "text",
            "text": "稳定系统提示词",
            "cache_control": {"type": "ephemeral"},
        }
    ]


def test_enabled_prompt_caching_marks_only_last_text_block() -> None:
    """内容块系统消息只应在最后一个非空文本块上放置缓存断点。"""
    model = _make_model(enable_prompt_caching=True)
    payload = {
        "messages": [
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": "第一段"},
                    {"type": "text", "text": "第二段"},
                ],
            },
            {"role": "user", "content": "用户问题"},
        ]
    }

    model._apply_prompt_caching(payload)

    blocks = payload["messages"][0]["content"]
    assert "cache_control" not in blocks[0]
    assert blocks[1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in payload["messages"][1]


def test_prompt_caching_uses_last_system_message() -> None:
    """存在多条系统消息时应只标记最后一条，形成单一稳定缓存前缀。"""
    model = _make_model(enable_prompt_caching=True)
    payload = {
        "messages": [
            {"role": "system", "content": "第一条系统消息"},
            {"role": "system", "content": "第二条系统消息"},
            {"role": "user", "content": "用户问题"},
        ]
    }

    model._apply_prompt_caching(payload)

    assert payload["messages"][0]["content"] == "第一条系统消息"
    assert payload["messages"][1]["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_prompt_caching_is_noop_without_system_message() -> None:
    """没有系统消息时不得缓存用户问题或其他动态内容。"""
    model = _make_model(enable_prompt_caching=True)
    payload = {"messages": [{"role": "user", "content": "用户问题"}]}

    model._apply_prompt_caching(payload)

    assert payload == {"messages": [{"role": "user", "content": "用户问题"}]}


def test_prompt_caching_is_idempotent() -> None:
    """重复应用缓存策略不得增加缓存断点或改变消息内容。"""
    model = _make_model(enable_prompt_caching=True)
    payload = {"messages": [{"role": "system", "content": "稳定系统提示词"}]}

    model._apply_prompt_caching(payload)
    model._apply_prompt_caching(payload)

    assert payload["messages"][0]["content"] == [
        {
            "type": "text",
            "text": "稳定系统提示词",
            "cache_control": {"type": "ephemeral"},
        }
    ]


def test_prompt_cache_ttl_one_hour_is_forwarded() -> None:
    """配置一小时缓存时应向 DashScope 发送对应 TTL。"""
    model = _make_model(enable_prompt_caching=True, prompt_cache_ttl="1h")
    payload = {"messages": [{"role": "system", "content": "稳定系统提示词"}]}

    model._apply_prompt_caching(payload)

    assert payload["messages"][0]["content"][0]["cache_control"] == {
        "type": "ephemeral",
        "ttl": "1h",
    }


def test_qwen_provider_preserves_reasoning_content() -> None:
    """新增缓存适配不得破坏父类的多轮思考内容回放。"""
    model = _make_model(enable_prompt_caching=True)

    payload = model._get_request_payload(
        [
            SystemMessage(content="稳定系统提示词"),
            HumanMessage(content="第一轮问题"),
            AIMessage(
                content="第一轮回答",
                additional_kwargs={"reasoning_content": "第一轮思考"},
            ),
            HumanMessage(content="第二轮问题"),
        ]
    )

    assistant_message = next(message for message in payload["messages"] if message["role"] == "assistant")
    assert assistant_message["reasoning_content"] == "第一轮思考"
    assert _find_system_message(payload)["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_invalid_prompt_cache_ttl_is_rejected() -> None:
    """不受支持的缓存 TTL 应在模型创建阶段失败。"""
    with pytest.raises(ValidationError):
        _make_model(enable_prompt_caching=True, prompt_cache_ttl="30m")


def test_qwen_provider_uses_dashscope_secret_name() -> None:
    """序列化元数据应引用 DashScope 环境变量而不是 DeepSeek 环境变量。"""
    model = _make_model()

    assert model.lc_secrets["api_key"] == "DASHSCOPE_API_KEY"
    assert model.lc_secrets["openai_api_key"] == "DASHSCOPE_API_KEY"
