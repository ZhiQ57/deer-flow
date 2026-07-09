"""TableRAG 运行时支撑模块。"""

_RUNTIME_EXPORTS = {
    "ConnectionProvider": (".connections", "ConnectionProvider"),
    "ConnectionValidationIssue": (".connections", "ConnectionValidationIssue"),
    "ConnectionValidationResult": (".connections", "ConnectionValidationResult"),
    "ConnectionValidator": (".connections", "ConnectionValidator"),
    "IndexInitializationResult": (".indexing.index_tables", "IndexInitializationResult"),
    "IndexInitializer": (".indexing.index_tables", "IndexInitializer"),
    "IntervalSyncValueIndexScheduler": (".indexing.sync_values", "IntervalSyncValueIndexScheduler"),
    "PostgresConnectionValidator": (".connections.postgresqls", "PostgresConnectionValidator"),
    "PostgresRuntimeBackend": (".backend_registry", "PostgresRuntimeBackend"),
    "RuntimeBackend": (".backend_registry", "RuntimeBackend"),
    "SyncFieldValueIndexService": (".indexing.sync_values", "SyncFieldValueIndexService"),
    "SyncValueIndexScheduler": (".indexing.sync_values", "SyncValueIndexScheduler"),
    "TableRAGRuntime": (".context", "TableRAGRuntime"),
    "UnsupportedRuntimeBackendError": (".backend_registry", "UnsupportedRuntimeBackendError"),
    "ValueIndexStore": (".indexing.sync_values", "ValueIndexStore"),
    "ValueSourceReader": (".indexing.sync_values", "ValueSourceReader"),
    "build_sync_value_index_service": (".indexing.sync_values", "build_sync_value_index_service"),
    "build_table_rag_runtime": (".factory", "build_table_rag_runtime"),
    "get_runtime_backend": (".backend_registry", "get_runtime_backend"),
    "register_runtime_backend": (".backend_registry", "register_runtime_backend"),
    "validate_connection": (".connections.validators", "validate_connection"),
    "validate_postgres_connection": (".connections.postgresqls", "validate_postgres_connection"),
}

__all__ = list(_RUNTIME_EXPORTS)


def __getattr__(name: str):
    """按需导入运行时公共入口，避免检索器和运行时循环导入。

    Args:
        name: 访问的导出名称。

    Returns:
        对应公共对象。
    """
    if name not in _RUNTIME_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _RUNTIME_EXPORTS[name]
    from importlib import import_module

    value = getattr(import_module(module_name, package=__name__), attr_name)
    globals()[name] = value
    return value
