"""TableRAG 索引运行时模块。"""

_INDEXING_EXPORTS = {
    "ColumnSchemaRepresentation": (".index_tables", "ColumnSchemaRepresentation"),
    "IndexInitializationResult": (".index_tables", "IndexInitializationResult"),
    "IndexInitializer": (".index_tables", "IndexInitializer"),
    "IntervalSyncValueIndexScheduler": (".sync_values", "IntervalSyncValueIndexScheduler"),
    "SyncFieldValueIndexService": (".sync_values", "SyncFieldValueIndexService"),
    "SyncValueIndexScheduler": (".sync_values", "SyncValueIndexScheduler"),
    "TableSchemaRepresentation": (".index_tables", "TableSchemaRepresentation"),
    "ValueIndexStore": (".sync_values", "ValueIndexStore"),
    "ValueSourceReader": (".sync_values", "ValueSourceReader"),
    "build_column_schema_representation": (".index_tables", "build_column_schema_representation"),
    "build_sync_value_index_service": (".sync_values", "build_sync_value_index_service"),
    "build_table_schema_representation": (".index_tables", "build_table_schema_representation"),
    "record_search_text": (".sync_values", "record_search_text"),
    "simple_query_normalize": (".sync_values", "simple_query_normalize"),
}

__all__ = list(_INDEXING_EXPORTS)


def __getattr__(name: str):
    """按需导入索引运行时公共入口，避免模块循环依赖。

    Args:
        name: 访问的导出名称。

    Returns:
        对应公共对象。
    """
    if name not in _INDEXING_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _INDEXING_EXPORTS[name]
    from importlib import import_module

    value = getattr(import_module(module_name, package=__name__), attr_name)
    globals()[name] = value
    return value
