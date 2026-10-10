"""字段值 CDC 的 Kafka 发布、消费、重试和 DLQ。"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence

from ..vssag_schemas.sql import (
    SQLFieldValueCDCEvent,
    SQLFieldValueStreamMessage,
)


class KafkaFieldValueStream:
    """使用手动 Offset 提交保证 Kafka 字段值事件至少投递一次。"""

    def __init__(self, config):
        self.config = config
        self._producer = None
        self._consumer = None

    async def _start_producer(self):
        if self._producer is not None:
            return self._producer
        try:
            from aiokafka import AIOKafkaProducer
        except ImportError as exc:
            raise RuntimeError(
                "Kafka CDC 需要 aiokafka，请安装 ownmindforge-vssag[cdc]"
            ) from exc
        options = {
            "bootstrap_servers": list(self.config.bootstrap_servers),
            "client_id": self.config.client_id,
            "enable_idempotence": True,
            "acks": "all",
        }
        if self.config.security_protocol.upper() != "PLAINTEXT":
            options.update(
                {
                    "security_protocol": self.config.security_protocol,
                    "sasl_mechanism": self.config.sasl_mechanism,
                    "sasl_plain_username": self.config.sasl_username,
                    "sasl_plain_password": self.config.sasl_password,
                }
            )
        self._producer = AIOKafkaProducer(**options)
        await self._producer.start()
        return self._producer

    async def _start_consumer(self):
        if self._consumer is not None:
            return self._consumer
        try:
            from aiokafka import AIOKafkaConsumer
        except ImportError as exc:
            raise RuntimeError(
                "Kafka CDC 需要 aiokafka，请安装 ownmindforge-vssag[cdc]"
            ) from exc
        options = {
            "bootstrap_servers": list(self.config.bootstrap_servers),
            "group_id": self.config.group_id,
            "client_id": self.config.client_id,
            "enable_auto_commit": False,
            "auto_offset_reset": "earliest",
        }
        if self.config.security_protocol.upper() != "PLAINTEXT":
            options.update(
                {
                    "security_protocol": self.config.security_protocol,
                    "sasl_mechanism": self.config.sasl_mechanism,
                    "sasl_plain_username": self.config.sasl_username,
                    "sasl_plain_password": self.config.sasl_password,
                }
            )
        self._consumer = AIOKafkaConsumer(
            self.config.topic,
            f"{self.config.topic}.retry",
            **options,
        )
        await self._consumer.start()
        return self._consumer

    async def publish(
        self,
        events: Sequence[SQLFieldValueCDCEvent],
    ) -> None:
        """发布源数据库 CDC 事件。

        Args:
            events: 待发布的统一 CDC 事件。
        """

        producer = await self._start_producer()
        for event in events:
            payload = json.dumps(
                {
                    "event": event.model_dump(mode="json"),
                    "attempt": 0,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            await producer.send_and_wait(
                self.config.topic,
                payload,
                key=str(event.database_id).encode("utf-8"),
            )

    async def consume_batch(
        self,
        *,
        max_events: int,
        timeout_seconds: float,
    ) -> list[SQLFieldValueStreamMessage]:
        """读取一批 Kafka 主主题和重试主题消息。

        Args:
            max_events: 本批最大消息数。
            timeout_seconds: 拉取等待时间。

        Returns:
            带分区和 Offset 的统一消息。
        """

        consumer = await self._start_consumer()
        batches = await consumer.getmany(
            timeout_ms=max(1, int(timeout_seconds * 1000)),
            max_records=max(1, int(max_events)),
        )
        messages: list[SQLFieldValueStreamMessage] = []
        for topic_partition, records in batches.items():
            for record in records:
                payload = json.loads(record.value.decode("utf-8"))
                messages.append(
                    SQLFieldValueStreamMessage(
                        message_id=(
                            f"{record.topic}:{record.partition}:{record.offset}"
                        ),
                        event=SQLFieldValueCDCEvent.model_validate(
                            payload["event"]
                        ),
                        attempt=int(payload.get("attempt", 0)),
                        transport_name="kafka",
                        transport_partition=(
                            f"{record.topic}:{record.partition}"
                        ),
                        transport_position=str(record.offset),
                    )
                )
        messages.sort(
            key=lambda item: (
                item.event.source_order,
                item.transport_partition,
                int(item.transport_position),
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
        """发布 Kafka 重试消息或 DLQ，并允许提交原消息 Offset。

        Args:
            message: 处理失败的 Kafka 消息。
            error: 失败原因。
            max_retries: 最大重试次数。

        Returns:
            始终返回真，表示新消息发布成功后可提交原 Offset。
        """

        producer = await self._start_producer()
        next_attempt = message.attempt + 1
        topic = (
            f"{self.config.topic}.dlq"
            if next_attempt > max_retries
            else f"{self.config.topic}.retry"
        )
        payload = json.dumps(
            {
                "event": message.event.model_dump(mode="json"),
                "attempt": next_attempt,
                "error": str(error)[:2000],
                "failed_message_id": message.message_id,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        await producer.send_and_wait(
            topic,
            payload,
            key=str(message.event.database_id).encode("utf-8"),
        )
        return True

    async def ack(
        self,
        messages: Sequence[SQLFieldValueStreamMessage],
    ) -> None:
        """按分区提交每个已处理消息之后的 Offset。

        Args:
            messages: 已成功处理或已经安全转移到重试/DLQ 的消息。
        """

        if not messages:
            return
        consumer = await self._start_consumer()
        try:
            from aiokafka import TopicPartition
        except ImportError as exc:
            raise RuntimeError("Kafka CDC 缺少 aiokafka") from exc
        offsets: dict = {}
        for message in messages:
            topic, partition = message.transport_partition.rsplit(":", 1)
            key = TopicPartition(topic, int(partition))
            offsets[key] = max(
                offsets.get(key, 0),
                int(message.transport_position) + 1,
            )
        await consumer.commit(offsets)

    async def replay_dlq(self, *, max_events: int = 100) -> int:
        """把 Kafka DLQ 消息重新发布到主 Topic。

        Args:
            max_events: 本次最多重放的消息数量。

        Returns:
            已成功重新发布并提交 DLQ Offset 的数量。
        """

        try:
            from aiokafka import AIOKafkaConsumer, TopicPartition
        except ImportError as exc:
            raise RuntimeError("Kafka CDC 缺少 aiokafka") from exc
        options = {
            "bootstrap_servers": list(self.config.bootstrap_servers),
            "group_id": f"{self.config.group_id}-dlq-replay",
            "client_id": f"{self.config.client_id}-dlq-replay",
            "enable_auto_commit": False,
            "auto_offset_reset": "earliest",
        }
        if self.config.security_protocol.upper() != "PLAINTEXT":
            options.update(
                {
                    "security_protocol": self.config.security_protocol,
                    "sasl_mechanism": self.config.sasl_mechanism,
                    "sasl_plain_username": self.config.sasl_username,
                    "sasl_plain_password": self.config.sasl_password,
                }
            )
        consumer = AIOKafkaConsumer(
            f"{self.config.topic}.dlq",
            **options,
        )
        producer = await self._start_producer()
        replayed = 0
        offsets: dict = {}
        await consumer.start()
        try:
            batches = await consumer.getmany(
                timeout_ms=1000,
                max_records=max(1, int(max_events)),
            )
            for topic_partition, records in batches.items():
                for record in records:
                    payload = json.loads(record.value.decode("utf-8"))
                    event = SQLFieldValueCDCEvent.model_validate(
                        payload["event"]
                    )
                    await producer.send_and_wait(
                        self.config.topic,
                        json.dumps(
                            {
                                "event": event.model_dump(mode="json"),
                                "attempt": 0,
                                "replayed_from": (
                                    f"{record.topic}:{record.partition}:"
                                    f"{record.offset}"
                                ),
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ).encode("utf-8"),
                        key=str(event.database_id).encode("utf-8"),
                    )
                    replayed += 1
                    key = TopicPartition(
                        topic_partition.topic,
                        topic_partition.partition,
                    )
                    offsets[key] = max(
                        offsets.get(key, 0),
                        int(record.offset) + 1,
                    )
            if offsets:
                await consumer.commit(offsets)
        finally:
            await consumer.stop()
        return replayed

    async def close(self) -> None:
        """关闭 Kafka 消费者和生产者。

        Returns:
            无返回值。
        """

        if self._consumer is not None:
            await self._consumer.stop()
            self._consumer = None
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None
