"""DashScope Qwen 模型适配器，支持显式提示词缓存。"""

# ADD: Qwen 显式提示词缓存需求新增

from __future__ import annotations

from typing import Any, Literal

from langchain_core.language_models import LanguageModelInput
from pydantic import Field

from deerflow.models.patched_deepseek import PatchedChatDeepSeek


class QwenChatModel(PatchedChatDeepSeek):
    """Qwen OpenAI 兼容接口适配器。

    该适配器继承 ``PatchedChatDeepSeek`` 的多轮思考内容回放能力，仅在模型配置
    显式启用时，为最后一条系统消息增加 DashScope ``cache_control`` 缓存断点。
    用户问题、记忆、工具结果和审批结果等动态消息不会被标记。

    Attributes:
        enable_prompt_caching: 是否启用 DashScope 显式提示词缓存。
        prompt_cache_ttl: 缓存有效期，仅支持五分钟或一小时。
    """

    enable_prompt_caching: bool = Field(default=False, description="是否启用 DashScope 显式提示词缓存")
    prompt_cache_ttl: Literal["5m", "1h"] = Field(default="5m", description="DashScope 显式提示词缓存有效期")

    @property
    def lc_secrets(self) -> dict[str, str]:
        """声明 Qwen Provider 使用的环境变量名称。

        Returns:
            LangChain 序列化时用于隐藏 DashScope API Key 的字段映射。
        """
        return {
            "api_key": "DASHSCOPE_API_KEY",
            "openai_api_key": "DASHSCOPE_API_KEY",
        }

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """生成包含可选显式缓存断点的模型请求。

        Args:
            input_: LangChain 模型输入。
            stop: 可选停止词列表。
            **kwargs: 传递给父类请求构造逻辑的附加参数。

        Returns:
            保留思考内容并按配置添加缓存断点的 DashScope 请求载荷。
        """
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if self.enable_prompt_caching:
            self._apply_prompt_caching(payload)
        return payload

    def _apply_prompt_caching(self, payload: dict) -> None:
        """为最后一条系统消息添加单个 DashScope 显式缓存断点。

        缓存断点只放在最后一条系统消息的最后一个非空文本块上。这样缓存前缀
        覆盖稳定系统提示词，同时不会把后续用户消息和工具结果纳入缓存创建范围。

        Args:
            payload: 已由 OpenAI 兼容客户端序列化的请求载荷。

        Returns:
            无返回值；直接修改请求载荷中的系统消息。
        """
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return

        for message in reversed(messages):
            if not isinstance(message, dict) or message.get("role") != "system":
                continue

            content = message.get("content")
            if isinstance(content, str):
                if not content:
                    return
                message["content"] = [
                    {
                        "type": "text",
                        "text": content,
                        "cache_control": self._build_cache_control(),
                    }
                ]
                return

            if isinstance(content, list):
                for block in reversed(content):
                    if not isinstance(block, dict):
                        continue
                    text = block.get("text")
                    if block.get("type") != "text" or not isinstance(text, str) or not text:
                        continue
                    block["cache_control"] = self._build_cache_control()
                    return
            return

    def _build_cache_control(self) -> dict[str, str]:
        """构建 DashScope 缓存控制参数。

        Returns:
            五分钟或一小时有效期的 ``cache_control`` 参数。
        """
        cache_control = {"type": "ephemeral"}
        if self.prompt_cache_ttl == "1h":
            cache_control["ttl"] = "1h"
        return cache_control
