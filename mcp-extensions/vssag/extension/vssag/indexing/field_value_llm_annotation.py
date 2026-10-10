"""对规则不确定的字段值执行受控 LLM 标注。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import deque
from collections.abc import Mapping, Sequence

from pydantic import TypeAdapter

from ..prompts import render_prompt
from ..errors import ProviderError
from ..utils import LLMJSONParseError, parse_llm_json
from ..vssag_schemas.sql import (
    SQLCollectedFieldValue,
    SQLFieldIndexStatistics,
    SQLFieldValueAnnotation,
    SQLFieldValueAnnotationDecision,
)


class SQLFieldValueLLMAnnotator:
    """批量标注不确定字段值，并控制缓存、速率、重试和调用预算。"""

    def __init__(
        self,
        llm,
        store,
        config,
        *,
        model_name: str,
        allow_real_table_names: bool,
    ):
        self.llm = llm
        self.store = store
        self.config = config
        self.model_name = model_name or "unknown"
        self.allow_real_table_names = allow_real_table_names
        self._request_times: deque[float] = deque()
        self._request_lock = asyncio.Lock()
        self._job_usage: dict[str, list[int]] = {}

    async def annotate_page(
        self,
        statistics: SQLFieldIndexStatistics,
        values: Sequence[SQLCollectedFieldValue],
        *,
        random_table_name: str,
    ) -> list[SQLCollectedFieldValue]:
        """标注一页已经通过硬拒绝规则的字段值。

        Args:
            statistics: 当前字段的统计和规则判断。
            values: 已通过敏感值、凭证和技术标识硬拒绝的候选值。
            random_table_name: 当前表的随机名称，用于关闭真实表名时构造提示词。

        Returns:
            带有 LLM 标注的候选值；拒绝项仍返回，Store 只写标注外表。

        Raises:
            RuntimeError: 调用预算耗尽，或 LLM 请求在允许的重试次数内仍失败。
        """

        if not values:
            return []
        prompt_version = self.config.prompt_version
        input_hashes = {
            hashlib.sha256(
                "\x1f".join(
                    [
                        str(statistics.database_id),
                        statistics.schema_name,
                        statistics.raw_table_name,
                        statistics.column_name,
                        item.value.raw_value,
                        self.model_name,
                        prompt_version,
                    ]
                ).encode("utf-8")
            ).hexdigest(): item
            for item in values
        }
        cached: dict[str, SQLFieldValueAnnotation] = {}
        read_cache = getattr(
            self.store,
            "get_cached_field_value_annotations",
            None,
        )
        if read_cache is not None:
            cached = await read_cache(
                list(input_hashes),
                model_name=self.model_name,
                prompt_version=prompt_version,
            )

        annotated: list[SQLCollectedFieldValue] = []
        missing: list[tuple[str, SQLCollectedFieldValue]] = []
        for input_hash, item in input_hashes.items():
            previous = cached.get(input_hash)
            if previous is None:
                missing.append((input_hash, item))
                continue
            accepted = (
                previous.review_status == "approved"
                or (
                    previous.review_status == "pending"
                    and previous.should_index
                    and previous.confidence
                    >= self.config.minimum_confidence
                )
            )
            annotation = previous.model_copy(
                update={
                    "id": None,
                    "value_index_id": None,
                    "collection_job_id": statistics.collection_job_id,
                    "should_index": accepted,
                    "created_at": None,
                    "updated_at": None,
                }
            )
            value = item.value.model_copy(deep=True)
            if accepted:
                value.aliases = list(annotation.aliases)
                value.retrieval_text = " ".join(
                    [value.raw_value, *annotation.aliases]
                ).strip()
            annotated.append(
                item.model_copy(
                    update={
                        "value": value,
                        "annotation": annotation,
                    }
                )
            )

        decision_adapter = TypeAdapter(SQLFieldValueAnnotationDecision)
        batch_size = max(1, int(self.config.batch_size))
        for offset in range(0, len(missing), batch_size):
            batch = missing[offset : offset + batch_size]
            raw_values = [
                {"index": index, "raw_value": item.value.raw_value}
                for index, (_, item) in enumerate(batch)
            ]
            raw_value_indexes = {
                item["raw_value"]: item["index"] for item in raw_values
            }
            visible_table_name = (
                statistics.raw_table_name
                if self.allow_real_table_names
                else random_table_name
            )
            prompt = render_prompt(
                "sql_field_value_label.md",
                table_name=visible_table_name,
                column_name=statistics.column_name,
                column_data_type=statistics.data_type or "未知",
                column_comment=statistics.comment or "无",
                field_score=f"{statistics.field_score:.2f}",
                distinct_count=statistics.distinct_count,
                raw_values=json.dumps(raw_values, ensure_ascii=False),
            )
            usage = self._job_usage.setdefault(
                statistics.collection_job_id,
                [0, 0],
            )
            if usage[0] >= self.config.max_calls_per_job:
                raise RuntimeError(
                    "字段值 LLM 标注达到当前采集任务的最大调用次数"
                )
            if (
                usage[1] + len(prompt)
                > self.config.max_input_characters_per_job
            ):
                raise RuntimeError(
                    "字段值 LLM 标注达到当前采集任务的输入字符上限"
                )

            decisions_by_index: dict[
                int, SQLFieldValueAnnotationDecision
            ] = {}
            failure_reason = ""
            for attempt in range(self.config.max_retries + 1):
                async with self._request_lock:
                    now = time.monotonic()
                    while (
                        self._request_times
                        and now - self._request_times[0] >= 60
                    ):
                        self._request_times.popleft()
                    if (
                        len(self._request_times)
                        >= self.config.requests_per_minute
                    ):
                        wait_seconds = max(
                            0.0,
                            60 - (now - self._request_times[0]),
                        )
                        if wait_seconds:
                            await asyncio.sleep(wait_seconds)
                        now = time.monotonic()
                        while (
                            self._request_times
                            and now - self._request_times[0] >= 60
                        ):
                            self._request_times.popleft()
                    self._request_times.append(time.monotonic())
                usage[0] += 1
                usage[1] += len(prompt)
                try:
                    response_text = await asyncio.wait_for(
                        self.llm.complete(
                            prompt,
                            temperature=0.0,
                            max_tokens=max(1000, len(batch) * 180),
                        ),
                        timeout=float(self.config.timeout_seconds),
                    )
                    payload = parse_llm_json(
                        response_text,
                        required_keys=("items",),
                    )
                    if not isinstance(payload, Mapping):
                        raise ValueError("LLM 标注响应根节点必须是对象")
                    response_items = payload.get("items")
                    if not isinstance(response_items, list):
                        raise ValueError("LLM 标注响应的 items 必须是数组")
                    invalid_indexes: set[int] = set()
                    issues: list[str] = []
                    for response_item in response_items:
                        try:
                            decision = decision_adapter.validate_python(
                                response_item
                            )
                        except (TypeError, ValueError) as exc:
                            issues.append(
                                "存在无法校验的标注项："
                                f"{type(exc).__name__}"
                            )
                            continue
                        if decision.index is not None:
                            index = decision.index
                            if not 0 <= index < len(batch):
                                issues.append("存在越界的标注 index")
                                continue
                        elif decision.raw_value is not None:
                            index = raw_value_indexes.get(
                                decision.raw_value
                            )
                            if index is None:
                                issues.append(
                                    "存在无法关联输入值的 raw_value"
                                )
                                continue
                        else:
                            issues.append(
                                "标注项缺少 index 或 raw_value"
                            )
                            continue
                        if index in invalid_indexes:
                            continue
                        if index in decisions_by_index:
                            decisions_by_index.pop(index)
                            invalid_indexes.add(index)
                            issues.append("存在重复的标注 index")
                            continue
                        decisions_by_index[index] = decision
                    missing_indexes = (
                        set(range(len(batch)))
                        - set(decisions_by_index)
                    )
                    invalid_indexes.update(missing_indexes)
                    if invalid_indexes or issues:
                        details = "；".join(issues[:3])
                        missing_detail = (
                            f"缺少或无效 index: {sorted(invalid_indexes)}"
                            if invalid_indexes
                            else ""
                        )
                        failure_reason = "；".join(
                            part
                            for part in (details, missing_detail)
                            if part
                        )
                        if self.config.failure_policy == "fail":
                            raise ValueError(
                                "LLM 标注响应存在缺失、重复或无效结果"
                                f"：{failure_reason}"
                            )
                    break
                except (
                    asyncio.TimeoutError,
                    ProviderError,
                    ConnectionError,
                    OSError,
                ) as exc:
                    if attempt >= self.config.max_retries:
                        raise RuntimeError(
                            "重试次数超过设置值，字段值 LLM 标注连续失败："
                            f"{type(exc).__name__}: {exc}"
                        ) from exc
                    await asyncio.sleep(min(2**attempt, 5))
                except (LLMJSONParseError, TypeError, ValueError) as exc:
                    if self.config.failure_policy == "fail":
                        raise RuntimeError(
                            "LLM 标注响应无法可靠关联字段值："
                            f"{type(exc).__name__}: {exc}"
                        ) from exc
                    failure_reason = failure_reason or (
                        "LLM 标注响应无法可靠解析或关联，待人工审核"
                    )
                    break

            for index, (input_hash, item) in enumerate(batch):
                decision = decisions_by_index.get(index)
                aliases: list[str] = []
                for alias in decision.aliases if decision else []:
                    cleaned = " ".join(str(alias).split())[:100]
                    if (
                        cleaned
                        and cleaned != item.value.raw_value
                        and cleaned not in aliases
                    ):
                        aliases.append(cleaned)
                    if len(aliases) >= 10:
                        break
                accepted = (
                    decision is not None
                    and decision.should_index
                    and decision.confidence
                    >= self.config.minimum_confidence
                )
                confidence = decision.confidence if decision else 0.0
                reason = (
                    decision.reason
                    if decision
                    else failure_reason
                    or "LLM 未返回该字段值的有效标注结果"
                )
                annotation = SQLFieldValueAnnotation(
                    collection_job_id=statistics.collection_job_id,
                    database_id=statistics.database_id,
                    schema_name=statistics.schema_name,
                    raw_table_name=statistics.raw_table_name,
                    column_name=statistics.column_name,
                    raw_value=item.value.raw_value,
                    should_index=accepted,
                    confidence=confidence,
                    aliases=aliases,
                    reason=reason[:1000],
                    model_name=self.model_name,
                    prompt_version=prompt_version,
                    input_hash=input_hash,
                    review_status=(
                        "approved"
                        if accepted
                        else "rejected"
                        if decision is not None
                        and not decision.should_index
                        and decision.confidence
                        >= self.config.minimum_confidence
                        else "pending"
                    ),
                )
                value = item.value.model_copy(deep=True)
                if accepted:
                    value.aliases = aliases
                    value.retrieval_text = " ".join(
                        [value.raw_value, *aliases]
                    ).strip()
                annotated.append(
                    item.model_copy(
                        update={
                            "value": value,
                            "annotation": annotation,
                        }
                    )
                )
        return annotated
