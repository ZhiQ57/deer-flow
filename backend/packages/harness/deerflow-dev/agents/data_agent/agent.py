"""DataAgent 实验性图工厂。"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.runnables import RunnableConfig
from tools.builtins import get_data_agent_tools
from tools.constants import (
    is_readonly_tablerag_tool_name,
    is_tablerag_retrieval_tool_name,
)

from agents.middlewares.data_agent_orchestration_middleware import DataAgentOrchestrationMiddleware
from agents.middlewares.data_agent_turn_reset_middleware import DataAgentTurnResetMiddleware
from agents.thread_state import DataAgentState
from deerflow.agents.factory import create_deerflow_agent
from deerflow.agents.lead_agent.agent import (
    _get_runtime_config,
    _load_enabled_skills_for_tool_policy,
    _resolve_model_name,
)
from deerflow.agents.lead_agent.agent import (
    build_middlewares as build_lead_middlewares,
)
from deerflow.agents.lead_agent.prompt import apply_prompt_template
from deerflow.agents.middlewares.query_labels_middleware import QueryLabelsMiddleware
from deerflow.config.agents_config import load_agent_config, validate_agent_name
from deerflow.config.app_config import AppConfig, get_app_config
from deerflow.models import create_chat_model
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.skills.describe import build_skill_search_setup
from deerflow.skills.tool_policy import ALWAYS_AVAILABLE_BUILTIN_TOOL_NAMES, filter_tools_by_skill_allowed_tools
from deerflow.tools.mcp_metadata import is_mcp_tool
from deerflow.tracing import build_tracing_callbacks

from .constants import (
    DATA_AGENT_NAME,
    DATA_AGENT_SAFE_LOCAL_TOOL_NAMES,
    DATA_AGENT_SKILLS,
    DATA_AGENT_TOOL_GROUPS,
)
from .prompt import build_data_agent_prompt_appendix

logger = logging.getLogger(__name__)

_NON_INTERACTIVE_DISABLED_TOOL_NAMES = frozenset({"ask_clarification"})


def _runtime_bounded_int(
    cfg: Mapping[str, Any],
    name: str,
    default: int,
    *,
    maximum: int,
) -> int:
    """读取有边界的 DataAgent 整数运行参数。

    Args:
        cfg: 合并后的运行时配置。
        name: 参数名。
        default: 默认值。
        maximum: 最大值。

    Return:
        校验后的正整数。
    """
    value = cfg.get(name, default)
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if not 1 <= parsed <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}.")
    return parsed


def _runtime_allowed_subagents(cfg: Mapping[str, Any]) -> frozenset[str]:
    """读取 DataAgent 允许委托的受限自定义子代理。

    Args:
        cfg: 合并后的运行时配置。

    Return:
        子代理名称集合。
    """
    value = cfg.get("data_agent_allowed_subagents")
    if value is None:
        return frozenset()
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise ValueError("data_agent_allowed_subagents must be a list of subagent names.")
    names = frozenset(str(item).strip() for item in value if str(item).strip())
    if len(names) > 10 or any(len(name) > 100 for name in names):
        raise ValueError("data_agent_allowed_subagents exceeds the allowed size.")
    return names


def _validate_allowed_subagents(
    names: frozenset[str],
    *,
    app_config: AppConfig,
) -> None:
    """校验 DataAgent 子代理必须采用显式只读工具白名单。

    Args:
        names: 允许运行的子代理名称。
        app_config: DeerFlow 应用配置。

    Return:
        None。
    """
    from deerflow.subagents.registry import get_subagent_config

    for name in names:
        subagent = get_subagent_config(name, app_config=app_config)
        if subagent is None:
            raise ValueError(f"Unknown DataAgent subagent: {name}")
        if subagent.tools is None:
            raise ValueError(f"DataAgent subagent '{name}' must declare an explicit tools allowlist.")
        unsafe_tools = [tool_name for tool_name in subagent.tools if not is_readonly_tablerag_tool_name(tool_name)]
        if unsafe_tools:
            raise ValueError(f"DataAgent subagent '{name}' contains unsafe tools: {', '.join(sorted(unsafe_tools))}")


def _load_optional_agent_config(agent_name: str, *, user_id: str | None = None):
    """读取原生 custom-agent 配置。

    Args:
        agent_name: DataAgent 名称。
        user_id: 用户 ID。

    Return:
        AgentConfig；文件不存在时返回 None。
    """
    try:
        return load_agent_config(agent_name, user_id=user_id)
    except FileNotFoundError:
        logger.info("DataAgent runtime config not found; using experimental defaults: %s", agent_name)
        return None
    except Exception as exc:
        raise RuntimeError(f"Failed to load DataAgent runtime config for '{agent_name}'.") from exc


def _resolve_agent_skills(agent_config: Any) -> set[str]:
    """解析并校验 DataAgent Skill 白名单，保留显式空列表。

    Args:
        agent_config: 可选 AgentConfig。

    Return:
        Skill 名称集合。
    """
    if agent_config is None or agent_config.skills is None:
        return set(DATA_AGENT_SKILLS)
    skills = set(agent_config.skills)
    unsupported = skills - DATA_AGENT_SKILLS
    if unsupported:
        raise ValueError(f"DataAgent config contains unsupported skills: {', '.join(sorted(unsupported))}")
    return skills


def _resolve_agent_tool_groups(agent_config: Any) -> list[str]:
    """解析并校验 DataAgent 工具组，保留显式空列表。

    Args:
        agent_config: 可选 AgentConfig。

    Return:
        工具组列表。
    """
    if agent_config is None or agent_config.tool_groups is None:
        return list(DATA_AGENT_TOOL_GROUPS)
    tool_groups = list(agent_config.tool_groups)
    unsupported = set(tool_groups) - set(DATA_AGENT_TOOL_GROUPS)
    if unsupported:
        raise ValueError(f"DataAgent config contains unsupported tool groups: {', '.join(sorted(unsupported))}")
    return tool_groups


def _filter_data_agent_tools(tools: list[Any]) -> list[Any]:
    """隔离 DataAgent MCP 工具面。

    非 MCP 工具继续按 DeerFlow 工具组和 Skill 策略处理；MCP 工具只允许
    TableRAG 只读检索/索引校验工具，明确排除其他 MCP 和索引变更工具。

    Args:
        tools: DeerFlow 原始工具列表。

    Return:
        DataAgent 可见工具列表。
    """
    return [tool for tool in tools if (is_mcp_tool(tool) and is_readonly_tablerag_tool_name(tool.name)) or (not is_mcp_tool(tool) and tool.name in DATA_AGENT_SAFE_LOCAL_TOOL_NAMES)]


def _has_tablerag_tools(tools: list[Any]) -> bool:
    """判断工具列表是否包含只读 TableRAG 工具。

    Args:
        tools: 工具列表。

    Return:
        至少包含一个只读 TableRAG MCP 工具时返回 True。
    """
    return any(is_mcp_tool(tool) and is_tablerag_retrieval_tool_name(tool.name) for tool in tools)


def _append_unique_tools(tools: list[Any], extra_tools: list[Any]) -> list[Any]:
    """按名称追加工具并避免重复注册。

    Args:
        tools: 已有工具。
        extra_tools: 待追加工具。

    Return:
        去重后的工具列表。
    """
    result = list(tools)
    names = {tool.name for tool in result}
    for tool in extra_tools:
        if tool.name in names:
            raise ValueError(f"Duplicate DataAgent tool name: {tool.name}")
        result.append(tool)
        names.add(tool.name)
    return result


def _insert_data_middlewares(
    middlewares: list[AgentMiddleware],
    turn_reset: DataAgentTurnResetMiddleware,
    query_labels: QueryLabelsMiddleware,
    orchestration: DataAgentOrchestrationMiddleware,
) -> list[AgentMiddleware]:
    """把 DataAgent 编排 middleware 插入到 lead-agent 动态上下文前。

    Args:
        middlewares: 原生 lead-agent middleware 链。
        turn_reset: 新用户轮次状态重置 middleware。
        query_labels: 查询标签发布 middleware。
        orchestration: 编排提示 middleware。

    Return:
        插入 DataAgent middleware 后的新链。
    """
    result = list(middlewares)
    insert_at = next((index for index, item in enumerate(result) if type(item).__name__ == "DynamicContextMiddleware"), len(result))
    result[insert_at:insert_at] = [turn_reset, query_labels, orchestration]
    return result


def build_data_middlewares(
    config: RunnableConfig,
    *,
    model_name: str | None,
    available_skills: set[str],
    app_config: AppConfig,
    deferred_setup: Any = None,
    user_id: str | None = None,
    subagent_enabled: bool = False,
    allowed_subagents: frozenset[str] = frozenset(),
    max_retrieval_calls: int = 6,
    max_sql_validation_calls: int = 4,
    max_sql_execution_calls: int = 2,
    max_chart_calls: int = 2,
) -> list[AgentMiddleware]:
    """构造 DataAgent middleware 链。

    Args:
        config: LangGraph 运行配置。
        model_name: 已解析模型名。
        available_skills: DataAgent 可用 Skill 白名单。
        app_config: DeerFlow 应用配置。
        deferred_setup: deferred MCP tool 设置。
        user_id: 用户 ID。
        subagent_enabled: 是否启用 task 子代理工具。
        allowed_subagents: 允许委托的受限自定义子代理名称。
        max_retrieval_calls: 单轮最大 TableRAG 检索调用数。
        max_sql_validation_calls: 单轮最大 SQL 校验调用数。
        max_sql_execution_calls: 单轮最大 SQL 执行调用数。
        max_chart_calls: 单轮最大 ChartSpec 调用数。
    Return:
        DataAgent middleware 实例列表。
    """
    lead_middlewares = build_lead_middlewares(
        config,
        model_name=model_name,
        agent_name=DATA_AGENT_NAME,
        available_skills=available_skills,
        app_config=app_config,
        deferred_setup=deferred_setup,
        user_id=user_id,
    )
    return _insert_data_middlewares(
        lead_middlewares,
        DataAgentTurnResetMiddleware(),
        QueryLabelsMiddleware(),
        DataAgentOrchestrationMiddleware(
            subagent_enabled=subagent_enabled,
            allowed_subagents=allowed_subagents,
            max_retrieval_calls=max_retrieval_calls,
            max_sql_validation_calls=max_sql_validation_calls,
            max_sql_execution_calls=max_sql_execution_calls,
            max_chart_calls=max_chart_calls,
        ),
    )


def build_data_agent(
    config: RunnableConfig | None = None,
    *,
    app_config: AppConfig | None = None,
    checkpointer=None,
):
    """创建 DataAgent 实验性 LangGraph 图。

    该函数复用 DeerFlow 原生 lead-agent 的模型、工具、Skill、MCP、prompt
    和绝大部分 middleware 组装逻辑，但最终通过 `create_deerflow_agent(...)`
    重新创建图，以便插入 DataAgent 专属编排 middleware。

    Args:
        config: LangGraph 运行配置。
        app_config: 可选 DeerFlow 应用配置；为空时读取 `config.yaml`。
        checkpointer: 可选 LangGraph checkpointer。

    Return:
        编译后的 DataAgent 图。
    """
    from deerflow.tools import get_available_tools
    from deerflow.tools.builtins.tool_search import assemble_deferred_tools, get_mcp_routing_hints_prompt_section

    runtime_config: RunnableConfig = config or {}
    cfg = _get_runtime_config(runtime_config)
    resolved_app_config = app_config or cfg.get("app_config") or get_app_config()

    runtime_user_id = cfg.get("user_id")
    resolved_user_id = str(runtime_user_id) if runtime_user_id else get_effective_user_id()
    requested_agent_name = validate_agent_name(cfg.get("agent_name") or DATA_AGENT_NAME) or DATA_AGENT_NAME
    if requested_agent_name != DATA_AGENT_NAME:
        raise ValueError(f"Experimental DataAgent only supports agent_name='{DATA_AGENT_NAME}'.")
    agent_config = _load_optional_agent_config(DATA_AGENT_NAME, user_id=resolved_user_id)
    if agent_config is not None and agent_config.name != DATA_AGENT_NAME:
        raise ValueError(f"DataAgent config name must be '{DATA_AGENT_NAME}'.")

    thinking_enabled = cfg.get("thinking_enabled", True)
    reasoning_effort = cfg.get("reasoning_effort", None)
    requested_model_name: str | None = cfg.get("model_name") or cfg.get("model")
    subagent_enabled = bool(cfg.get("subagent_enabled", False))
    allowed_subagents = _runtime_allowed_subagents(cfg)
    if subagent_enabled and not allowed_subagents:
        raise ValueError("subagent_enabled=true requires non-empty data_agent_allowed_subagents.")
    if allowed_subagents and not subagent_enabled:
        raise ValueError("data_agent_allowed_subagents requires subagent_enabled=true.")
    if subagent_enabled:
        _validate_allowed_subagents(allowed_subagents, app_config=resolved_app_config)
    max_concurrent_subagents = int(cfg.get("max_concurrent_subagents", 3))
    if not 1 <= max_concurrent_subagents <= 20:
        raise ValueError("max_concurrent_subagents must be between 1 and 20.")
    non_interactive = bool(cfg.get("non_interactive", False))
    require_table_rag = bool(cfg.get("data_agent_require_table_rag", True))
    max_retrieval_calls = _runtime_bounded_int(cfg, "data_agent_max_retrieval_calls", 6, maximum=20)
    max_sql_validation_calls = _runtime_bounded_int(cfg, "data_agent_max_sql_validation_calls", 4, maximum=10)
    max_sql_execution_calls = _runtime_bounded_int(cfg, "data_agent_max_sql_execution_calls", 2, maximum=5)
    max_chart_calls = _runtime_bounded_int(cfg, "data_agent_max_chart_calls", 2, maximum=5)

    available_skills = _resolve_agent_skills(agent_config)
    tool_groups = _resolve_agent_tool_groups(agent_config)
    agent_model_name = agent_config.model if agent_config and agent_config.model else None
    model_name = _resolve_model_name(requested_model_name or agent_model_name, app_config=resolved_app_config)
    model_config = resolved_app_config.get_model_config(model_name)
    if model_config is None:
        raise ValueError("No chat model could be resolved for DataAgent.")
    if thinking_enabled and not model_config.supports_thinking:
        logger.warning("DataAgent model %s does not support thinking; disabling thinking.", model_name)
        thinking_enabled = False

    runtime_config.setdefault("metadata", {})
    runtime_config["metadata"].update(
        {
            "agent_name": DATA_AGENT_NAME,
            "model_name": model_name,
            "thinking_enabled": thinking_enabled,
            "reasoning_effort": reasoning_effort,
            "subagent_enabled": subagent_enabled,
            "data_agent_allowed_subagents": sorted(allowed_subagents),
            "tool_groups": tool_groups,
            "available_skills": sorted(available_skills),
            "experimental": "data-agent",
            "data_agent_require_table_rag": require_table_rag,
            "data_agent_max_retrieval_calls": max_retrieval_calls,
            "data_agent_max_sql_validation_calls": max_sql_validation_calls,
            "data_agent_max_sql_execution_calls": max_sql_execution_calls,
            "data_agent_max_chart_calls": max_chart_calls,
        }
    )

    tracing_callbacks = build_tracing_callbacks()
    if tracing_callbacks:
        existing_callbacks = runtime_config.get("callbacks") or []
        if not isinstance(existing_callbacks, list):
            existing_callbacks = list(existing_callbacks)
        runtime_config["callbacks"] = [*existing_callbacks, *tracing_callbacks]

    skills_for_tool_policy = _load_enabled_skills_for_tool_policy(available_skills, app_config=resolved_app_config, user_id=resolved_user_id)
    skill_setup = build_skill_search_setup(
        skills_for_tool_policy,
        enabled=resolved_app_config.skills.deferred_discovery,
        container_base_path=resolved_app_config.skills.container_path,
    )

    raw_tools = _filter_data_agent_tools(
        get_available_tools(
            model_name=model_name,
            groups=tool_groups,
            subagent_enabled=subagent_enabled,
            app_config=resolved_app_config,
        )
    )
    if require_table_rag and not _has_tablerag_tools(raw_tools):
        raise RuntimeError("DataAgent requires at least one read-only TableRAG MCP tool. Enable the tablerag MCP server and initialize its tool cache before building the agent.")
    filtered_tools = filter_tools_by_skill_allowed_tools(
        raw_tools,
        skills_for_tool_policy,
        always_allowed_tool_names=ALWAYS_AVAILABLE_BUILTIN_TOOL_NAMES,
    )
    if require_table_rag and not _has_tablerag_tools(filtered_tools):
        raise RuntimeError("DataAgent TableRAG tools were removed by the active Skill tool policy. Check the enabled DataAgent skills and their allowed-tools metadata.")
    filtered_tools = _append_unique_tools(filtered_tools, get_data_agent_tools())
    if non_interactive:
        filtered_tools = [tool for tool in filtered_tools if tool.name not in _NON_INTERACTIVE_DISABLED_TOOL_NAMES]
    final_tools, deferred_setup = assemble_deferred_tools(filtered_tools, enabled=resolved_app_config.tool_search.enabled)
    if skill_setup.describe_skill_tool:
        final_tools = _append_unique_tools(final_tools, [skill_setup.describe_skill_tool])

    mcp_routing_hints_section = get_mcp_routing_hints_prompt_section(filtered_tools, deferred_names=deferred_setup.deferred_names)
    lead_prompt = apply_prompt_template(
        subagent_enabled=subagent_enabled,
        max_concurrent_subagents=max_concurrent_subagents,
        agent_name=DATA_AGENT_NAME,
        available_skills=available_skills,
        app_config=resolved_app_config,
        deferred_names=deferred_setup.deferred_names,
        mcp_routing_hints_section=mcp_routing_hints_section,
        user_id=resolved_user_id,
        skill_names=skill_setup.skill_names or None,
    )
    system_prompt = "\n\n".join(
        [
            lead_prompt,
            build_data_agent_prompt_appendix(
                subagent_enabled=subagent_enabled,
                allowed_subagents=allowed_subagents,
            ),
        ]
    )

    return create_deerflow_agent(
        model=create_chat_model(
            name=model_name,
            thinking_enabled=thinking_enabled,
            reasoning_effort=reasoning_effort,
            app_config=resolved_app_config,
            attach_tracing=False,
        ),
        tools=final_tools,
        middleware=build_data_middlewares(
            runtime_config,
            model_name=model_name,
            available_skills=available_skills,
            app_config=resolved_app_config,
            deferred_setup=deferred_setup,
            user_id=resolved_user_id,
            subagent_enabled=subagent_enabled,
            allowed_subagents=allowed_subagents,
            max_retrieval_calls=max_retrieval_calls,
            max_sql_validation_calls=max_sql_validation_calls,
            max_sql_execution_calls=max_sql_execution_calls,
            max_chart_calls=max_chart_calls,
        ),
        system_prompt=system_prompt,
        state_schema=DataAgentState,
        checkpointer=checkpointer,
        name=DATA_AGENT_NAME,
    )


def make_data_agent(config: RunnableConfig):
    """LangGraph 风格 DataAgent 工厂入口。

    Args:
        config: LangGraph 运行配置。

    Return:
        编译后的 DataAgent 图。
    """
    runtime_config = _get_runtime_config(config)
    runtime_app_config = runtime_config.get("app_config")
    return build_data_agent(config, app_config=runtime_app_config or get_app_config())
