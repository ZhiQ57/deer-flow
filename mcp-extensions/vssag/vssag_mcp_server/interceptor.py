"""仅向私有 stdio MCP 传递宿主已认证的运行上下文。"""

from __future__ import annotations

import json
from collections.abc import Mapping

CONTEXT_HEADER = "X-DeerFlow-VSSAG-Context"
MAX_CONTEXT_BYTES = 16_384
IDENTITY_FIELDS = frozenset({"user_id", "user_role", "authz_attributes", "thread_id", "run_id"})


def build_context_interceptor():
    """构建只处理固定 vssag 服务的 MCP 拦截器。

    Args:
        无；通过 DeerFlow 的 mcpInterceptors 配置加载。
    Returns:
        在工具参数之外注入可信上下文的异步函数。
    """

    async def inject_context(request, handler):
        """传递当前调用身份，不修改检索参数或其他服务。

        Args:
            request: DeerFlow MCP 工具请求，runtime 由宿主注入。
            handler: 现有 MCP 传输链的后续处理函数。
        Returns:
            原 MCP 工具调用结果。
        """
        if request.server_name != "vssag":
            return await handler(request)
        if IDENTITY_FIELDS.intersection(request.args):
            raise ValueError("VSSAG identity fields must not be supplied as tool arguments")
        context = getattr(getattr(request, "runtime", None), "context", None)
        if not isinstance(context, Mapping):
            raise ValueError("VSSAG requires authenticated runtime context")
        values = {name: context.get(name) for name in IDENTITY_FIELDS}
        for name in ("user_id", "user_role", "thread_id", "run_id"):
            if not isinstance(values[name], str) or not values[name].strip():
                raise ValueError(f"VSSAG runtime context is missing {name}")
        attributes = values["authz_attributes"]
        if attributes is None:
            values["authz_attributes"] = {}
        elif not isinstance(attributes, dict):
            raise ValueError("VSSAG runtime context authz_attributes must be an object")
        serialized = json.dumps(values, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        if len(serialized.encode("ascii")) > MAX_CONTEXT_BYTES:
            raise ValueError("VSSAG runtime context exceeds the metadata limit")
        headers = {name: value for name, value in (request.headers or {}).items() if name.lower() != CONTEXT_HEADER.lower()}
        headers[CONTEXT_HEADER] = serialized
        return await handler(request.override(headers=headers))

    return inject_context
