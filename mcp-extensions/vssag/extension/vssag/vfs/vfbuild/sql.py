"""Virtual SQL Schema directory builder."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable

from ...config import SQLAbilityConfig
from ...indexing.index_stores.sql_schema_store import SQLSchemaStore
from ...vssag_schemas.sql import SQLScope, SQLTableSchema
from ..paths import vssag_sql_root
from ..tree import render_tree


class SQLVirtualFileSystemBuilder:
    def __init__(self, store: SQLSchemaStore, config: SQLAbilityConfig):
        self.store = store
        self.config = config

    def root(self, scope: SQLScope) -> str:
        """返回当前 scope 的 SQL 虚拟目录根路径。"""

        return vssag_sql_root(
            scope.mount_root,
            root_prefix=self.config.vfs_root_prefix,
            mount_name=self.config.vfs_mount_name,
        )

    async def build(self, scope: SQLScope) -> dict[str, str]:
        """构建已启用 SQL 能力的权限裁剪文件视图。

        Args:
            scope: 调用方授予的表、字段范围与外部挂载父路径。

        Returns:
            完整公开路径到文件内容的映射；能力关闭时返回空映射。
        """

        if not self.config.enabled:
            return {}
        root = self.root(scope)
        tables = await self.store.list_tables()
        tables = [
            table
            for table in tables
            if self._visible(table, scope)
        ]
        aliases = {
            table.raw_table_name: table.random_table_name
            for table in tables
            if table.raw_table_name != table.random_table_name
        }
        grouped: dict[str, list[SQLTableSchema]] = {}
        for table in tables:
            grouped.setdefault(table.raw_schema_name, []).append(table)

        files: dict[str, str] = {
            f"{root}/index.md": self._root_index(grouped),
        }
        evidence = await self.store.list_evidence()
        hidden_names = {
            name
            for table in await self.store.list_tables()
            if not self._visible(table, scope)
            for name in (table.raw_table_name, table.random_table_name)
        }
        evidence = [
            item
            for item in evidence
            if not hidden_names.intersection(
                f"{item.retrieval_text}\n{item.evidence_content}"
            )
        ]
        for schema_name, schema_tables in sorted(grouped.items()):
            schema_root = f"{root}/{schema_name}"
            files[f"{schema_root}/index.md"] = self._schema_index(schema_name, schema_tables)
            files[f"{schema_root}/evidence.md"] = await self._evidence_markdown(evidence, scope)
            files[f"{schema_root}/graph.md"] = await self._graph_markdown(schema_name, scope)
            for table in sorted(schema_tables, key=lambda item: item.raw_table_name):
                display_name = self.display_table_name(table)
                table_root = f"{schema_root}/table/{display_name}"
                files[f"{table_root}/ddl.sql"] = self.ddl(table, aliases=aliases)
                files[f"{table_root}/desc.md"] = self.description(table, scope)
                files[f"{table_root}/sample.csv"] = self.sample_csv(table, scope)
                if table.qa_text:
                    files[f"{table_root}/qa.md"] = table.qa_text.rstrip() + "\n"
        return files

    async def build_tree(
        self,
        scope: SQLScope,
        *,
        path: str | None = None,
        max_depth: int | None = None,
    ) -> str:
        """从模块公开根展示 SQL 能力目录，或展开指定的子目录。

        Args:
            scope: 当前 SQL 访问范围和外部挂载父路径。
            path: 指定目录的完整公开路径，默认使用模块挂载根。
            max_depth: 相对于展示目录的最大展开深度。

        Returns:
            保留完整公开根路径的受限目录树。
        """

        files = await self.build(scope)
        target = (
            self.root(scope).rsplit("/", 1)[0]
            if path in {None, "", "/"}
            else path
        )
        return render_tree(files, target, max_depth=max_depth)

    async def entries(self, scope: SQLScope, path: str = "/") -> list[dict]:
        files = await self.build(scope)
        normalized = path.rstrip("/") or "/"
        seen: dict[str, dict] = {}
        for file_path, content in files.items():
            if normalized != "/" and not file_path.startswith(normalized.rstrip("/") + "/"):
                continue
            prefix = normalized.rstrip("/") + "/" if normalized != "/" else "/"
            relative = file_path[len(prefix) :] if file_path.startswith(prefix) else file_path.lstrip("/")
            name = relative.split("/", 1)[0]
            child = (normalized.rstrip("/") + "/" + name).rstrip("/") if normalized != "/" else "/" + name
            if "/" in relative:
                seen[child] = {"name": name, "path": child, "type": "dir", "size": 0}
            else:
                seen[child] = {"name": name, "path": child, "type": "file", "size": len(content)}
        return sorted(seen.values(), key=lambda item: (item["type"] != "dir", item["name"]))

    @staticmethod
    def _root_index(grouped: dict[str, list[SQLTableSchema]]) -> str:
        lines = ["# SQL Schema", ""]
        for schema_name, tables in sorted(grouped.items()):
            lines.append(f"## {schema_name}")
            lines.extend(
                f"- {table.random_table_name if table.random_table_name != table.raw_table_name else table.raw_table_name}"
                for table in sorted(tables, key=lambda item: item.raw_table_name)
            )
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    def display_table_name(self, table: SQLTableSchema) -> str:
        if self.config.security.allow_real_table_names:
            return table.raw_table_name
        return table.random_table_name

    def ddl(
        self,
        table: SQLTableSchema,
        *,
        aliases: dict[str, str] | None = None,
    ) -> str:
        content = (
            table.raw_ddl_text
            if self.config.security.allow_real_table_names
            else table.random_ddl_text
        )
        if aliases and not self.config.security.allow_real_table_names:
            for raw, random in sorted(
                aliases.items(),
                key=lambda item: -len(item[0]),
            ):
                content = content.replace(raw, random)
        return content.rstrip() + "\n"

    def description(self, table: SQLTableSchema, scope: SQLScope | None = None) -> str:
        """构建包含表业务说明和完整字段语义的表描述。

        Args:
            table: 已采集的表结构、业务摘要与字段元数据。
            scope: 调用方授予的字段范围，空值表示不额外裁剪字段。

        Returns:
            `desc.md` 内容，字段描述统一为一张 Markdown 表格。
        """

        table_name = self.display_table_name(table)
        lines = [
            f"# {table_name}",
            "",
            table.random_schema_summary
            if not self.config.security.allow_real_table_names
            else table.raw_schema_summary,
            "",
            f"- 行数: {table.row_count if table.row_count is not None else '未知'}",
            f"- DDL hash: {table.raw_ddl_hash}",
            "",
            "## 字段",
            "",
            "| 字段 | 类型 | 主键 | 可为空 | 默认值 | 注释 | 枚举值 |",
            "|------|------|------|--------|--------|------|--------|",
        ]
        for column in table.columns:
            if not self._column_visible(table, column.name, scope):
                continue
            cells = [
                column.name,
                column.data_type,
                "是" if column.primary_key else "否",
                "是" if column.nullable else "否",
                column.default or "",
                column.comment,
                "、".join(column.enum_values),
            ]
            lines.append(
                "| "
                + " | ".join(
                    value.replace("|", "\\|")
                    .replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")
                    for value in cells
                )
                + " |"
            )
        return "\n".join(lines) + "\n"

    def _schema_index(self, schema_name: str, tables: list[SQLTableSchema]) -> str:
        lines = [f"# Schema: {schema_name}", ""]
        for table in sorted(tables, key=lambda item: item.raw_table_name):
            name = self.display_table_name(table)
            summary = (
                table.random_schema_summary
                if not self.config.security.allow_real_table_names
                else table.raw_schema_summary
            )
            lines.append(f"- `{name}`: {summary}")
        return "\n".join(lines) + "\n"

    def sample_csv(self, table: SQLTableSchema, scope: SQLScope | None = None) -> str:
        if not table.sample_rows:
            return ""
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        columns = [
            column.name
            for column in table.columns
            if self._column_visible(table, column.name, scope)
        ]
        writer.writerow(columns)
        for row in table.sample_rows[:5]:
            writer.writerow([row.get(column, "") for column in columns])
        return output.getvalue()

    async def _graph_markdown(self, schema_name: str, scope: SQLScope | None = None) -> str:
        edges = await self.store.list_graph_edges(
            None,
            include_candidates=self.config.retriever_service.index.graph.include_candidates,
            limit=1000,
        )
        edges = [
            edge
            for edge in edges
            if edge.raw_schema_name == schema_name
            or edge.related_raw_schema_name == schema_name
            if scope is None
            or (
                scope.allows_table(edge.raw_schema_name, edge.raw_table_name, edge.random_table_name)
                and scope.allows_table(
                    edge.related_raw_schema_name,
                    edge.related_raw_table_name,
                    edge.related_random_table_name,
                )
                and scope.allows_column(
                    edge.raw_table_name,
                    edge.raw_column_name or "",
                    edge.random_table_name,
                )
                and scope.allows_column(
                    edge.related_raw_table_name,
                    edge.related_raw_column_name or "",
                    edge.related_random_table_name,
                )
            )
        ]
        lines = [
            f"# {schema_name} 表关联关系",
            "",
            "| 方向 | 目标表 | 关联字段 | 类型 | 方法 | 置信度 |",
            "|------|--------|----------|------|------|--------|",
        ]
        for edge in edges:
            left = (
                edge.random_table_name
                if not self.config.security.allow_real_table_names
                else edge.raw_table_name
            )
            right = (
                edge.related_random_table_name
                if not self.config.security.allow_real_table_names
                else edge.related_raw_table_name
            )
            left_column = edge.raw_column_name
            right_column = edge.related_raw_column_name
            relation = (
                f"{left}.{left_column} → {right}.{right_column}"
                if left_column and right_column
                else f"{left} → {right}"
            )
            lines.append(
                f"| {edge.relation_direction} | {right} | {relation} | "
                f"{edge.cardinality} | {edge.evidence_source} | {edge.confidence:.4f} |"
            )
        return "\n".join(lines) + "\n"

    async def _evidence_markdown(
        self,
        evidence: Iterable,
        scope: SQLScope | None = None,
    ) -> str:
        lines = ["# 业务规则与证据", ""]
        for item in evidence:
            trigger = "、".join(item.triggers)
            lines.extend(
                [
                    f"## {trigger or item.evidence_type}",
                    "",
                    item.evidence_content,
                    "",
                ]
            )
        return (await self.mask_text("\n".join(lines))).rstrip() + "\n"

    async def mask_text(self, text: str) -> str:
        if self.config.security.allow_real_table_names or not text:
            return text
        tables = await self.store.list_tables()
        masked = text
        for table in sorted(tables, key=lambda item: len(item.raw_table_name), reverse=True):
            if table.raw_table_name and table.raw_table_name != table.random_table_name:
                masked = masked.replace(table.raw_table_name, table.random_table_name)
        return masked

    def _visible(self, table: SQLTableSchema, scope: SQLScope) -> bool:
        return scope.allows_table(
            table.raw_schema_name,
            table.raw_table_name,
            table.random_table_name,
        )

    @staticmethod
    def _column_visible(
        table: SQLTableSchema,
        column_name: str,
        scope: SQLScope | None,
    ) -> bool:
        return scope is None or scope.allows_column(
            table.raw_table_name,
            column_name,
            table.random_table_name,
        )
