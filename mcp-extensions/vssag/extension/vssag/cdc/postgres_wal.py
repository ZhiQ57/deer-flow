"""使用 PostgreSQL 逻辑复制槽读取真实 WAL 变更。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence

from ..vssag_schemas.sql import SQLFieldValueCDCEvent


class PostgresWALFieldValueSource:
    """通过 wal2json 逻辑复制槽读取字段值行变更。"""

    def __init__(self, dsn: str, database_id: int, config):
        self.dsn = (dsn or "").replace(
            "postgresql+asyncpg://",
            "postgresql://",
            1,
        )
        self.database_id = database_id
        self.config = config
        self._connection = None
        self._pending_position = ""
        self._indexed_tables: set[tuple[str, str]] = set()

    async def _connect(self):
        if self._connection is not None:
            return self._connection
        try:
            import asyncpg
        except ImportError as exc:
            raise RuntimeError(
                "PostgreSQL WAL CDC 需要 asyncpg，请安装 "
                "ownmindforge-vssag[cdc,postgres]"
            ) from exc
        if not self.dsn:
            raise RuntimeError("PostgreSQL WAL CDC 缺少目标数据库 DSN")
        self._connection = await asyncpg.connect(self.dsn)
        return self._connection

    async def prepare(
        self,
        indexed_tables: Sequence[tuple[str, str]],
    ) -> str:
        """校验 WAL 前置条件、创建逻辑复制槽并返回扫描起点。

        Args:
            indexed_tables: 已建立字段值索引的 Schema 和表。

        Returns:
            逻辑复制槽当前确认的 LSN。

        Raises:
            RuntimeError: WAL 配置、输出插件或表副本标识不满足要求。
            ValueError: 复制槽名称或输出插件名称非法。
        """

        slot_name = self.config.slot_name
        plugin = self.config.output_plugin
        if not re.fullmatch(r"[a-z0-9_]{1,63}", slot_name):
            raise ValueError("PostgreSQL 逻辑复制槽名称只能包含小写字母、数字和下划线")
        if not re.fullmatch(r"[A-Za-z0-9_]{1,63}", plugin):
            raise ValueError("PostgreSQL WAL 输出插件名称非法")
        connection = await self._connect()
        wal_level = await connection.fetchval("SHOW wal_level")
        if str(wal_level).lower() != "logical":
            raise RuntimeError("PostgreSQL wal_level 必须设置为 logical")
        if indexed_tables:
            rows = await connection.fetch(
                """
                SELECT namespace.nspname AS schema_name,
                       relation.relname AS table_name,
                       relation.relreplident AS replica_identity
                FROM pg_class relation
                JOIN pg_namespace namespace
                  ON namespace.oid=relation.relnamespace
                WHERE relation.relkind IN ('r', 'p')
                """
            )
            identities = {
                (str(row["schema_name"]), str(row["table_name"])): (
                    row["replica_identity"].decode()
                    if isinstance(row["replica_identity"], bytes)
                    else str(row["replica_identity"])
                )
                for row in rows
            }
            invalid = sorted(
                f"{schema_name}.{table_name}"
                for schema_name, table_name in indexed_tables
                if identities.get((schema_name, table_name)) != "f"
            )
            if invalid:
                raise RuntimeError(
                    "PostgreSQL CDC 索引表必须设置 REPLICA IDENTITY FULL："
                    + ", ".join(invalid)
                )
        self._indexed_tables = set(indexed_tables)
        slot = await connection.fetchrow(
            """
            SELECT plugin, confirmed_flush_lsn::text AS confirmed_flush_lsn,
                   restart_lsn::text AS restart_lsn
            FROM pg_replication_slots
            WHERE slot_name=$1
            """,
            slot_name,
        )
        if slot is None:
            try:
                slot = await connection.fetchrow(
                    """
                    SELECT slot_name, lsn::text AS confirmed_flush_lsn,
                           $2::text AS plugin, lsn::text AS restart_lsn
                    FROM pg_create_logical_replication_slot($1, $2)
                    """,
                    slot_name,
                    plugin,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"创建 PostgreSQL 逻辑复制槽失败，请确认已安装 {plugin}"
                ) from exc
        elif str(slot["plugin"]) != plugin:
            raise RuntimeError(
                f"逻辑复制槽 {slot_name} 使用 {slot['plugin']}，"
                f"与配置的 {plugin} 不一致"
            )
        return str(
            slot["confirmed_flush_lsn"]
            or slot["restart_lsn"]
            or "0/0"
        )

    async def capture_current_position(self) -> str:
        """读取全量扫描开始前的 PostgreSQL 当前 WAL LSN。

        Returns:
            当前数据库 WAL LSN。
        """

        connection = await self._connect()
        return str(
            await connection.fetchval(
                "SELECT pg_current_wal_lsn()::text"
            )
        )

    async def read_batch(
        self,
        position: str,
        limit: int,
    ) -> tuple[list[SQLFieldValueCDCEvent], str]:
        """从复制槽查看一批尚未确认的 WAL 事件。

        Args:
            position: 本地记录的已发布位置；复制槽自身作为读取真值。
            limit: 本批最多读取的变更数。

        Returns:
            统一 CDC 事件和最后一个 WAL LSN。
        """

        del position
        connection = await self._connect()
        rows = await connection.fetch(
            """
            SELECT lsn::text AS lsn, xid::text AS xid, data
            FROM pg_logical_slot_peek_changes(
                $1, NULL, $2,
                'format-version', '2',
                'include-lsn', '1',
                'include-timestamp', '1'
            )
            """,
            self.config.slot_name,
            max(1, int(limit)),
        )
        events: list[SQLFieldValueCDCEvent] = []
        last_position = ""
        for row_index, row in enumerate(rows):
            last_position = str(row["lsn"])
            payload = json.loads(str(row["data"]))
            changes = (
                payload.get("change", [])
                if isinstance(payload, dict) and "change" in payload
                else [payload]
            )
            for change_index, change in enumerate(changes):
                if not isinstance(change, dict):
                    continue
                action = str(
                    change.get("action")
                    or change.get("kind")
                    or ""
                ).lower()
                operation = {
                    "i": "insert",
                    "insert": "insert",
                    "u": "update",
                    "update": "update",
                    "d": "delete",
                    "delete": "delete",
                    "t": "truncate",
                    "truncate": "truncate",
                }.get(action)
                if operation is None:
                    continue
                schema_name = str(
                    change.get("schema")
                    or change.get("namespace")
                    or "public"
                )
                table_name = str(change.get("table") or "")
                if not table_name:
                    continue
                if (
                    schema_name,
                    table_name,
                ) not in self._indexed_tables:
                    continue
                new_values: dict = {}
                old_values: dict = {}
                columns = change.get("columns")
                if isinstance(columns, list):
                    for column in columns:
                        if isinstance(column, dict) and column.get("name"):
                            new_values[str(column["name"])] = column.get(
                                "value"
                            )
                else:
                    names = change.get("columnnames") or []
                    values = change.get("columnvalues") or []
                    new_values = dict(zip(names, values, strict=False))
                identity = (
                    change.get("identity")
                    or change.get("oldkeys")
                    or {}
                )
                if isinstance(identity, list):
                    for column in identity:
                        if isinstance(column, dict) and column.get("name"):
                            old_values[str(column["name"])] = column.get(
                                "value"
                            )
                elif isinstance(identity, dict):
                    names = identity.get("keynames") or []
                    values = identity.get("keyvalues") or []
                    old_values = dict(zip(names, values, strict=False))
                if operation == "delete" and not old_values:
                    old_values = dict(new_values)
                    new_values = {}
                source_position = str(
                    change.get("lsn") or last_position
                )
                if "/" in source_position:
                    high, low = source_position.split("/", 1)
                    source_order = (
                        int(high, 16) << 32
                    ) + int(low, 16)
                else:
                    source_order = 0
                transaction_id = str(
                    change.get("xid") or row["xid"] or ""
                )
                event_id = hashlib.sha256(
                    (
                        f"postgresql:{self.database_id}:{source_position}:"
                        f"{transaction_id}:{row_index}:{change_index}:"
                        f"{operation}:{schema_name}:{table_name}"
                    ).encode("utf-8")
                ).hexdigest()
                events.append(
                    SQLFieldValueCDCEvent(
                        event_id=event_id,
                        database_id=self.database_id,
                        db_type="postgresql",
                        operation=operation,
                        schema_name=schema_name,
                        raw_table_name=table_name,
                        old_values=old_values,
                        new_values=new_values,
                        source_position=source_position,
                        source_order=source_order,
                        transaction_id=transaction_id,
                        occurred_at=str(
                            change.get("timestamp")
                            or payload.get("timestamp")
                            if isinstance(payload, dict)
                            else ""
                        ),
                    )
                )
        self._pending_position = last_position
        return events, last_position

    async def confirm(self, position: str) -> None:
        """在消息发布成功后推进逻辑复制槽。

        Args:
            position: 已成功发布的最后一个 WAL LSN。
        """

        if not position:
            return
        connection = await self._connect()
        await connection.execute(
            "SELECT pg_replication_slot_advance($1, $2::text::pg_lsn)",
            self.config.slot_name,
            position,
        )
        self._pending_position = ""

    async def close(self) -> None:
        """关闭 PostgreSQL WAL 连接。

        Returns:
            无返回值。
        """

        if self._connection is not None:
            await self._connection.close()
            self._connection = None
