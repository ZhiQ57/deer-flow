"""使用 MySQL Row Binlog 读取真实行变更。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Sequence
from urllib.parse import parse_qs, unquote, urlparse

from ..vssag_schemas.sql import SQLFieldValueCDCEvent


class MySQLBinlogFieldValueSource:
    """通过 python-mysql-replication 读取 MySQL Row Binlog。"""

    def __init__(self, dsn: str, database_id: int, config):
        self.dsn = dsn or ""
        self.database_id = database_id
        self.config = config
        parsed = urlparse(
            self.dsn.replace("mysql+aiomysql://", "mysql://", 1)
        )
        query = parse_qs(parsed.query)
        self.connection_settings = {
            "host": parsed.hostname or "127.0.0.1",
            "port": parsed.port or 3306,
            "user": unquote(parsed.username or ""),
            "password": unquote(parsed.password or ""),
            "charset": query.get("charset", ["utf8mb4"])[0],
        }
        self.database = (parsed.path or "/").lstrip("/")
        self._indexed_tables: set[tuple[str, str]] = set()

    async def prepare(
        self,
        indexed_tables: Sequence[tuple[str, str]],
    ) -> str:
        """校验 Row Binlog 配置并返回当前文件与位置。

        Args:
            indexed_tables: 已建立字段值索引的数据库和表。

        Returns:
            JSON 编码的 Binlog 文件与位置。

        Raises:
            RuntimeError: MySQL 未启用 FULL Row Binlog。
        """

        try:
            import aiomysql
        except ImportError as exc:
            raise RuntimeError(
                "MySQL Binlog CDC 需要 aiomysql，请安装 "
                "ownmindforge-vssag[cdc,mysql]"
            ) from exc
        self._indexed_tables = set(indexed_tables)
        connection = await aiomysql.connect(
            **self.connection_settings,
            db=self.database or None,
            autocommit=True,
        )
        try:
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SHOW GLOBAL VARIABLES
                    WHERE Variable_name IN (
                        'log_bin',
                        'binlog_format',
                        'binlog_row_image',
                        'binlog_row_metadata'
                    )
                    """
                )
                variables = {
                    str(name).lower(): str(value).upper()
                    for name, value in await cursor.fetchall()
                }
                if variables.get("log_bin") not in {"ON", "1"}:
                    raise RuntimeError("MySQL 必须启用 log_bin")
                if variables.get("binlog_format") != "ROW":
                    raise RuntimeError("MySQL binlog_format 必须设置为 ROW")
                if variables.get("binlog_row_image") != "FULL":
                    raise RuntimeError(
                        "MySQL binlog_row_image 必须设置为 FULL"
                    )
                if variables.get("binlog_row_metadata") != "FULL":
                    raise RuntimeError(
                        "MySQL binlog_row_metadata 必须设置为 FULL"
                    )
                try:
                    await cursor.execute("SHOW BINARY LOG STATUS")
                except Exception:
                    await cursor.execute("SHOW MASTER STATUS")
                row = await cursor.fetchone()
                if not row:
                    raise RuntimeError("MySQL 未返回 Binlog 文件和位置")
                return json.dumps(
                    {
                        "log_file": str(row[0]),
                        "log_pos": int(row[1]),
                    },
                    separators=(",", ":"),
                )
        finally:
            connection.close()

    async def capture_current_position(self) -> str:
        """读取全量扫描开始前的 MySQL 当前 Binlog 位点。

        Returns:
            JSON 编码的 Binlog 文件与位置。

        Raises:
            RuntimeError: MySQL 未返回 Binlog 文件和位置。
        """

        try:
            import aiomysql
        except ImportError as exc:
            raise RuntimeError(
                "MySQL Binlog CDC 需要 aiomysql，请安装 "
                "ownmindforge-vssag[cdc,mysql]"
            ) from exc
        connection = await aiomysql.connect(
            **self.connection_settings,
            db=self.database or None,
            autocommit=True,
        )
        try:
            async with connection.cursor() as cursor:
                try:
                    await cursor.execute("SHOW BINARY LOG STATUS")
                except Exception:
                    await cursor.execute("SHOW MASTER STATUS")
                row = await cursor.fetchone()
                if not row:
                    raise RuntimeError("MySQL 未返回 Binlog 文件和位置")
                return json.dumps(
                    {
                        "log_file": str(row[0]),
                        "log_pos": int(row[1]),
                    },
                    separators=(",", ":"),
                )
        finally:
            connection.close()

    async def read_batch(
        self,
        position: str,
        limit: int,
    ) -> tuple[list[SQLFieldValueCDCEvent], str]:
        """从指定 Binlog 位点读取一批行事件。

        Args:
            position: JSON 编码的 Binlog 文件与位置。
            limit: 本批最多读取的行事件数量。

        Returns:
            统一 CDC 事件和最后读取位置。
        """

        start = json.loads(position or "{}")

        def read() -> tuple[list[SQLFieldValueCDCEvent], str]:
            """在线程中执行阻塞式 Binlog 读取。

            Returns:
                统一 CDC 事件和最后读取位置。

            Raises:
                RuntimeError: 缺少 MySQL Binlog CDC 依赖。
            """

            try:
                from pymysqlreplication import BinLogStreamReader
                from pymysqlreplication.event import QueryEvent
                from pymysqlreplication.row_event import (
                    DeleteRowsEvent,
                    UpdateRowsEvent,
                    WriteRowsEvent,
                )
            except ImportError as exc:
                raise RuntimeError(
                    "MySQL Binlog CDC 需要 mysql-replication，请安装 "
                    "ownmindforge-vssag[cdc,mysql]"
                ) from exc
            stream = BinLogStreamReader(
                connection_settings=self.connection_settings,
                server_id=int(self.config.server_id),
                blocking=False,
                resume_stream=True,
                log_file=start.get("log_file"),
                log_pos=int(start.get("log_pos") or 4),
                only_events=[
                    WriteRowsEvent,
                    UpdateRowsEvent,
                    DeleteRowsEvent,
                    QueryEvent,
                ],
                only_schemas=[self.database] if self.database else None,
                freeze_schema=True,
            )
            events: list[SQLFieldValueCDCEvent] = []
            last_file = str(start.get("log_file") or "")
            last_pos = int(start.get("log_pos") or 4)
            try:
                for event in stream:
                    last_file = str(stream.log_file or last_file)
                    last_pos = int(
                        getattr(event.packet, "log_pos", 0)
                        or stream.log_pos
                        or last_pos
                    )
                    schema_name = (
                        event.schema.decode()
                        if isinstance(getattr(event, "schema", ""), bytes)
                        else str(getattr(event, "schema", self.database))
                    )
                    table_name = (
                        event.table.decode()
                        if isinstance(getattr(event, "table", ""), bytes)
                        else str(getattr(event, "table", ""))
                    )
                    if isinstance(event, QueryEvent):
                        query = (
                            event.query.decode(errors="replace")
                            if isinstance(event.query, bytes)
                            else str(event.query)
                        )
                        if re.match(
                            r"^\s*(ALTER|CREATE|DROP|RENAME|TRUNCATE)\b",
                            query,
                            re.IGNORECASE,
                        ):
                            source_position = json.dumps(
                                {
                                    "log_file": last_file,
                                    "log_pos": last_pos,
                                },
                                separators=(",", ":"),
                            )
                            event_id = hashlib.sha256(
                                (
                                    f"mysql:{self.database_id}:"
                                    f"{last_file}:{last_pos}:schema:{query}"
                                ).encode("utf-8")
                            ).hexdigest()
                            events.append(
                                SQLFieldValueCDCEvent(
                                    event_id=event_id,
                                    database_id=self.database_id,
                                    db_type="mysql",
                                    operation="schema",
                                    schema_name=schema_name,
                                    raw_table_name=table_name,
                                    source_position=source_position,
                                    source_order=(
                                        int(
                                            re.search(
                                                r"(\d+)$",
                                                last_file,
                                            ).group(1)
                                        )
                                        << 32
                                    )
                                    + last_pos
                                    if re.search(r"(\d+)$", last_file)
                                    else last_pos,
                                    transaction_id=str(
                                        getattr(event, "timestamp", "")
                                    ),
                                    occurred_at=str(
                                        getattr(event, "timestamp", "")
                                    ),
                                )
                            )
                        if len(events) >= max(1, int(limit)):
                            break
                        continue
                    if (
                        schema_name,
                        table_name,
                    ) not in self._indexed_tables:
                        continue
                    operation = (
                        "insert"
                        if isinstance(event, WriteRowsEvent)
                        else "update"
                        if isinstance(event, UpdateRowsEvent)
                        else "delete"
                    )
                    for row_index, row in enumerate(event.rows):
                        old_values = (
                            dict(row.get("before_values") or {})
                            if operation == "update"
                            else dict(row.get("values") or {})
                            if operation == "delete"
                            else {}
                        )
                        new_values = (
                            dict(row.get("after_values") or {})
                            if operation == "update"
                            else dict(row.get("values") or {})
                            if operation == "insert"
                            else {}
                        )
                        source_position = json.dumps(
                            {
                                "log_file": last_file,
                                "log_pos": last_pos,
                                "row_index": row_index,
                            },
                            separators=(",", ":"),
                        )
                        file_match = re.search(r"(\d+)$", last_file)
                        source_order = (
                            (int(file_match.group(1)) << 32) + last_pos
                            if file_match
                            else last_pos
                        )
                        event_id = hashlib.sha256(
                            (
                                f"mysql:{self.database_id}:{last_file}:"
                                f"{last_pos}:{row_index}:{operation}:"
                                f"{schema_name}:{table_name}"
                            ).encode("utf-8")
                        ).hexdigest()
                        events.append(
                            SQLFieldValueCDCEvent(
                                event_id=event_id,
                                database_id=self.database_id,
                                db_type="mysql",
                                operation=operation,
                                schema_name=schema_name,
                                raw_table_name=table_name,
                                old_values=old_values,
                                new_values=new_values,
                                source_position=source_position,
                                source_order=source_order,
                                transaction_id=str(
                                    getattr(event, "timestamp", "")
                                ),
                                occurred_at=str(
                                    getattr(event, "timestamp", "")
                                ),
                            )
                        )
                        if len(events) >= max(1, int(limit)):
                            break
                    if len(events) >= max(1, int(limit)):
                        break
            finally:
                stream.close()
            return events, json.dumps(
                {"log_file": last_file, "log_pos": last_pos},
                separators=(",", ":"),
            )

        return await asyncio.to_thread(read)

    async def confirm(self, position: str) -> None:
        """确认已处理的 Binlog 位点。

        Args:
            position: 已成功处理的 Binlog 位点。

        Returns:
            无返回值。MySQL 位点由外部 checkpoint 持久化。
        """

        del position

    async def close(self) -> None:
        """结束当前 MySQL Binlog 数据源。

        Returns:
            无返回值。每次读取均独立关闭同步 Binlog 连接。
        """

        return None
