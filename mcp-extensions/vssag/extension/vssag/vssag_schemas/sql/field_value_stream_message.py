from __future__ import annotations

from pydantic import BaseModel

from .field_value_cdc_event import SQLFieldValueCDCEvent


class SQLFieldValueStreamMessage(BaseModel):
    """Kafka 或 Redis Stream 消费到的字段值 CDC 消息。"""

    message_id: str
    event: SQLFieldValueCDCEvent
    attempt: int = 0
    transport_name: str
    transport_partition: str
    transport_position: str
