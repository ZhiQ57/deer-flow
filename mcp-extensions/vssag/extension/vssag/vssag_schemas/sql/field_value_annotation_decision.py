from __future__ import annotations

from pydantic import BaseModel, Field


class SQLFieldValueAnnotationDecision(BaseModel):
    """LLM 对单个字段值的结构化判断。

    Args:
        index: 输入数组中的稳定位置，优先用于关联数据库原始值。
        raw_value: 旧版 Prompt 返回的原始值，仅用于兼容旧输出。
        should_index: 是否建议建立字段值索引。
        confidence: 标注置信度。
        aliases: 可用于检索的别名。
        reason: 标注原因。
    """

    index: int | None = Field(default=None, ge=0)
    raw_value: str | None = None
    should_index: bool
    confidence: float = Field(ge=0, le=1)
    aliases: list[str] = Field(default_factory=list, max_length=10)
    reason: str = Field(max_length=1000)
