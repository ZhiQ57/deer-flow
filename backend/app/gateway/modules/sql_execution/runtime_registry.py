"""Gateway SQL Execution 运行时能力注册表。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import RLock
from typing import Any


@dataclass(slots=True)
class SqlExecutionRunCapability:
    """单次 Gateway Run 的 SQL 执行能力。

    Args:
        run_id: 父运行标识。
        thread_id: 父线程标识。
        user_id: 线程所有者标识。
        agent_name: 当前 Custom Agent 名称。
        binding: Gateway 解析的无密钥数据源绑定。
        secrets: 只驻留 Gateway 内存的请求级 Secret。
        created_at: 注册时间。
        flow_ids: 已授权给 SQL SubAgent 的 Flow 集合。
        validation_generations: 每个 Flow 最新有效校验的代次。
        validation_digests: 每个 Flow 最新有效校验摘要。
        consumed_validation_generations: 每个 Flow 已消费的校验代次。
        attempts: 每个 Flow 已占用的数据库执行次数。
        succeeded: 已经执行成功的 Flow 集合。
    """

    run_id: str
    thread_id: str
    user_id: str
    agent_name: str
    binding: dict[str, Any]
    secrets: dict[str, str]
    created_at: float = field(default_factory=time.monotonic)
    flow_ids: set[str] = field(default_factory=set)
    validation_generations: dict[str, int] = field(default_factory=dict)
    validation_digests: dict[str, str] = field(default_factory=dict)
    consumed_validation_generations: dict[str, int] = field(default_factory=dict)
    attempts: dict[str, int] = field(default_factory=dict)
    succeeded: set[str] = field(default_factory=set)


class SqlExecutionRuntimeRegistry:
    """Gateway 进程内 SQL 执行能力注册表。"""

    def __init__(self, *, ttl_seconds: float = 3600, max_entries: int = 1024) -> None:
        """初始化注册表。

        Args:
            ttl_seconds: Run 能力保留时间。
            max_entries: 最大 Run 能力数量。
        """
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._entries: dict[str, SqlExecutionRunCapability] = {}
        self._lock = RLock()

    def register_run(
        self,
        *,
        run_id: str,
        thread_id: str,
        user_id: str,
        agent_name: str,
        binding: dict[str, Any],
        secrets: dict[str, str],
    ) -> None:
        """登记 Gateway 已认证 Run 的 SQL 能力。

        Args:
            run_id: 父运行标识。
            thread_id: 父线程标识。
            user_id: 当前用户标识。
            agent_name: 当前 Custom Agent 名称。
            binding: 无密钥数据源绑定。
            secrets: 请求级 Secret 副本。

        Returns:
            无返回值。
        """
        with self._lock:
            self._evict_locked()
            self._entries[run_id] = SqlExecutionRunCapability(
                run_id=run_id,
                thread_id=thread_id,
                user_id=user_id,
                agent_name=agent_name,
                binding=dict(binding),
                secrets=dict(secrets),
            )
            self._trim_locked()

    def authorize_flow(
        self,
        *,
        run_id: str,
        thread_id: str,
        user_id: str,
        agent_name: str,
        flow_id: str,
    ) -> bool:
        """授权当前 Run 使用指定 Flow。

        Args:
            run_id: 父运行标识。
            thread_id: 父线程标识。
            user_id: 当前用户标识。
            agent_name: 当前 Custom Agent 名称。
            flow_id: 当前 approved Flow。

        Returns:
            Run 能力存在且身份完全匹配时返回 True。
        """
        with self._lock:
            self._evict_locked()
            entry = self._entries.get(run_id)
            if entry is None or not self._matches(entry, thread_id, user_id, agent_name):
                return False
            entry.flow_ids.add(flow_id)
            return True

    def record_validation(
        self,
        *,
        run_id: str,
        flow_id: str,
        validation_digest: str,
    ) -> bool:
        """登记一次由内部校验路由产生的有效 SQL 校验。

        Args:
            run_id: 父运行标识。
            flow_id: 当前 Flow。
            validation_digest: Gateway 生成的有效校验摘要。

        Returns:
            Run 能力和 Snapshot 仍有效时返回 True。
        """
        with self._lock:
            self._evict_locked()
            entry = self._entries.get(run_id)
            if entry is None or flow_id not in entry.flow_ids:
                return False
            generation = entry.validation_generations.get(flow_id, 0) + 1
            entry.validation_generations[flow_id] = generation
            entry.validation_digests[flow_id] = validation_digest
            return True

    def resolve(
        self,
        *,
        run_id: str,
        thread_id: str,
        user_id: str,
        agent_name: str,
        flow_id: str,
    ) -> SqlExecutionRunCapability | None:
        """解析已授权 SQL 能力。

        Args:
            run_id: 父运行标识。
            thread_id: 父线程标识。
            user_id: 当前用户标识。
            agent_name: 当前 Custom Agent 名称。
            flow_id: 当前 Flow。

        Returns:
            身份及 Snapshot 均匹配的能力；否则返回 None。
        """
        with self._lock:
            self._evict_locked()
            entry = self._entries.get(run_id)
            if entry is None or not self._matches(entry, thread_id, user_id, agent_name):
                return None
            if flow_id not in entry.flow_ids:
                return None
            return entry

    def reserve_attempt(
        self,
        *,
        run_id: str,
        flow_id: str,
        validation_digest: str,
        max_attempts: int,
    ) -> tuple[int, str | None]:
        """原子占用一次数据库执行预算。

        Args:
            run_id: 父运行标识。
            flow_id: 当前 Flow。
            validation_digest: SQL SubAgent 最近一次校验返回的摘要。
            max_attempts: Gateway 配置的最大执行次数。

        Returns:
            ``(attempt, error_category)``；成功时错误类别为 None。
        """
        with self._lock:
            self._evict_locked()
            entry = self._entries.get(run_id)
            if entry is None or flow_id not in entry.flow_ids:
                return 0, "capability_missing"
            attempt = entry.attempts.get(flow_id, 0)
            generation = entry.validation_generations.get(flow_id, 0)
            expected_digest = entry.validation_digests.get(flow_id)
            if generation <= 0 or expected_digest is None:
                return attempt, "validation_missing"
            if expected_digest != validation_digest:
                return attempt, "validation_mismatch"
            if flow_id in entry.succeeded:
                return attempt, "execution_complete"
            if attempt >= max_attempts:
                return attempt, "attempt_budget"
            if entry.consumed_validation_generations.get(flow_id, 0) >= generation:
                return attempt, "validation_reuse"
            attempt += 1
            entry.attempts[flow_id] = attempt
            entry.consumed_validation_generations[flow_id] = generation
            return attempt, None

    def mark_succeeded(self, *, run_id: str, flow_id: str) -> None:
        """标记 Flow 已成功执行。

        Args:
            run_id: 父运行标识。
            flow_id: 当前 Flow。

        Returns:
            无返回值。
        """
        with self._lock:
            entry = self._entries.get(run_id)
            if entry is not None:
                entry.succeeded.add(flow_id)

    def discard_run(self, run_id: str) -> None:
        """删除已结束 Run 的 SQL 能力和请求级数据库 Secret。

        Args:
            run_id: 已完成、失败或取消的父运行标识。

        Returns:
            无返回值。
        """
        with self._lock:
            self._entries.pop(run_id, None)

    def clear(self) -> None:
        """清空注册表，供测试隔离使用。

        Returns:
            无返回值。
        """
        with self._lock:
            self._entries.clear()

    @staticmethod
    def _matches(
        entry: SqlExecutionRunCapability,
        thread_id: str,
        user_id: str,
        agent_name: str,
    ) -> bool:
        """检查 Run 能力身份。

        Args:
            entry: 当前能力记录。
            thread_id: 请求线程标识。
            user_id: 请求用户标识。
            agent_name: 请求 Agent 名称。

        Returns:
            全部身份字段匹配时返回 True。
        """
        return entry.thread_id == thread_id and entry.user_id == user_id and entry.agent_name == agent_name

    def _evict_locked(self) -> None:
        """删除过期能力。

        Returns:
            无返回值。
        """
        cutoff = time.monotonic() - self._ttl_seconds
        expired = [run_id for run_id, entry in self._entries.items() if entry.created_at < cutoff]
        for run_id in expired:
            self._entries.pop(run_id, None)

    def _trim_locked(self) -> None:
        """按创建时间限制注册表容量。

        Returns:
            无返回值。
        """
        overflow = len(self._entries) - self._max_entries
        if overflow <= 0:
            return
        oldest = sorted(self._entries.values(), key=lambda item: item.created_at)[:overflow]
        for entry in oldest:
            self._entries.pop(entry.run_id, None)


sql_execution_runtime_registry = SqlExecutionRuntimeRegistry()
