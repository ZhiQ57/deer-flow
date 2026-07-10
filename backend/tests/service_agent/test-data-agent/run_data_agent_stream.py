"""DataAgent 控制台流式执行脚本。

用法示例：

    python backend/tests/service_agent/test-data-agent/run_data_agent_stream.py "查询 2024 年华东 GMV 前 10 商品"
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import platform
import re
import sys
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, NoReturn

_MAX_DATASET_BYTES = 10 * 1024 * 1024
_RUNTIME_ENV_NAMES = (
    "DEER_FLOW_CONFIG_PATH",
    "DEER_FLOW_EXTENSIONS_CONFIG_PATH",
    "TABLERAG_CONFIG",
    "TABLERAG_MCP_CONFIG",
    "TABLERAG_MCP_INDEX_DSN",
    "TABLERAG_INDEX_DSN",
    "TABLERAG_MCP_SOURCE_DSN",
    "TABLERAG_SOURCE_DSN",
    "TABLERAG_MCP_TRANSPORT",
    "TABLERAG_MCP_HOST",
    "TABLERAG_MCP_PORT",
    "TABLERAG_MCP_STREAMABLE_HTTP_PATH",
    "TABLERAG_MCP_SSE_PATH",
    "TABLERAG_MCP_MESSAGE_PATH",
    "TABLERAG_MCP_MOUNT_PATH",
    "TABLERAG_MCP_LOG_LEVEL",
    "TABLERAG_MCP_DEBUG",
    "TABLERAG_MCP_JSON_RESPONSE",
    "TABLERAG_MCP_STATELESS_HTTP",
    "TABLERAG_MCP_MAX_TOP_K",
    "TABLERAG_MCP_MAX_JOIN_HOPS",
    "TABLERAG_MCP_ALLOW_INITIALIZE",
    "TABLERAG_MCP_ALLOW_SYNC_VALUES",
    "DATA_AGENT_MYSQL_DSN",
    "DATA_AGENT_MYSQL_HOST",
    "DATA_AGENT_MYSQL_PORT",
    "DATA_AGENT_MYSQL_USER",
    "DATA_AGENT_MYSQL_PASSWORD",
    "DATA_AGENT_MYSQL_DATABASE",
    "DATA_AGENT_MYSQL_CHARSET",
    "DATA_AGENT_MYSQL_CONNECT_TIMEOUT",
    "DATA_AGENT_MYSQL_READ_TIMEOUT",
    "DATA_AGENT_MYSQL_WRITE_TIMEOUT",
    "DATA_AGENT_SQL_TIMEOUT_MS",
    "DATA_AGENT_SQL_MAX_ROWS",
    "DATA_AGENT_SQL_MAX_CELL_CHARS",
    "DATA_AGENT_SQL_MAX_RESULT_CHARS",
)
_DSN_CREDENTIAL_PATTERN = re.compile(
    r"(?P<prefix>[a-z][a-z0-9+.-]*://[^:/@\s]+:)[^@\s/]+(?=@)",
    flags=re.IGNORECASE,
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?P<prefix>\b(?:password|passwd|pwd|token|secret|api[_-]?key)\b\s*[=:]\s*['\"]?)[^,;\s'\"}]+",
    flags=re.IGNORECASE,
)


class SensitiveDataFormatter(logging.Formatter):
    """格式化文件日志，并对完整日志文本执行凭据脱敏。"""

    def format(self, record: logging.LogRecord) -> str:
        """格式化并脱敏当前日志记录。

        Args:
            record: Python logging 日志记录。

        Return:
            可安全写入日志文件的完整文本。
        """
        # logging.Formatter 可能缓存 exc_text；恢复原值，避免共享 LogRecord 污染其他 handler。
        original_exc_text = record.exc_text
        try:
            return _redact_sensitive_text(super().format(record))
        finally:
            record.exc_text = original_exc_text


class RunLogOutput:
    """DataAgent 控制台与日志文件双通道输出器。"""

    def __init__(
        self,
        *,
        event_logger: logging.Logger,
        stream_logger: logging.Logger,
        handlers: list[logging.Handler],
        log_file: Path,
        root_logger: logging.Logger,
        root_previous_level: int,
    ) -> None:
        """初始化双通道输出器。

        Args:
            event_logger: 同时输出到控制台和文件的标准事件 logger。
            stream_logger: 仅输出到文件的模型文本 logger。
            handlers: 需要在结束时关闭的 logging handler。
            log_file: 当前日志文件绝对路径。
            root_logger: 用于收集 DeerFlow 及依赖日志的根 logger。
            root_previous_level: 脚本启动前的根日志级别。

        Return:
            None。
        """
        self.event_logger = event_logger
        self.stream_logger = stream_logger
        self.handlers = handlers
        self.log_file = log_file
        self.root_logger = root_logger
        self.root_previous_level = root_previous_level
        self._stream_buffer = ""

    def info(self, message: str) -> None:
        """输出 INFO 事件。

        Args:
            message: 单行日志消息。

        Return:
            None。
        """
        self.flush_stream()
        self.event_logger.info(message)

    def error(self, message: str) -> None:
        """输出 ERROR 事件。

        Args:
            message: 单行错误消息。

        Return:
            None。
        """
        self.flush_stream()
        self.event_logger.error(message)

    def stream_text(self, text: str) -> None:
        """把模型增量文本实时打印到控制台，并按完整行写入日志。

        Args:
            text: 模型流式文本增量。

        Return:
            None。
        """
        if not text:
            return
        print(text, end="", flush=True)
        self._stream_buffer += text
        while "\n" in self._stream_buffer:
            line, self._stream_buffer = self._stream_buffer.split("\n", 1)
            self.stream_logger.info("[stream.ai] %s", line.rstrip("\r"))

    def flush_stream(self) -> None:
        """把尚未换行的模型文本写入日志。

        Args:
            无。

        Return:
            None。
        """
        if not self._stream_buffer:
            return
        print(flush=True)
        self.stream_logger.info("[stream.ai] %s", self._stream_buffer.rstrip("\r"))
        self._stream_buffer = ""

    def close(self) -> None:
        """刷新并关闭当前脚本创建的日志 handler。

        Args:
            无。

        Return:
            None。
        """
        self.flush_stream()
        for handler in self.handlers:
            handler.flush()
        for logger in (self.event_logger, self.stream_logger):
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
        self.root_logger.removeHandler(self.handlers[-1])
        self.root_logger.setLevel(self.root_previous_level)
        for handler in self.handlers:
            handler.close()


def _single_line(value: Any) -> str:
    """将日志值转换为单行文本。

    Args:
        value: 任意待记录值。

    Return:
        去除真实换行符后的文本。
    """
    return str(value).replace("\r", "\\r").replace("\n", "\\n")


def _redact_env_value(name: str, value: str | None) -> str:
    """脱敏运行环境变量值。

    Args:
        name: 环境变量名。
        value: 原始环境变量值。

    Return:
        可安全写入日志的变量值。
    """
    if value is None or not value.strip():
        return "<NOT_SET>"
    normalized_name = name.upper()
    if any(token in normalized_name for token in ("PASSWORD", "TOKEN", "SECRET", "API_KEY")):
        return "<SET:REDACTED>"
    if normalized_name.endswith("_DSN"):
        return _DSN_CREDENTIAL_PATTERN.sub(r"\g<prefix>***", value)
    return _single_line(value)


def _redact_sensitive_text(value: str) -> str:
    """脱敏异常和诊断文本中的已知凭据。

    Args:
        value: 原始文本。

    Return:
        脱敏后的文本。
    """
    redacted = _DSN_CREDENTIAL_PATTERN.sub(r"\g<prefix>***", value)
    redacted = _SECRET_ASSIGNMENT_PATTERN.sub(r"\g<prefix>***", redacted)
    for name, secret in os.environ.items():
        if not secret or len(secret) < 4:
            continue
        normalized_name = name.upper()
        if any(token in normalized_name for token in ("PASSWORD", "TOKEN", "SECRET", "API_KEY")):
            redacted = redacted.replace(secret, "***")
    return redacted


def _build_log_file_path(path: Path, *, now: datetime | None = None) -> Path:
    """根据目录或 log.txt 模板生成时间戳日志文件。

    Args:
        path: 日志目录，或类似 `log.txt` 的文件模板。
        now: 可选固定时间，供测试使用。

    Return:
        `log_时间戳.txt` 形式的绝对路径。
    """
    resolved = path.expanduser()
    if not resolved.is_absolute():
        resolved = (Path.cwd() / resolved).resolve()
    else:
        resolved = resolved.resolve()

    if resolved.suffix.lower() == ".txt" or (resolved.exists() and resolved.is_file()):
        directory = resolved.parent
        prefix = resolved.stem or "log"
    else:
        directory = resolved
        prefix = "log"
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now().astimezone()).strftime("%Y%m%d_%H%M%S_%f")[:-3]
    return directory / f"{prefix}_{timestamp}.txt"


def _configure_logging(log_path: Path) -> RunLogOutput:
    """配置标准控制台日志和 UTF-8 文件日志。

    Args:
        log_path: 日志目录或 `log.txt` 文件模板。

    Return:
        当前运行的双通道日志输出器。
    """
    log_file = _build_log_file_path(log_path)
    console_formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_formatter = SensitiveDataFormatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_formatter)
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(file_formatter)

    logger_suffix = uuid.uuid4().hex
    event_logger = logging.getLogger(f"data_agent.runner.{logger_suffix}")
    event_logger.setLevel(logging.INFO)
    event_logger.propagate = False
    event_logger.handlers.clear()
    event_logger.addHandler(console_handler)
    event_logger.addHandler(file_handler)

    stream_logger = logging.getLogger(f"data_agent.stream.{logger_suffix}")
    stream_logger.setLevel(logging.INFO)
    stream_logger.propagate = False
    stream_logger.handlers.clear()
    stream_logger.addHandler(file_handler)
    root_logger = logging.getLogger()
    root_previous_level = root_logger.level
    if root_logger.level == logging.NOTSET or root_logger.level > logging.INFO:
        root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    return RunLogOutput(
        event_logger=event_logger,
        stream_logger=stream_logger,
        handlers=[console_handler, file_handler],
        log_file=log_file,
        root_logger=root_logger,
        root_previous_level=root_previous_level,
    )


def _emit_event(output: RunLogOutput | None, message: str) -> None:
    """输出标准事件，未配置日志时保持原控制台行为。

    Args:
        output: 可选日志输出器。
        message: 事件消息。

    Return:
        None。
    """
    if output is None:
        print(message, flush=True)
        return
    output.info(_single_line(message))


def _emit_stream_text(output: RunLogOutput | None, text: str) -> None:
    """输出模型流式文本。

    Args:
        output: 可选日志输出器。
        text: 模型文本增量。

    Return:
        None。
    """
    if output is None:
        print(text, end="", flush=True)
        return
    output.stream_text(text)


def _repo_root() -> Path:
    """定位仓库根目录。

    Args:
        无。

    Return:
        仓库根目录绝对路径。
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "AGENTS.md").is_file() and (candidate / "backend" / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError("无法从 DataAgent 测试脚本位置定位 DeerFlow 仓库根目录。")


def _prepare_imports(repo_root: Path) -> None:
    """准备 harness 与 deerflow-dev 的导入路径。

    Args:
        repo_root: 仓库根目录。

    Return:
        None。
    """
    harness_dir = repo_root / "backend" / "packages" / "harness"
    dev_dir = harness_dir / "deerflow-dev"
    for path in (str(dev_dir), str(harness_dir)):
        if path not in sys.path:
            sys.path.insert(0, path)


def _parse_aliases(items: list[str]) -> dict[str, str]:
    """解析命令行黑话映射。

    Args:
        items: `黑话=标准值` 字符串列表。

    Return:
        黑话映射字典。
    """
    aliases: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--alias 需要使用 `黑话=标准值` 格式：{item}")
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise SystemExit(f"--alias 不能包含空键或空值：{item}")
        aliases[key] = value
    return aliases


def _content_to_text(content: Any) -> str:
    """提取 LangChain 消息内容文本。

    Args:
        content: LangChain 消息 content。

    Return:
        可打印文本。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return str(content)


def _print_custom_event(chunk: Any, output: RunLogOutput | None = None) -> None:
    """打印 DataAgent custom stream 事件。

    Args:
        chunk: custom stream 数据。
        output: 可选日志输出器。

    Return:
        None。
    """
    if isinstance(chunk, dict) and chunk.get("type") == "data_query_context":
        payload = json.dumps(chunk.get("context"), ensure_ascii=False, separators=(",", ":"), default=str)
        _emit_event(output, f"[DataAgent:QueryContext] {payload}")
        return
    payload = json.dumps(chunk, ensure_ascii=False, separators=(",", ":"), default=str)
    _emit_event(output, f"[custom] {payload}")


def _print_message_chunk(chunk: Any, output: RunLogOutput | None = None) -> None:
    """打印 messages stream 增量。

    Args:
        chunk: messages stream 数据。
        output: 可选日志输出器。

    Return:
        None。
    """
    from langchain_core.messages import AIMessageChunk, ToolMessage

    message = chunk[0] if isinstance(chunk, tuple) and len(chunk) == 2 else chunk
    if isinstance(message, AIMessageChunk):
        text = _content_to_text(message.content)
        if text:
            _emit_stream_text(output, text)
        tool_chunks = getattr(message, "tool_call_chunks", None) or []
        for tool_call in tool_chunks:
            name = tool_call.get("name")
            if name:
                _emit_event(output, f"[tool-call] {name}")
        return
    if isinstance(message, ToolMessage):
        content = _single_line(_content_to_text(message.content)[:2000])
        _emit_event(output, f"[tool-result] name={message.name or message.tool_call_id} content={content}")


def _print_values_event(
    chunk: Any,
    observed: dict[str, Any],
    output: RunLogOutput | None = None,
) -> None:
    """打印 DataAgent 关键状态变化。

    Args:
        chunk: values stream 状态快照。
        observed: 已打印状态摘要。
        output: 可选日志输出器。

    Return:
        None。
    """
    if not isinstance(chunk, dict):
        return

    stage = chunk.get("data_agent_stage")
    if stage and stage != observed.get("stage"):
        observed["stage"] = stage
        _emit_event(output, f"[DataAgent:Stage] {stage}")

    retrieval = chunk.get("data_retrieval_context")
    retrieval_digest = retrieval.get("content_sha256") if isinstance(retrieval, dict) else None
    if retrieval_digest and retrieval_digest != observed.get("retrieval_digest"):
        observed["retrieval_digest"] = retrieval_digest
        _emit_event(
            output,
            f"[DataAgent:Retrieval] ok={retrieval.get('ok')} tool={retrieval.get('tool_name')} query={retrieval.get('query')}",
        )

    validation = chunk.get("data_sql_validation")
    validation_digest = validation.get("sql_sha256") or validation.get("error") if isinstance(validation, dict) else None
    if validation_digest and validation_digest != observed.get("validation_digest"):
        observed["validation_digest"] = validation_digest
        _emit_event(
            output,
            f"[DataAgent:SQLValidation] valid={validation.get('valid')} limit_applied={validation.get('limit_applied')} error={validation.get('error')}",
        )

    execution = chunk.get("data_sql_execution")
    execution_digest = execution.get("sql_sha256") or execution.get("error") if isinstance(execution, dict) else None
    if execution_digest and execution_digest != observed.get("execution_digest"):
        observed["execution_digest"] = execution_digest
        _emit_event(
            output,
            f"[DataAgent:SQLExecution] ok={execution.get('ok')} rows={execution.get('row_count')} truncated={execution.get('truncated')} elapsed_ms={execution.get('elapsed_ms')} error={execution.get('error')}",
        )

    chart = chunk.get("data_chart_spec")
    chart_digest = json.dumps(chart, ensure_ascii=False, sort_keys=True, default=str) if isinstance(chart, dict) else None
    if chart_digest and chart_digest != observed.get("chart_digest"):
        observed["chart_digest"] = chart_digest
        _emit_event(
            output,
            f"[DataAgent:ChartSpec] type={chart.get('type')} x={chart.get('x')} y={chart.get('y')}",
        )


def _load_dataset_questions(
    path: Path,
    *,
    sample_count: int,
    question_column: str,
    encoding: str | None = None,
) -> list[str]:
    """从 CSV 读取少量 DataAgent 测试问题。

    Args:
        path: CSV 路径。
        sample_count: 最大问题数。
        question_column: 问题列名。
        encoding: 可选显式编码。

    Return:
        去重后的问题列表。
    """
    if not 1 <= sample_count <= 100:
        raise SystemExit("--sample-count 必须在 1 到 100 之间")
    if not path.is_file():
        raise SystemExit(f"CSV 文件不存在：{path}")
    if path.stat().st_size > _MAX_DATASET_BYTES:
        raise SystemExit(f"CSV 文件不能超过 {_MAX_DATASET_BYTES // (1024 * 1024)} MB")
    encodings = [encoding] if encoding else ["utf-8-sig", "gb18030"]
    last_error: Exception | None = None
    questions: list[str] | None = None
    for candidate in encodings:
        if not candidate:
            continue
        try:
            with path.open("r", encoding=candidate, newline="") as file:
                reader = csv.DictReader(file)
                current: list[str] = []
                for row in reader:
                    question = str(row.get(question_column) or "").strip()
                    if not question or question in current:
                        continue
                    current.append(question)
                    if len(current) >= sample_count:
                        break
                questions = current
            break
        except UnicodeDecodeError as exc:
            last_error = exc
    if questions is None:
        raise SystemExit(f"无法识别 CSV 编码：{last_error}")

    if not questions:
        raise SystemExit(f"CSV 中没有找到非空列：{question_column}")
    return questions


def _resolve_dataset_path(path: Path, *, repo_root: Path) -> Path:
    """解析相对 CSV 路径并优先支持脚本同目录数据集。

    Args:
        path: 命令行传入的 CSV 路径。
        repo_root: DeerFlow 仓库根目录。

    Return:
        解析后的 CSV 绝对路径。
    """
    if path.is_absolute():
        return path.resolve()
    candidates = [
        (Path.cwd() / path).resolve(),
        (Path(__file__).resolve().parent / path).resolve(),
        (repo_root / path).resolve(),
    ]
    return next((candidate for candidate in candidates if candidate.is_file()), candidates[-1])


def _log_runtime_context(
    output: RunLogOutput,
    *,
    args: argparse.Namespace,
    repo_root: Path,
) -> None:
    """在日志开头记录命令行参数和相关环境变量。

    Args:
        output: 当前运行日志输出器。
        args: 已解析命令行参数。
        repo_root: DeerFlow 仓库根目录。

    Return:
        None。
    """
    output.info("runtime.start")
    output.info(f"runtime.started_at={datetime.now().astimezone().isoformat()}")
    output.info(f"runtime.log_file={output.log_file}")
    output.info(f"runtime.repo_root={repo_root}")
    output.info(f"runtime.cwd={Path.cwd().resolve()}")
    output.info(f"runtime.script={Path(__file__).resolve()}")
    output.info(f"runtime.python={sys.executable}")
    output.info(f"runtime.python_version={platform.python_version()}")
    output.info(f"runtime.platform={platform.platform()}")
    output.info(f"runtime.argv={json.dumps(sys.argv, ensure_ascii=False, default=str)}")

    for name, value in sorted(vars(args).items()):
        if isinstance(value, Path):
            serialized = str(value.expanduser().resolve()) if value.is_absolute() else str(value)
        else:
            serialized = json.dumps(value, ensure_ascii=False, default=str)
        output.info(f"arg.{name}={_single_line(serialized)}")

    for name in _RUNTIME_ENV_NAMES:
        output.info(f"env.{name}={_redact_env_value(name, os.environ.get(name))}")

    relevant_paths = {
        "config": os.environ.get("DEER_FLOW_CONFIG_PATH"),
        "extensions_config": os.environ.get("DEER_FLOW_EXTENSIONS_CONFIG_PATH"),
        "tablerag_config": os.environ.get("TABLERAG_CONFIG"),
        "data_agent_config": str(repo_root / ".deer-flow" / "users" / "default" / "agents" / "data-agent" / "config.yaml"),
        "data_agent_soul": str(repo_root / ".deer-flow" / "users" / "default" / "agents" / "data-agent" / "SOUL.md"),
    }
    for name, value in relevant_paths.items():
        if not value:
            output.info(f"path.{name}=<NOT_SET>")
            continue
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = (repo_root / path).resolve()
        output.info(f"path.{name}={path} exists={path.is_file()}")


def _build_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。

    Args:
        无。

    Return:
        argparse.ArgumentParser 实例。
    """
    parser = argparse.ArgumentParser(description="Run experimental DataAgent with console streaming logs.")
    parser.add_argument("question", nargs="*", help="用户数据问题")
    parser.add_argument("--thread-id", default=None, help="可选 thread_id；默认随机生成")
    parser.add_argument("--model", default=None, help="覆盖 config.yaml 中的模型名")
    parser.add_argument("--config", default=None, help="DEER_FLOW_CONFIG_PATH")
    parser.add_argument("--extensions-config", default=None, help="DEER_FLOW_EXTENSIONS_CONFIG_PATH")
    parser.add_argument("--dataset", type=Path, default=None, help="从 CSV 指标数据集读取少量测试问题")
    parser.add_argument("--sample-count", type=int, default=2, help="CSV 模式最多执行的问题数")
    parser.add_argument("--dataset-question-column", default="指标名称", help="CSV 中作为用户问题的列名")
    parser.add_argument("--dataset-encoding", default=None, help="可选 CSV 编码；默认自动尝试 utf-8-sig/gb18030")
    parser.add_argument(
        "--log-path",
        type=Path,
        default=Path(__file__).resolve().parent / "logs",
        help="日志输出目录或 log.txt 模板；实际文件名自动追加时间戳",
    )
    parser.add_argument("--alias", action="append", default=[], help="追加黑话映射，格式：黑话=标准值")
    parser.add_argument("--no-thinking", action="store_true", help="关闭 thinking_enabled")
    parser.add_argument("--enable-subagent", action="store_true", help="启用受限自定义 task 子代理；默认关闭")
    parser.add_argument(
        "--allowed-subagent",
        action="append",
        default=[],
        help="允许委托的自定义子代理名称；启用子代理时至少配置一个",
    )
    parser.add_argument("--skip-db-preflight", action="store_true", help="跳过 MySQL SELECT 1 只读连通性检查")
    parser.add_argument("--recursion-limit", type=int, default=150, help="LangGraph recursion_limit")
    return parser


def _abort_with_parser_error(
    parser: argparse.ArgumentParser,
    output: RunLogOutput,
    message: str,
) -> NoReturn:
    """记录参数错误并按 argparse 标准退出。

    Args:
        parser: 当前命令行解析器。
        output: 当前运行日志输出器。
        message: 参数错误说明。

    Return:
        不返回；由 argparse 抛出 SystemExit。
    """
    output.error(f"runtime.invalid_arguments message={_single_line(message)}")
    parser.error(message)


def _run_question(
    args: argparse.Namespace,
    question: str,
    *,
    sequence: int,
    output: RunLogOutput,
) -> None:
    """执行单个 DataAgent 问题。

    Args:
        args: 命令行参数。
        question: 用户问题。
        sequence: 问题序号。
        output: 当前运行日志输出器。

    Return:
        None。
    """
    from agents.data_agent import make_data_agent
    from langchain_core.messages import HumanMessage

    thread_id = args.thread_id or f"data-agent-{uuid.uuid4()}"
    if args.thread_id and sequence > 1:
        thread_id = f"{args.thread_id}-{sequence}"
    alias_map = _parse_aliases(args.alias)
    run_context = {
        "thread_id": thread_id,
        "data_agent_alias_map": alias_map,
    }
    configurable = {
        "thread_id": thread_id,
        "agent_name": "data-agent",
        "thinking_enabled": not args.no_thinking,
        "subagent_enabled": args.enable_subagent,
        "data_agent_allowed_subagents": args.allowed_subagent,
    }
    if args.model:
        configurable["model_name"] = args.model
    run_config = {
        "configurable": configurable,
        "context": run_context,
        "recursion_limit": args.recursion_limit,
    }

    graph = make_data_agent(run_config)
    output.info(f"[DataAgent] case={sequence} thread_id={thread_id}")
    output.info(f"[User] {question}")
    output.info(f"[RunConfig] {_single_line(json.dumps(run_config, ensure_ascii=False, default=str))}")
    observed: dict[str, Any] = {}
    for item in graph.stream(
        {"messages": [HumanMessage(content=question)]},
        config=run_config,
        context=run_context,
        stream_mode=["values", "messages", "custom"],
    ):
        if isinstance(item, tuple) and len(item) == 2:
            mode, chunk = item
        else:
            mode, chunk = "values", item
        if mode == "custom":
            _print_custom_event(chunk, output)
        elif mode == "messages":
            _print_message_chunk(chunk, output)
        elif mode == "values":
            _print_values_event(chunk, observed, output)
    output.info("[DataAgent] done")


def main() -> int:
    """执行 DataAgent 并打印流式输出。

    Args:
        无。

    Return:
        进程退出码。
    """
    parser = _build_parser()
    args = parser.parse_args()
    repo_root = _repo_root()
    _prepare_imports(repo_root)

    if args.config:
        os.environ["DEER_FLOW_CONFIG_PATH"] = str(Path(args.config).resolve())
    if args.extensions_config:
        os.environ["DEER_FLOW_EXTENSIONS_CONFIG_PATH"] = str(Path(args.extensions_config).resolve())

    output = _configure_logging(args.log_path)
    try:
        _log_runtime_context(output, args=args, repo_root=repo_root)
        questions: list[str] = []
        direct_question = " ".join(args.question).strip()
        if direct_question:
            questions.append(direct_question)
        if args.dataset:
            dataset_path = _resolve_dataset_path(args.dataset, repo_root=repo_root)
            output.info(f"dataset.resolved_path={dataset_path}")
            questions.extend(
                question
                for question in _load_dataset_questions(
                    dataset_path,
                    sample_count=args.sample_count,
                    question_column=args.dataset_question_column,
                    encoding=args.dataset_encoding,
                )
                if question not in questions
            )
        if not questions:
            _abort_with_parser_error(parser, output, "必须提供 question 或 --dataset")
        if args.enable_subagent and not args.allowed_subagent:
            _abort_with_parser_error(parser, output, "--enable-subagent 必须至少搭配一个 --allowed-subagent")
        if args.allowed_subagent and not args.enable_subagent:
            _abort_with_parser_error(parser, output, "--allowed-subagent 只能与 --enable-subagent 一起使用")
        if not 10 <= args.recursion_limit <= 1_000:
            _abort_with_parser_error(parser, output, "--recursion-limit 必须在 10 到 1000 之间")
        output.info(f"runtime.question_count={len(questions)}")

        if not args.skip_db_preflight:
            from agents.data_agent import MySQLExecutionSettings, execute_readonly_sql

            settings = MySQLExecutionSettings.from_env()
            output.info(f"[DataAgent:MySQL] preflight {settings.safe_description()}")
            preflight = execute_readonly_sql("SELECT 1 AS data_agent_healthcheck", settings=settings)
            output.info(f"[DataAgent:MySQL] ok={preflight['ok']} elapsed_ms={preflight['elapsed_ms']}")

        for sequence, question in enumerate(questions, 1):
            _run_question(args, question, sequence=sequence, output=output)
        output.info("runtime.completed exit_code=0")
        return 0
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else 1
        if isinstance(exc.code, str):
            output.error(f"runtime.aborted exit_code={exit_code} message={_single_line(exc.code)}")
        else:
            output.error(f"runtime.aborted exit_code={exit_code}")
        raise
    except Exception as exc:
        output.error(f"runtime.failed type={exc.__class__.__name__} message={_single_line(_redact_sensitive_text(str(exc)))}")
        for line in _redact_sensitive_text(traceback.format_exc()).splitlines():
            output.error(f"runtime.traceback={_single_line(line)}")
        return 1
    finally:
        output.close()


if __name__ == "__main__":
    raise SystemExit(main())
