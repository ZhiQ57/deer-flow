"""VSSAG 阶段配置加载器。"""

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from .vfssag_sdk_config import VSSAGConfig

_ENV_REFERENCE_PATTERN = re.compile(
    r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}"
)
_SUPPORTED_ENVIRONMENTS = {"dev", "test", "prod"}
_ENVIRONMENT_ALIASES = {
    "development": "dev",
    "testing": "test",
    "production": "prod",
}

_PROCESS_ENV_PATHS: dict[str, tuple[str, ...]] = {
    "VSSAG_ENABLED": ("vssag", "enabled"),
    "VSSAG_PARSING_ENABLED": ("vssag", "parsing", "enabled"),
    "VSSAG_PARSING_ENGINE": ("vssag", "parsing", "engine"),
    "VSSAG_HEADING_CORRECTION_ENABLED": (
        "vssag",
        "parsing",
        "heading_correction_enabled",
    ),
    "VSSAG_MINERU_MODE": ("vssag", "parsing", "mineru", "mode"),
    "VSSAG_MINERU_API_URL": ("vssag", "parsing", "mineru", "api_url"),
    "VSSAG_MINERU_SERVER_URL": ("vssag", "parsing", "mineru", "server_url"),
    "VSSAG_MINERU_BACKEND": ("vssag", "parsing", "mineru", "backend"),
    "VSSAG_MINERU_TIMEOUT": ("vssag", "parsing", "mineru", "timeout_seconds"),
    "VSSAG_MINERU_POLL_INTERVAL": (
        "vssag",
        "parsing",
        "mineru",
        "poll_interval_seconds",
    ),
    "VSSAG_MINERU_MAX_RETRIES": (
        "vssag",
        "parsing",
        "mineru",
        "max_retries",
    ),
    "VSSAG_INDEX_ENABLED": ("vssag", "index", "enabled"),
    "VSSAG_LLM_MODEL": ("vssag", "index", "llm", "model"),
    "VSSAG_EMBEDDING_ENABLED": ("vssag", "index", "embedding", "enabled"),
    "VSSAG_VECTOR_BACKEND": ("vssag", "index", "embedding", "engine"),
    "VSSAG_LANCEDB_URI": ("vssag", "index", "embedding", "vector_store"),
    "VSSAG_PGVECTOR_DSN": ("vssag", "index", "embedding", "pgvector_dsn"),
    "VSSAG_EMBEDDING_PROVIDER": ("vssag", "index", "embedding", "provider"),
    "VSSAG_EMBEDDING_MODEL": ("vssag", "index", "embedding", "model_name"),
    "VSSAG_EMBEDDING_DIMENSION": (
        "vssag",
        "index",
        "embedding",
        "dimension",
    ),
    "VSSAG_GRAPH_INDEX_ENABLED": ("vssag", "index", "graph", "enabled"),
    "VSSAG_GRAPH_STORE_PATH": ("vssag", "index", "graph", "graph_store"),
    "VSSAG_GRAPH_INDEX_MAX_JOIN_HOPS": (
        "vssag",
        "index",
        "graph",
        "max_join_hops",
    ),
    "VSSAG_GRAPH_INCLUDE_CANDIDATES": (
        "vssag",
        "index",
        "graph",
        "include_candidates",
    ),
    "VSSAG_GRAPH_INDEX_MAX_ENTITIES": (
        "vssag",
        "index",
        "graph",
        "extraction_max_entities",
    ),
    "VSSAG_VFS_ENABLED": ("vssag", "retrieval", "vfs_enabled"),
    "VSSAG_SEARCH_TYPE": ("vssag", "retrieval", "search_type"),
    "VSSAG_VFS_ROOT_PREFIX": (
        "vssag",
        "retrieval",
        "vfs",
        "root_prefix",
    ),
    "VSSAG_VFS_MOUNT_NAME": (
        "vssag",
        "retrieval",
        "vfs",
        "mount_name",
    ),
    "VSSAG_JUST_BASH_WORKER_DIR": (
        "vssag",
        "retrieval",
        "vfs",
        "just_bash_worker_dir",
    ),
    "VSSAG_MAX_FULL_CHARS": (
        "vssag",
        "retrieval",
        "vfs",
        "max_full_chars",
    ),
    "VSSAG_MAX_GREP_RESULTS": (
        "vssag",
        "retrieval",
        "vfs",
        "max_grep_results",
    ),
    "VSSAG_MAX_FIND_DEPTH": (
        "vssag",
        "retrieval",
        "vfs",
        "max_find_depth",
    ),
    "VSSAG_VFS_ALLOWED_MODES": (
        "vssag",
        "retrieval",
        "vfs",
        "allowed_modes",
    ),
    "VSSAG_MAX_QUERY_SECONDS": (
        "vssag",
        "retrieval",
        "embedding_retriever",
        "max_query_seconds",
    ),
    "VSSAG_EMBEDDING_RETRIEVER_ENABLED": (
        "vssag",
        "retrieval",
        "embedding_retriever",
        "enabled",
    ),
    "VSSAG_EMBEDDING_RETRIEVER_TOP_K": (
        "vssag",
        "retrieval",
        "embedding_retriever",
        "top_k",
    ),
    "VSSAG_TEXT_RETRIEVER_ENABLED": (
        "vssag",
        "retrieval",
        "text_retriever",
        "enabled",
    ),
    "VSSAG_GRAPH_RETRIEVER_ENABLED": (
        "vssag",
        "retrieval",
        "graph_retriever",
        "enabled",
    ),
    "VSSAG_GRAPH_RETRIEVER_TOP_K": (
        "vssag",
        "retrieval",
        "graph_retriever",
        "top_k",
    ),
    "VSSAG_GRAPH_RESULT_RATIO_MIN": (
        "vssag",
        "retrieval",
        "graph_retriever",
        "result_ratio_min",
    ),
    "VSSAG_GRAPH_RESULT_RATIO_MAX": (
        "vssag",
        "retrieval",
        "graph_retriever",
        "result_ratio_max",
    ),
    "VSSAG_SQL_RETRIEVER_ENABLED": (
        "vssag",
        "retrieval",
        "sql_retriever",
        "enabled",
    ),
    "VSSAG_SQL_MAX_TABLES": (
        "vssag",
        "retrieval",
        "sql_retriever",
        "max_tables",
    ),
    "VSSAG_SQL_RETRIEVAL_MAX_JOIN_HOPS": (
        "vssag",
        "retrieval",
        "sql_retriever",
        "max_join_hops",
    ),
    "VSSAG_LANGFUSE_ENABLED": ("observability", "enabled"),
    "VSSAG_TRACES_PROVIDER": ("observability", "provider"),
}

_LEGACY_OVERRIDE_PATHS: dict[str, tuple[str, ...]] = {
    "parsing_enabled": ("parsing", "enabled"),
    "parsing_engine": ("parsing", "engine"),
    "heading_correction_enabled": ("parsing", "heading_correction_enabled"),
    "index_enabled": ("index", "enabled"),
    "embedding_enabled": ("index", "embedding", "enabled"),
    "vector_backend": ("index", "embedding", "engine"),
    "lancedb_uri": ("index", "embedding", "vector_store"),
    "pgvector_dsn": ("index", "embedding", "pgvector_dsn"),
    "embedding_provider": ("index", "embedding", "provider"),
    "embedding_model": ("index", "embedding", "model_name"),
    "embedding_dimension": ("index", "embedding", "dimension"),
    "llm_model": ("index", "llm", "model"),
    "openai_api_key": ("model", "api_key"),
    "openai_base_url": ("model", "base_url"),
    "rerank_model": ("model", "rerank_model"),
    "mineru_mode": ("parsing", "mineru", "mode"),
    "mineru_api_url": ("parsing", "mineru", "api_url"),
    "mineru_server_url": ("parsing", "mineru", "server_url"),
    "mineru_backend": ("parsing", "mineru", "backend"),
    "mineru_timeout": ("parsing", "mineru", "timeout_seconds"),
    "mineru_poll_interval": ("parsing", "mineru", "poll_interval_seconds"),
    "mineru_max_retries": ("parsing", "mineru", "max_retries"),
    "graph_index_enabled": ("index", "graph", "enabled"),
    "graph_index_max_join_hops": ("index", "graph", "max_join_hops"),
    "graph_include_candidates": ("index", "graph", "include_candidates"),
    "graph_max_entities": ("index", "graph", "extraction_max_entities"),
    "vfs_enabled": ("retrieval", "vfs_enabled"),
    "search_type": ("retrieval", "search_type"),
    "text_retriever_enabled": ("retrieval", "text_retriever", "enabled"),
    "embedding_retriever_enabled": (
        "retrieval",
        "embedding_retriever",
        "enabled",
    ),
    "sql_retriever_enabled": ("retrieval", "sql_retriever", "enabled"),
    "text_retriever_weight": (
        "retrieval",
        "retriever_weight",
        "text_retriever",
    ),
    "embedding_retriever_weight": (
        "retrieval",
        "retriever_weight",
        "embedding_retriever",
    ),
    "graph_weight": ("retrieval", "retriever_weight", "graph_retriever"),
    "sql_retriever_weight": ("retrieval", "retriever_weight", "sql_retriever"),
    "default_top_k": ("retrieval", "embedding_retriever", "top_k"),
    "max_query_seconds": (
        "retrieval",
        "embedding_retriever",
        "max_query_seconds",
    ),
    "max_full_chars": ("retrieval", "vfs", "max_full_chars"),
    "max_grep_results": ("retrieval", "vfs", "max_grep_results"),
    "max_find_depth": ("retrieval", "vfs", "max_find_depth"),
    "vfs_root_prefix": ("retrieval", "vfs", "root_prefix"),
    "vfs_mount_name": ("retrieval", "vfs", "mount_name"),
    "vfs_allowed_modes": ("retrieval", "vfs", "allowed_modes"),
    "sql_ability": ("index", "sql_ability"),
}


def _normalize_environment(value: str | None) -> str:
    """规范化运行阶段名称。

    Args:
        value: 外部传入的阶段名称。

    Returns:
        dev、test 或 prod。

    Raises:
        ValueError: 阶段名称不受支持。
    """
    environment = (value or "dev").strip().lower()
    environment = _ENVIRONMENT_ALIASES.get(environment, environment)
    if environment not in _SUPPORTED_ENVIRONMENTS:
        raise ValueError(f"不支持的 APP_ENV={environment!r}。")
    return environment


def _read_dotenv(path: Path) -> dict[str, str]:
    """读取 dotenv 文件。

    Args:
        path: dotenv 文件路径。

    Returns:
        过滤空值后的环境变量字典。
    """
    if not path.exists():
        return {}
    return {
        key: value
        for key, value in dotenv_values(path).items()
        if key and value is not None
    }


def _resolve_files() -> tuple[Path, dict[str, str]]:
    """解析当前阶段的 dotenv 和 YAML 文件。"""
    root = Path.cwd()
    base_values = _read_dotenv(root / ".env")
    explicit_file = os.getenv("ENV_FILE")
    explicit_values: dict[str, str] = {}
    explicit_path: Path | None = None
    if explicit_file:
        explicit_path = Path(explicit_file)
        if not explicit_path.is_absolute():
            explicit_path = root / explicit_path
        if not explicit_path.exists():
            raise FileNotFoundError(f"ENV_FILE 指向的文件不存在: {explicit_path}")
        explicit_values = _read_dotenv(explicit_path)

    environment = _normalize_environment(
        os.getenv("APP_ENV")
        or explicit_values.get("APP_ENV")
        or base_values.get("APP_ENV")
    )
    stage_values = _read_dotenv(root / f".env.{environment}")
    variables = {**base_values, **stage_values, **explicit_values}

    configured_file = os.getenv("CONFIG_FILE") or variables.get("CONFIG_FILE")
    config_path = Path(configured_file) if configured_file else root / f"config.{environment}.yaml"
    if not config_path.is_absolute():
        config_path = root / config_path
    if not config_path.exists():
        raise FileNotFoundError(f"VSSAG 阶段配置文件不存在: {config_path}")
    return config_path, variables


def _interpolate(value: Any, variables: Mapping[str, str], source: Path) -> Any:
    """递归替换 YAML 中的环境变量引用。"""
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            name = match.group("name")
            default = match.group("default")
            if name in variables:
                return variables[name]
            if default is not None:
                return default
            raise RuntimeError(f"{source} 引用了未定义环境变量 {name}。")

        return _ENV_REFERENCE_PATTERN.sub(replace, value)
    if isinstance(value, Mapping):
        return {key: _interpolate(item, variables, source) for key, item in value.items()}
    if isinstance(value, list):
        return [_interpolate(item, variables, source) for item in value]
    return value


def _load_yaml(path: Path) -> dict[str, Any]:
    """读取 YAML 配置文件。"""
    try:
        from ruamel.yaml import YAML
        with path.open("r", encoding="utf-8") as stream:
            document = YAML(typ="safe", pure=True).load(stream) or {}
    except Exception as exc:
        raise RuntimeError(f"无法读取 VSSAG 配置文件 {path}: {exc}") from exc
    if not isinstance(document, Mapping):
        raise TypeError(f"VSSAG 配置文件必须是 YAML 对象: {path}")
    return dict(document)


def _set_path(document: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    """设置配置文档中的一个嵌套字段。"""
    node = document
    for key in path[:-1]:
        child = node.get(key)
        if not isinstance(child, dict):
            child = {}
            node[key] = child
        node = child
    node[path[-1]] = value


def _parse_process_value(name: str, value: str) -> Any:
    """把进程环境变量转换成适合 Pydantic 的值。"""
    if name in {"VSSAG_VFS_ALLOWED_MODES"}:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = [item.strip() for item in value.split(",") if item.strip()]
        return parsed
    return value


def _apply_process_overrides(document: dict[str, Any]) -> None:
    """应用 SDK 专属进程环境变量覆盖。"""
    for name, path in _PROCESS_ENV_PATHS.items():
        value = os.getenv(name)
        if value is not None:
            _set_path(document, path, _parse_process_value(name, value))

    models = document.setdefault("models", {})
    default_provider = str(models.get("default_provider", "openai"))
    provider = models.setdefault(default_provider, {})
    if not isinstance(provider, dict):
        provider = {}
        models[default_provider] = provider
    if os.getenv("OPENAI_API_KEY"):
        provider["api_key"] = os.environ["OPENAI_API_KEY"]
    if os.getenv("OPENAI_BASE_URL"):
        provider["base_url"] = os.environ["OPENAI_BASE_URL"]
    if os.getenv("VSSAG_OPENAI_API_KEY"):
        provider["api_key"] = os.environ["VSSAG_OPENAI_API_KEY"]
    if os.getenv("VSSAG_OPENAI_BASE_URL"):
        provider["base_url"] = os.environ["VSSAG_OPENAI_BASE_URL"]


def _build_model_config(document: Mapping[str, Any]) -> dict[str, Any]:
    """把旧 YAML 的 models 段转换为 SDK 模型配置。"""
    models = document.get("models")
    if not isinstance(models, Mapping):
        models = {}
    providers = {
        name: value
        for name, value in models.items()
        if name not in {"default_provider", "embedding", "huggingface"}
        and isinstance(value, Mapping)
    }
    compatibility = document.get("compatibility")
    if not isinstance(compatibility, Mapping):
        compatibility = {}
    default_llm = compatibility.get("default_llm")
    if not isinstance(default_llm, Mapping):
        default_llm = {}
    models_block = default_llm.get("models")
    if not isinstance(models_block, Mapping):
        models_block = {}
    rerank_model = str(models_block.get("rerank", ""))
    default_provider = str(models.get("default_provider", "openai"))
    rerank_provider = str(models.get("rerank_provider", ""))
    default_config = providers.get(default_provider)
    if not isinstance(default_config, Mapping):
        default_config = {}
    return {
        "default_provider": default_provider,
        "rerank_provider": rerank_provider,
        "api_key": str(default_config.get("api_key", "")),
        "base_url": str(default_config.get("base_url", "")),
        "providers": providers,
        "embedding": models.get("embedding", {}),
        "rerank_model": rerank_model,
    }


def _normalize_overrides(overrides: Mapping[str, Any]) -> dict[str, Any]:
    """兼容旧的扁平覆盖字段并转换为新的分层结构。"""
    normalized: dict[str, Any] = {}
    for key, value in overrides.items():
        path = _LEGACY_OVERRIDE_PATHS.get(key)
        if path is None:
            normalized[key] = value
            continue
        current = normalized
        for item in path[:-1]:
            current = current.setdefault(item, {})
        current[path[-1]] = value
    return normalized


def load_vssag_config(
    *,
    overrides: Mapping[str, Any] | None = None,
) -> VSSAGConfig:
    """加载默认 VSSAG 配置并应用一次性运行时覆盖。

    Args:
        overrides: 数据库管理员配置或任务级配置覆盖。

    Returns:
        经过 YAML、进程环境和运行时覆盖校验的 VSSAGConfig。
    """
    config_path, dotenv_values_map = _resolve_files()
    variables = {**dotenv_values_map, **os.environ}
    document = _interpolate(_load_yaml(config_path), variables, config_path)
    _apply_process_overrides(document)

    vssag = document.get("vssag")
    if not isinstance(vssag, Mapping):
        vssag = {}
    values = dict(vssag)
    values["model"] = _build_model_config(document)
    values["observability"] = document.get("observability", {})

    config = VSSAGConfig.model_validate(values)
    if overrides:
        config = config.with_overrides(_normalize_overrides(overrides))
    return config
