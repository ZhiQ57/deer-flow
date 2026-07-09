"""字段值索引调度器。"""

from __future__ import annotations

import logging
import signal
import threading
import time
from abc import ABC, abstractmethod

from ....schemas import ValueIndexSyncReport
from .sync_service import SyncFieldValueIndexService

log = logging.getLogger(__name__)


class SyncValueIndexScheduler(ABC):
    """字段值索引调度器抽象类，用于封装一次性或周期性同步任务。"""

    @abstractmethod
    def run_once(self) -> ValueIndexSyncReport:
        """执行一次完整同步。

        Args:
            无。

        Returns:
            字段值索引同步报告。
        """

    @abstractmethod
    def run_forever(self) -> None:
        """按固定间隔持续执行同步，直到收到停止信号。

        Args:
            无。

        Returns:
            None。
        """


class IntervalSyncValueIndexScheduler(SyncValueIndexScheduler):
    """进程内定时调度器，按固定间隔执行字段值索引同步。"""

    def __init__(self, service: SyncFieldValueIndexService, interval_seconds: int):
        """初始化定时调度器。

        Args:
            service: 字段值索引业务服务。
            interval_seconds: 同步间隔秒数，必须大于 0。

        Returns:
            None。
        """
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self.service = service
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()

    def run_once(self) -> ValueIndexSyncReport:
        """执行一次完整同步任务。

        Args:
            无。

        Returns:
            字段值索引同步报告。
        """
        log.info("Starting field value index synchronization")
        report = self.service.sync_all_report()
        log.info(
            "Field value index synchronization finished: status=%s total=%s errors=%s",
            report.status,
            report.total_indexed_count,
            len(report.errors),
        )
        return report

    def run_forever(self) -> None:
        """持续运行定时同步任务，直到收到停止信号。

        Args:
            无。

        Returns:
            None。
        """
        self._install_signal_handlers()
        while not self._stop_event.is_set():
            started_at = time.monotonic()
            try:
                self.run_once()
            except Exception:
                log.exception("Field value index synchronization failed")

            elapsed = time.monotonic() - started_at
            sleep_seconds = max(1, self.interval_seconds - int(elapsed))
            log.info("Next synchronization in %s seconds", sleep_seconds)
            # 使用 Event.wait 代替 sleep，便于 stop() 立即打断等待。
            self._stop_event.wait(sleep_seconds)

    def stop(self) -> None:
        """请求停止周期调度。

        Args:
            无。

        Returns:
            None。
        """
        self._stop_event.set()

    def _install_signal_handlers(self) -> None:
        """安装进程信号处理器，支持 Ctrl+C 或 SIGTERM 优雅停止。

        Args:
            无。

        Returns:
            None。
        """

        def _handle_signal(signum, frame):
            """处理停止信号并请求调度器退出。

            Args:
                signum: 操作系统信号编号。
                frame: 当前栈帧对象。

            Returns:
                None。
            """
            log.info("Received signal %s, stopping scheduler", signum)
            self.stop()

        try:
            signal.signal(signal.SIGINT, _handle_signal)
            signal.signal(signal.SIGTERM, _handle_signal)
        except ValueError:
            # Signal handlers can only be installed in the main thread.
            pass

