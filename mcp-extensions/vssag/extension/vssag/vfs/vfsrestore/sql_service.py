"""Facade for SQL Tool Use and restricted Bash restoration."""

from __future__ import annotations

from ...config import SQLAbilityConfig
from ...indexing.index_stores.sql_schema_store import SQLSchemaStore
from ...vssag_schemas.sql import SQLScope, SQLToolRequest, SQLToolResponse
from ..vfbuild.sql import SQLVirtualFileSystemBuilder
from .sql import SQLBashRestorer, SQLToolUseRestorer


class SQLVFSRestoreService:
    def __init__(self, store: SQLSchemaStore, config: SQLAbilityConfig):
        self.builder = SQLVirtualFileSystemBuilder(store, config)
        self.tool_use = SQLToolUseRestorer(self.builder, store)
        self.bash = SQLBashRestorer(self.tool_use)

    async def restore_tool(self, request: SQLToolRequest) -> SQLToolResponse:
        return await self.tool_use.restore(request)

    async def restore_bash(self, command: str, scope: SQLScope) -> SQLToolResponse:
        return await self.bash.execute(command, scope)

    async def build_tree(
        self,
        scope: SQLScope,
        *,
        path: str | None = None,
        max_depth: int | None = None,
    ) -> str:
        """构建受限展开的 SQL 虚拟目录树。"""

        return await self.builder.build_tree(
            scope,
            path=path,
            max_depth=max_depth,
        )

    async def list_entries(self, path: str, scope: SQLScope) -> SQLToolResponse:
        return await self.restore_tool(
            SQLToolRequest(mode="tables", path=path, scope=scope)
        )
