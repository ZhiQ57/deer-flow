from __future__ import annotations

from pydantic import BaseModel, Field


class SQLScope(BaseModel):
    """Scope for the SQL virtual directory."""

    user_id: str
    mount_root: str | None = None
    schema_names: list[str] = Field(default_factory=list)
    table_names: list[str] = Field(default_factory=list)
    excluded_columns: dict[str, list[str]] = Field(default_factory=dict)

    def allows_table(
        self,
        schema_name: str,
        raw_table_name: str,
        random_table_name: str = "",
    ) -> bool:
        """判断表是否在当前 Schema 访问范围内。

        Args:
            schema_name: 原始 Schema 名称。
            raw_table_name: 原始表名。
            random_table_name: 脱敏后的虚拟表名。

        Returns:
            表在 scope 白名单内时返回 True。
        """

        if self.schema_names and schema_name not in set(self.schema_names):
            return False
        if self.table_names:
            allowed = set(self.table_names)
            qualified = {
                f"{schema_name}.{raw_table_name}",
                f"{schema_name}.{random_table_name}",
            }
            if not (
                raw_table_name in allowed
                or random_table_name in allowed
                or qualified.intersection(allowed)
            ):
                return False
        return True

    def allows_column(
        self,
        raw_table_name: str,
        column_name: str,
        random_table_name: str = "",
    ) -> bool:
        """判断字段是否未被当前 scope 排除。

        Args:
            raw_table_name: 原始表名。
            column_name: 原始字段名。
            random_table_name: 脱敏后的虚拟表名。

        Returns:
            字段未被排除时返回 True。
        """

        excluded = set(self.excluded_columns.get(raw_table_name, []))
        if random_table_name:
            excluded.update(self.excluded_columns.get(random_table_name, []))
        return column_name not in excluded
