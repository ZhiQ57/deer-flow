"""VSSAG 到 TypeScript just-bash Worker 的 Bash 语法解析桥接。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shutil
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class JustBashParser:
    """管理 VSSAG 专用的 just-bash 解析 Worker。

    Worker 只接收模型给出的公开 Bash 文本，不传入用户标识、内部隔离标签、
    文件快照或索引内容。
    """

    def __init__(self, *, timeout_seconds: float, worker_dir: str) -> None:
        self.timeout_seconds = timeout_seconds
        self.worker_dir = worker_dir
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def parse(self, command: str) -> list[dict[str, Any]]:
        """使用 just-bash 解析受限 Bash 文本。

        Args:
            command: 模型输出的公开 Bash 指令。

        Returns:
            由 Worker 校验后的语句和管道结构。

        Raises:
            RuntimeError: Worker 不可用、超时或返回的解析结果不合法。
        """

        async with self._lock:
            request_id = uuid.uuid4().hex
            try:
                await self._ensure_started()
                assert self._process is not None
                assert self._process.stdin is not None
                assert self._process.stdout is not None
                payload = {
                    "id": request_id,
                    "operation": "parse",
                    "command": command,
                }
                self._process.stdin.write(
                    (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
                )
                await self._process.stdin.drain()
                while True:
                    raw = await asyncio.wait_for(
                        self._process.stdout.readline(),
                        timeout=self.timeout_seconds,
                    )
                    if not raw:
                        raise RuntimeError("just-bash Worker 在返回前已退出")
                    response = json.loads(raw.decode("utf-8"))
                    if response.get("id") != request_id:
                        continue
                    if not response.get("ok"):
                        message = str(response.get("error") or "Bash 指令解析失败")
                        raise RuntimeError(message)
                    statements = response.get("statements")
                    if not isinstance(statements, list):
                        raise RuntimeError("just-bash Worker 返回了无效的语句结构")
                    return statements
            except Exception as exc:
                await self._stop()
                if isinstance(exc, RuntimeError):
                    raise
                raise RuntimeError(f"just-bash Worker 解析失败: {exc}") from exc

    async def close(self) -> None:
        """停止 Worker 并释放 Node.js 子进程。"""

        async with self._lock:
            await self._stop()

    async def _ensure_started(self) -> None:
        if self._process is not None and self._process.returncode is None:
            return
        configured_path = Path(self.worker_dir).expanduser()
        current_directory = Path.cwd()
        candidates = (
            (configured_path,)
            if configured_path.is_absolute()
            else (
                current_directory / configured_path,
                current_directory.parent / configured_path,
            )
        )
        worker_dir = next(
            (
                candidate.resolve()
                for candidate in candidates
                if (candidate / "worker.mjs").is_file()
                and (candidate / "node_modules" / "just-bash").is_dir()
            ),
            None,
        )
        if worker_dir is None:
            raise RuntimeError(
                "just-bash Worker 不可用；请检查 VSSAG VFS 配置中的 "
                f"just_bash_worker_dir: {self.worker_dir}，并执行 npm ci"
            )
        node = shutil.which("node")
        if not node:
            raise RuntimeError("VSSAG Bash 解析需要 Node.js 和 just-bash Worker")
        self._process = await asyncio.create_subprocess_exec(
            node,
            str(worker_dir / "worker.mjs"),
            cwd=str(worker_dir),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._stderr_task = asyncio.create_task(self._drain_stderr(self._process))

    async def _stop(self) -> None:
        process = self._process
        self._process = None
        stderr_task = self._stderr_task
        self._stderr_task = None
        if process is not None:
            if process.stdin is not None:
                with contextlib.suppress(Exception):
                    process.stdin.close()
            try:
                await asyncio.wait_for(process.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    with contextlib.suppress(ProcessLookupError):
                        process.kill()
                    with contextlib.suppress(Exception):
                        await process.wait()
        if stderr_task is not None:
            try:
                await asyncio.wait_for(stderr_task, timeout=1.0)
            except asyncio.TimeoutError:
                stderr_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await stderr_task

    async def _drain_stderr(self, process: asyncio.subprocess.Process) -> None:
        if process.stderr is None:
            return
        try:
            while True:
                raw = await process.stderr.readline()
                if not raw:
                    return
                logger.warning(
                    "VSSAG just-bash Worker: %s",
                    raw.decode("utf-8", "replace").rstrip(),
                )
        except asyncio.CancelledError:
            return
