"""字段值 CDC 的 Redis Stream 消费组、Pending 重试和 DLQ。"""

from __future__ import annotations

import json
from collections.abc import Sequence

from ..vssag_schemas.sql import (
    SQLFieldValueCDCEvent,
    SQLFieldValueStreamMessage,
)


class RedisFieldValueStream:
    """使用 Redis Stream Pending 列表实现至少一次消费。"""

    def __init__(self, config):
        self.config = config
        self._redis = None
        self._group_ready = False
        self._dlq_group_ready = False
        self._retry_hash = f"{config.stream_name}:retry-counts"

    async def _connect(self):
        if self._redis is not None:
            return self._redis
        try:
            from redis.asyncio import Redis
        except ImportError as exc:
            raise RuntimeError(
                "Redis Stream CDC 需要 redis，请安装 ownmindforge-vssag[cdc]"
            ) from exc
        self._redis = Redis.from_url(
            self.config.url,
            decode_responses=True,
        )
        await self._redis.ping()
        return self._redis

    async def _ensure_group(self):
        redis = await self._connect()
        if self._group_ready:
            return redis
        try:
            await redis.xgroup_create(
                self.config.stream_name,
                self.config.group_name,
                id="0-0",
                mkstream=True,
            )
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        self._group_ready = True
        return redis

    async def publish(
        self,
        events: Sequence[SQLFieldValueCDCEvent],
    ) -> None:
        """发布源数据库 CDC 事件。

        Args:
            events: 待发布的统一 CDC 事件。
        """

        redis = await self._connect()
        for event in events:
            await redis.xadd(
                self.config.stream_name,
                {
                    "event": event.model_dump_json(),
                    "attempt": "0",
                },
            )

    async def consume_batch(
        self,
        *,
        max_events: int,
        timeout_seconds: float,
    ) -> list[SQLFieldValueStreamMessage]:
        """先认领超时 Pending，再读取新 Redis Stream 消息。

        Args:
            max_events: 本批最大消息数。
            timeout_seconds: 新消息阻塞等待时间。

        Returns:
            带 Stream ID 和重试次数的统一消息。
        """

        redis = await self._ensure_group()
        max_events = max(1, int(max_events))
        claimed = await redis.xautoclaim(
            self.config.stream_name,
            self.config.group_name,
            self.config.consumer_name,
            min_idle_time=self.config.pending_idle_milliseconds,
            start_id="0-0",
            count=max_events,
        )
        claimed_rows = claimed[1] if len(claimed) > 1 else []
        rows = list(claimed_rows)
        if len(rows) < max_events:
            new_batches = await redis.xreadgroup(
                self.config.group_name,
                self.config.consumer_name,
                {self.config.stream_name: ">"},
                count=max_events - len(rows),
                block=max(
                    1,
                    min(
                        int(timeout_seconds * 1000),
                        int(self.config.block_milliseconds),
                    ),
                ),
            )
            for _stream_name, stream_rows in new_batches:
                rows.extend(stream_rows)
        if not rows:
            return []
        retry_counts = await redis.hmget(
            self._retry_hash,
            [message_id for message_id, _fields in rows],
        )
        messages: list[SQLFieldValueStreamMessage] = []
        for (message_id, fields), retry_count in zip(
            rows,
            retry_counts,
            strict=True,
        ):
            event = SQLFieldValueCDCEvent.model_validate_json(fields["event"])
            messages.append(
                SQLFieldValueStreamMessage(
                    message_id=str(message_id),
                    event=event,
                    attempt=int(
                        retry_count
                        or fields.get("attempt")
                        or 0
                    ),
                    transport_name="redis_stream",
                    transport_partition=self.config.stream_name,
                    transport_position=str(message_id),
                )
            )
        messages.sort(
            key=lambda item: (
                item.event.source_order,
                item.transport_position,
            )
        )
        return messages

    async def retry(
        self,
        message: SQLFieldValueStreamMessage,
        error: str,
        *,
        max_retries: int,
    ) -> bool:
        """保留 Pending 消息重试，超限后转入 DLQ 并 ACK。

        Args:
            message: 处理失败的 Redis Stream 消息。
            error: 失败原因。
            max_retries: 最大重试次数。

        Returns:
            返回假；Pending 消息不能由通用 ACK 再次提交。
        """

        redis = await self._ensure_group()
        attempt = int(
            await redis.hincrby(
                self._retry_hash,
                message.message_id,
                1,
            )
        )
        if attempt > max_retries:
            await redis.xadd(
                f"{self.config.stream_name}.dlq",
                {
                    "event": message.event.model_dump_json(),
                    "attempt": str(attempt),
                    "error": str(error)[:2000],
                    "failed_message_id": message.message_id,
                },
            )
            await redis.xack(
                self.config.stream_name,
                self.config.group_name,
                message.message_id,
            )
            await redis.hdel(self._retry_hash, message.message_id)
        return False

    async def ack(
        self,
        messages: Sequence[SQLFieldValueStreamMessage],
    ) -> None:
        """ACK 成功处理的 Redis Stream 消息并清理重试计数。

        Args:
            messages: 已成功处理的消息。
        """

        if not messages:
            return
        redis = await self._ensure_group()
        ids = [message.message_id for message in messages]
        await redis.xack(
            self.config.stream_name,
            self.config.group_name,
            *ids,
        )
        await redis.hdel(self._retry_hash, *ids)

    async def replay_dlq(self, *, max_events: int = 100) -> int:
        """把 Redis Stream DLQ 消息重新发布到主 Stream。

        Args:
            max_events: 本次最多重放的消息数量。

        Returns:
            已成功重新发布并从 DLQ 删除的数量。
        """

        redis = await self._connect()
        dlq_stream = f"{self.config.stream_name}.dlq"
        dlq_group = f"{self.config.group_name}:dlq-replay"
        if not self._dlq_group_ready:
            try:
                await redis.xgroup_create(
                    dlq_stream,
                    dlq_group,
                    id="0-0",
                    mkstream=True,
                )
            except Exception as exc:
                if "BUSYGROUP" not in str(exc):
                    raise
            self._dlq_group_ready = True
        batches = await redis.xreadgroup(
            dlq_group,
            self.config.consumer_name,
            {dlq_stream: ">"},
            count=max(1, int(max_events)),
            block=1,
        )
        replayed = 0
        for _stream_name, rows in batches:
            for message_id, fields in rows:
                event = SQLFieldValueCDCEvent.model_validate_json(
                    fields["event"]
                )
                await redis.xadd(
                    self.config.stream_name,
                    {
                        "event": event.model_dump_json(),
                        "attempt": "0",
                        "replayed_from": str(message_id),
                    },
                )
                await redis.xack(
                    dlq_stream,
                    dlq_group,
                    message_id,
                )
                await redis.xdel(dlq_stream, message_id)
                replayed += 1
        return replayed

    async def close(self) -> None:
        """关闭 Redis Stream 连接。

        Returns:
            无返回值。
        """

        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
