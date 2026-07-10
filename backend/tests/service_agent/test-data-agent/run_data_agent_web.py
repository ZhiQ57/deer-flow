"""DataAgent 本地可视化调试页面。

用法示例：

    python backend/tests/service_agent/test-data-agent/run_data_agent_web.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from run_data_agent_stream import (  # noqa: E402
    _RUNTIME_ENV_NAMES,
    RunLogOutput,
    _configure_logging,
    _content_to_text,
    _prepare_imports,
    _redact_env_value,
    _redact_sensitive_text,
    _repo_root,
    _single_line,
)

_MAX_TOOL_RESULT_CHARS = 20_000
_MAX_REQUEST_BYTES = 64 * 1024
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_THREAD_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]+$")


@dataclass(frozen=True)
class WebSettings:
    """DataAgent 本地调试页面启动配置。"""

    host: str = "127.0.0.1"
    port: int = 8765
    log_path: Path = SCRIPT_DIR / "logs"
    model_name: str | None = None
    thinking_enabled: bool = True
    skip_db_preflight: bool = False
    recursion_limit: int = 150


class ChatRequest(BaseModel):
    """浏览器提交的单轮 DataAgent 请求。"""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=10_000)
    thread_id: str | None = Field(default=None, max_length=128)
    aliases: dict[str, str] = Field(default_factory=dict)
    model_name: str | None = Field(default=None, max_length=100)
    thinking_enabled: bool | None = None
    skip_db_preflight: bool | None = None
    recursion_limit: int | None = Field(default=None, ge=10, le=1_000)

    @field_validator("question")
    @classmethod
    def _validate_question(cls, value: str) -> str:
        """去除问题首尾空白并拒绝空问题。

        Args:
            value: 原始问题。

        Return:
            清理后的问题。
        """
        normalized = value.strip()
        if not normalized:
            raise ValueError("question 不能为空。")
        return normalized

    @field_validator("thread_id")
    @classmethod
    def _validate_thread_id(cls, value: str | None) -> str | None:
        """限制 thread_id 字符范围。

        Args:
            value: 可选 thread_id。

        Return:
            校验后的 thread_id。
        """
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if not _THREAD_ID_PATTERN.fullmatch(normalized):
            raise ValueError("thread_id 只能包含字母、数字、点、下划线、冒号和连字符。")
        return normalized

    @field_validator("model_name")
    @classmethod
    def _normalize_model_name(cls, value: str | None) -> str | None:
        """清理可选模型名。

        Args:
            value: 可选模型名。

        Return:
            清理后的模型名。
        """
        normalized = value.strip() if value else ""
        return normalized or None

    @model_validator(mode="after")
    def _validate_aliases(self) -> ChatRequest:
        """限制黑话映射规模和字段长度。

        Args:
            无。

        Return:
            当前请求。
        """
        if len(self.aliases) > 200:
            raise ValueError("aliases 最多允许 200 项。")
        normalized: dict[str, str] = {}
        for key, value in self.aliases.items():
            clean_key = str(key).strip()
            clean_value = str(value).strip()
            if not clean_key or not clean_value:
                raise ValueError("aliases 不能包含空键或空值。")
            if len(clean_key) > 100 or len(clean_value) > 200:
                raise ValueError("aliases 键或值超过长度限制。")
            normalized[clean_key] = clean_value
        self.aliases = normalized
        return self


@dataclass
class WebRuntime:
    """调试页面运行时依赖。"""

    repo_root: Path
    settings: WebSettings
    checkpointer: Any
    run_lock: threading.Lock


EventSource = Callable[[WebRuntime, ChatRequest], Iterator[dict[str, Any]]]


def _event_digest(value: Any) -> str:
    """生成结构化事件的稳定摘要。

    Args:
        value: 任意可 JSON 序列化值。

    Return:
        稳定 JSON 文本。
    """
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _truncate_text(value: str, *, limit: int = _MAX_TOOL_RESULT_CHARS) -> tuple[str, bool]:
    """限制发送到浏览器的工具文本长度。

    Args:
        value: 原始文本。
        limit: 最大字符数。

    Return:
        截断后的文本及是否截断。
    """
    if len(value) <= limit:
        return value, False
    return value[:limit] + "\n...[浏览器调试输出已截断]", True


def _custom_events(chunk: Any, observed: dict[str, Any]) -> list[dict[str, Any]]:
    """将 custom stream 数据转换为浏览器事件。

    Args:
        chunk: LangGraph custom stream 数据。
        observed: 当前运行的去重状态。

    Return:
        结构化浏览器事件列表。
    """
    if isinstance(chunk, dict) and chunk.get("type") == "data_query_context":
        payload = chunk.get("context") or {}
        digest = _event_digest(payload)
        if digest == observed.get("digest:data_query_context"):
            return []
        observed["digest:data_query_context"] = digest
        return [{"type": "query_context", "payload": payload}]
    return [{"type": "custom", "payload": chunk}]


def _message_events(chunk: Any, observed: dict[str, Any]) -> list[dict[str, Any]]:
    """将 messages stream 数据转换为浏览器事件。

    Args:
        chunk: LangGraph messages stream 数据。
        observed: 当前运行的去重状态。

    Return:
        AI 文本、工具调用或工具结果事件。
    """
    from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

    message = chunk[0] if isinstance(chunk, tuple) and len(chunk) == 2 else chunk
    events: list[dict[str, Any]] = []
    if isinstance(message, (AIMessageChunk, AIMessage)):
        text = _content_to_text(message.content)
        if text:
            events.append({"type": "ai_delta", "text": text})
        tool_calls = getattr(message, "tool_call_chunks", None) or getattr(message, "tool_calls", None) or []
        observed_calls = observed.setdefault("tool_calls", set())
        for index, tool_call in enumerate(tool_calls):
            if not isinstance(tool_call, dict):
                continue
            name = tool_call.get("name")
            if not name:
                continue
            message_id = getattr(message, "id", None) or "message"
            key = str(tool_call.get("id") or f"{message_id}:{tool_call.get('index', index)}:{name}")
            if key in observed_calls:
                continue
            observed_calls.add(key)
            events.append(
                {
                    "type": "tool_call",
                    "name": name,
                    "tool_call_id": tool_call.get("id"),
                }
            )
        return events

    if isinstance(message, ToolMessage):
        raw_content = _content_to_text(message.content)
        content, truncated = _truncate_text(raw_content)
        events.append(
            {
                "type": "tool_result",
                "name": message.name or message.tool_call_id or "unknown",
                "tool_call_id": message.tool_call_id,
                "content": content,
                "truncated": truncated,
            }
        )
    return events


def _values_events(chunk: Any, observed: dict[str, Any]) -> list[dict[str, Any]]:
    """将 values 状态快照转换为去重后的业务轨迹事件。

    Args:
        chunk: LangGraph values stream 状态。
        observed: 当前运行的去重状态。

    Return:
        QueryContext、阶段、检索、SQL、执行结果和图表事件。
    """
    if not isinstance(chunk, dict):
        return []

    events: list[dict[str, Any]] = []
    mappings = (
        ("data_query_context", "query_context"),
        ("data_retrieval_context", "retrieval"),
        ("data_generated_sql", "generated_sql"),
        ("data_sql_validation", "sql_validation"),
        ("data_sql_execution", "sql_execution"),
        ("data_chart_spec", "chart_spec"),
    )

    stage = chunk.get("data_agent_stage")
    if stage and stage != observed.get("stage"):
        observed["stage"] = stage
        events.append({"type": "stage", "stage": stage})

    for state_key, event_type in mappings:
        value = chunk.get(state_key)
        if value is None:
            continue
        digest = _event_digest(value)
        observed_key = f"digest:{state_key}"
        if digest == observed.get(observed_key):
            continue
        observed[observed_key] = digest
        event: dict[str, Any] = {"type": event_type, "payload": value}
        if event_type == "sql_execution":
            event["last_successful"] = chunk.get("data_last_successful_sql_execution")
        events.append(event)
    return events


def _graph_item_events(item: Any, observed: dict[str, Any]) -> list[dict[str, Any]]:
    """转换单个 LangGraph 多模式流事件。

    Args:
        item: LangGraph stream 返回项。
        observed: 当前运行的去重状态。

    Return:
        浏览器结构化事件列表。
    """
    if isinstance(item, tuple) and len(item) == 2:
        mode, chunk = item
    else:
        mode, chunk = "values", item
    if mode == "custom":
        return _custom_events(chunk, observed)
    if mode == "messages":
        return _message_events(chunk, observed)
    if mode == "values":
        return _values_events(chunk, observed)
    return [{"type": "unknown", "mode": str(mode), "payload": chunk}]


def _record_event(output: RunLogOutput, event: dict[str, Any]) -> None:
    """将浏览器事件同步写入时间戳日志。

    Args:
        output: 当前日志输出器。
        event: 结构化浏览器事件。

    Return:
        None。
    """
    if event.get("type") == "ai_delta":
        output.stream_text(str(event.get("text") or ""))
        return
    output.info(f"web.event={_single_line(_event_digest(event))}")


def _log_web_runtime_context(
    output: RunLogOutput,
    *,
    runtime: WebRuntime,
    request: ChatRequest,
    run_id: str,
    thread_id: str,
) -> None:
    """记录 Web 调试运行的参数、路径和环境变量。

    Args:
        output: 当前日志输出器。
        runtime: Web 运行时。
        request: 浏览器请求。
        run_id: 当前运行 ID。
        thread_id: 当前会话 ID。

    Return:
        None。
    """
    output.info("runtime.start")
    output.info("runtime.entry=data-agent-web")
    output.info(f"runtime.started_at={datetime.now().astimezone().isoformat()}")
    output.info(f"runtime.run_id={run_id}")
    output.info(f"runtime.thread_id={thread_id}")
    output.info(f"runtime.log_file={output.log_file}")
    output.info(f"runtime.repo_root={runtime.repo_root}")
    output.info(f"runtime.cwd={Path.cwd().resolve()}")
    output.info(f"runtime.script={Path(__file__).resolve()}")
    output.info(f"runtime.python={sys.executable}")
    output.info(f"request.question={_single_line(request.question)}")
    output.info(f"request.aliases={_single_line(_event_digest(request.aliases))}")
    output.info(f"request.model_name={request.model_name or runtime.settings.model_name or '<DEFAULT>'}")
    output.info(f"request.thinking_enabled={request.thinking_enabled if request.thinking_enabled is not None else runtime.settings.thinking_enabled}")
    output.info(f"request.skip_db_preflight={request.skip_db_preflight if request.skip_db_preflight is not None else runtime.settings.skip_db_preflight}")
    output.info(f"request.recursion_limit={request.recursion_limit if request.recursion_limit is not None else runtime.settings.recursion_limit}")
    for name in _RUNTIME_ENV_NAMES:
        output.info(f"env.{name}={_redact_env_value(name, os.environ.get(name))}")


def _run_events(runtime: WebRuntime, request: ChatRequest) -> Iterator[dict[str, Any]]:
    """执行单轮 DataAgent，并产出浏览器结构化轨迹。

    Args:
        runtime: Web 运行时。
        request: 浏览器请求。

    Yields:
        NDJSON 响应中的结构化事件。
    """
    from agents.data_agent import MySQLExecutionSettings, build_data_agent, execute_readonly_sql
    from langchain_core.messages import HumanMessage

    run_id = f"web-{uuid.uuid4()}"
    thread_id = request.thread_id or f"data-agent-web-{uuid.uuid4()}"
    model_name = request.model_name or runtime.settings.model_name
    thinking_enabled = request.thinking_enabled if request.thinking_enabled is not None else runtime.settings.thinking_enabled
    skip_db_preflight = request.skip_db_preflight if request.skip_db_preflight is not None else runtime.settings.skip_db_preflight
    recursion_limit = request.recursion_limit or runtime.settings.recursion_limit
    started = time.perf_counter()
    output = _configure_logging(runtime.settings.log_path)

    try:
        _log_web_runtime_context(
            output,
            runtime=runtime,
            request=request,
            run_id=run_id,
            thread_id=thread_id,
        )
        run_context = {
            "thread_id": thread_id,
            "data_agent_alias_map": request.aliases,
        }
        configurable: dict[str, Any] = {
            "thread_id": thread_id,
            "agent_name": "data-agent",
            "thinking_enabled": thinking_enabled,
            "subagent_enabled": False,
        }
        if model_name:
            configurable["model_name"] = model_name
        run_config = {
            "configurable": configurable,
            "context": run_context,
            "recursion_limit": recursion_limit,
        }
        started_event = {
            "type": "run_started",
            "run_id": run_id,
            "thread_id": thread_id,
            "question": request.question,
            "log_file": str(output.log_file),
            "model_name": model_name,
            "thinking_enabled": thinking_enabled,
            "skip_db_preflight": skip_db_preflight,
            "recursion_limit": recursion_limit,
        }
        _record_event(output, started_event)
        yield started_event

        if skip_db_preflight:
            preflight_event = {"type": "preflight", "status": "skipped"}
            _record_event(output, preflight_event)
            yield preflight_event
        else:
            settings = MySQLExecutionSettings.from_env()
            preflight_started = {
                "type": "preflight",
                "status": "running",
                "database": settings.safe_description(),
            }
            _record_event(output, preflight_started)
            yield preflight_started
            preflight = execute_readonly_sql("SELECT 1 AS data_agent_healthcheck", settings=settings)
            preflight_completed = {
                "type": "preflight",
                "status": "completed",
                "ok": preflight.get("ok"),
                "elapsed_ms": preflight.get("elapsed_ms"),
            }
            _record_event(output, preflight_completed)
            yield preflight_completed

        graph = build_data_agent(run_config, checkpointer=runtime.checkpointer)
        observed: dict[str, Any] = {}
        for item in graph.stream(
            {"messages": [HumanMessage(content=request.question)]},
            config=run_config,
            context=run_context,
            stream_mode=["values", "messages", "custom"],
        ):
            for event in _graph_item_events(item, observed):
                _record_event(output, event)
                yield event

        completed_event = {
            "type": "run_completed",
            "run_id": run_id,
            "thread_id": thread_id,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "log_file": str(output.log_file),
        }
        _record_event(output, completed_event)
        yield completed_event
    except Exception as exc:
        error_event = {
            "type": "error",
            "run_id": run_id,
            "thread_id": thread_id,
            "error_type": exc.__class__.__name__,
            "message": _redact_sensitive_text(str(exc)),
            "traceback": _redact_sensitive_text(traceback.format_exc()),
            "log_file": str(output.log_file),
        }
        output.error(f"runtime.failed event={_single_line(_event_digest(error_event))}")
        yield error_event
    finally:
        output.close()


def _encode_ndjson(event: dict[str, Any]) -> str:
    """编码单行 NDJSON 事件。

    Args:
        event: 结构化事件。

    Return:
        以换行结尾的 JSON 文本。
    """
    return json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=str) + "\n"


def _environment_snapshot(repo_root: Path, settings: WebSettings) -> dict[str, Any]:
    """生成页面可展示的脱敏运行环境摘要。

    Args:
        repo_root: 仓库根目录。
        settings: Web 启动配置。

    Return:
        脱敏环境和路径状态。
    """
    config_path = os.environ.get("DEER_FLOW_CONFIG_PATH")
    extensions_path = os.environ.get("DEER_FLOW_EXTENSIONS_CONFIG_PATH")
    tablerag_path = os.environ.get("TABLERAG_CONFIG")

    def path_state(value: str | None, fallback: Path | None = None) -> dict[str, Any]:
        """解析页面展示用的配置路径状态。

        Args:
            value: 可选环境变量路径。
            fallback: 环境变量为空时使用的默认路径。

        Return:
            路径文本和文件存在性。
        """
        candidate = Path(value).expanduser() if value else fallback
        if candidate is None:
            return {"path": None, "exists": False}
        if not candidate.is_absolute():
            candidate = (repo_root / candidate).resolve()
        else:
            candidate = candidate.resolve()
        return {"path": str(candidate), "exists": candidate.is_file()}

    return {
        "server": {
            "host": settings.host,
            "port": settings.port,
            "log_path": str(settings.log_path.expanduser().resolve()),
            "model_name": settings.model_name,
            "thinking_enabled": settings.thinking_enabled,
            "skip_db_preflight": settings.skip_db_preflight,
            "recursion_limit": settings.recursion_limit,
        },
        "paths": {
            "config": path_state(config_path, repo_root / "config.yaml"),
            "extensions_config": path_state(extensions_path, repo_root / "extensions_config.json"),
            "tablerag_config": path_state(tablerag_path),
            "data_agent_config": path_state(
                None,
                repo_root / ".deer-flow" / "users" / "default" / "agents" / "data-agent" / "config.yaml",
            ),
            "data_agent_soul": path_state(
                None,
                repo_root / ".deer-flow" / "users" / "default" / "agents" / "data-agent" / "SOUL.md",
            ),
        },
        "environment": {name: _redact_env_value(name, os.environ.get(name)) for name in _RUNTIME_ENV_NAMES},
    }


def create_web_app(
    settings: WebSettings | None = None,
    *,
    event_source: EventSource | None = None,
    checkpointer: Any = None,
) -> FastAPI:
    """创建独立的 DataAgent 本地调试应用。

    Args:
        settings: Web 启动配置。
        event_source: 可选事件源，供单元测试注入。
        checkpointer: 可选 LangGraph checkpointer。

    Return:
        FastAPI 应用。
    """
    from langgraph.checkpoint.memory import InMemorySaver

    resolved_settings = settings or WebSettings()
    repo_root = _repo_root()
    _prepare_imports(repo_root)
    runtime = WebRuntime(
        repo_root=repo_root,
        settings=resolved_settings,
        checkpointer=checkpointer or InMemorySaver(),
        run_lock=threading.Lock(),
    )
    source = event_source or _run_events
    app = FastAPI(
        title="DataAgent Local Debug UI",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.data_agent_runtime = runtime

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        """限制请求体并增加基础安全响应头。

        Args:
            request: 当前 HTTP 请求。
            call_next: FastAPI 后续处理函数。

        Return:
            带安全响应头的 HTTP 响应。
        """
        content_length = request.headers.get("content-length")
        if request.method == "POST" and request.url.path == "/api/chat" and content_length:
            try:
                request_size = int(content_length)
            except ValueError:
                request_size = _MAX_REQUEST_BYTES + 1
            if request_size > _MAX_REQUEST_BYTES:
                response = JSONResponse(status_code=413, content={"detail": "请求体不能超过 64 KiB。"})
            else:
                response = await call_next(request)
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        """返回 DataAgent 调试页面。

        Args:
            无。

        Return:
            内嵌 HTML 页面响应。
        """
        return HTMLResponse(_PAGE_HTML)

    @app.get("/api/health")
    def health() -> JSONResponse:
        """返回脱敏环境摘要和当前运行状态。

        Args:
            无。

        Return:
            健康状态 JSON 响应。
        """
        return JSONResponse(
            {
                "ok": True,
                "busy": runtime.run_lock.locked(),
                "runtime": _environment_snapshot(runtime.repo_root, runtime.settings),
            }
        )

    @app.post("/api/chat")
    def chat(payload: ChatRequest, request: Request) -> StreamingResponse:
        """接收浏览器问题并返回 NDJSON 结构化流。

        Args:
            payload: 已校验的 DataAgent 请求。
            request: 当前 HTTP 请求。

        Return:
            NDJSON 流式响应。
        """
        content_type = request.headers.get("content-type", "")
        if not content_type.lower().startswith("application/json"):
            raise HTTPException(status_code=415, detail="Content-Type 必须是 application/json。")
        if not runtime.run_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="已有 DataAgent 任务正在运行，请等待完成后再提交。")

        def stream_events() -> Iterator[str]:
            """编码事件源并在结束时释放单运行锁。

            Args:
                无。

            Yields:
                单行 NDJSON 文本。
            """
            try:
                for event in source(runtime, payload):
                    yield _encode_ndjson(event)
            except Exception as exc:
                yield _encode_ndjson(
                    {
                        "type": "error",
                        "error_type": exc.__class__.__name__,
                        "message": _redact_sensitive_text(str(exc)),
                    }
                )
            finally:
                runtime.run_lock.release()

        return StreamingResponse(
            stream_events(),
            media_type="application/x-ndjson; charset=utf-8",
            headers={"X-Accel-Buffering": "no"},
        )

    return app


def _build_parser() -> argparse.ArgumentParser:
    """构造 Web 调试脚本参数解析器。

    Args:
        无。

    Return:
        argparse.ArgumentParser 实例。
    """
    parser = argparse.ArgumentParser(description="Run the local DataAgent structured debug UI.")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址；默认仅本机 127.0.0.1")
    parser.add_argument("--port", type=int, default=8765, help="监听端口")
    parser.add_argument("--allow-remote", action="store_true", help="显式允许监听非回环地址；不建议用于非受控网络")
    parser.add_argument("--no-open-browser", action="store_true", help="启动后不自动打开浏览器")
    parser.add_argument("--config", default=None, help="DEER_FLOW_CONFIG_PATH")
    parser.add_argument("--extensions-config", default=None, help="DEER_FLOW_EXTENSIONS_CONFIG_PATH")
    parser.add_argument("--model", default=None, help="默认模型名")
    parser.add_argument("--no-thinking", action="store_true", help="默认关闭 thinking")
    parser.add_argument("--skip-db-preflight", action="store_true", help="默认跳过每轮 MySQL SELECT 1 预检")
    parser.add_argument("--recursion-limit", type=int, default=150, help="默认 LangGraph recursion_limit")
    parser.add_argument(
        "--log-path",
        type=Path,
        default=SCRIPT_DIR / "logs",
        help="每轮日志输出目录或 log.txt 模板",
    )
    return parser


def main() -> int:
    """启动 DataAgent 本地调试页面。

    Args:
        无。

    Return:
        进程退出码。
    """
    parser = _build_parser()
    args = parser.parse_args()
    if not 1 <= args.port <= 65_535:
        parser.error("--port 必须在 1 到 65535 之间")
    if not 10 <= args.recursion_limit <= 1_000:
        parser.error("--recursion-limit 必须在 10 到 1000 之间")
    if args.host not in _LOOPBACK_HOSTS and not args.allow_remote:
        parser.error("监听非回环地址必须显式传入 --allow-remote")

    repo_root = _repo_root()
    _prepare_imports(repo_root)
    if args.config:
        os.environ["DEER_FLOW_CONFIG_PATH"] = str(Path(args.config).expanduser().resolve())
    if args.extensions_config:
        os.environ["DEER_FLOW_EXTENSIONS_CONFIG_PATH"] = str(Path(args.extensions_config).expanduser().resolve())

    settings = WebSettings(
        host=args.host,
        port=args.port,
        log_path=args.log_path,
        model_name=args.model,
        thinking_enabled=not args.no_thinking,
        skip_db_preflight=args.skip_db_preflight,
        recursion_limit=args.recursion_limit,
    )
    app = create_web_app(settings)
    browser_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
    browser_address = f"[{browser_host}]" if ":" in browser_host else browser_host
    browser_url = f"http://{browser_address}:{args.port}"
    print(f"DataAgent 调试页面：{browser_url}", flush=True)
    print(f"每轮日志目录：{settings.log_path.expanduser().resolve()}", flush=True)
    if not args.no_open_browser:
        timer = threading.Timer(1.0, lambda: webbrowser.open(browser_url))
        timer.daemon = True
        timer.start()

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info", access_log=False)
    return 0


_PAGE_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>DataAgent 结构化调试台</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #08111f;
      --panel: #101c2f;
      --panel-2: #15243b;
      --border: #28405f;
      --text: #e6edf7;
      --muted: #8fa5bf;
      --accent: #55d6be;
      --accent-2: #7aa2ff;
      --danger: #ff7b8b;
      --warning: #ffc857;
      --success: #5ee38f;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font: 14px/1.5 "Segoe UI", "Microsoft YaHei", sans-serif;
      background:
        radial-gradient(circle at 15% 0%, rgba(85,214,190,.12), transparent 28rem),
        radial-gradient(circle at 100% 10%, rgba(122,162,255,.10), transparent 30rem),
        var(--bg);
      color: var(--text);
    }
    button, input, textarea { font: inherit; }
    button { cursor: pointer; }
    .shell { max-width: 1680px; margin: 0 auto; padding: 18px; }
    header {
      display: flex; align-items: center; justify-content: space-between;
      gap: 16px; margin-bottom: 14px;
    }
    h1 { margin: 0; font-size: 22px; letter-spacing: .3px; }
    .subtitle { color: var(--muted); margin-top: 3px; }
    .status-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .pill {
      display: inline-flex; align-items: center; gap: 6px; padding: 5px 9px;
      border: 1px solid var(--border); border-radius: 999px; background: rgba(16,28,47,.84);
      color: var(--muted); font-size: 12px;
    }
    .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--warning); }
    .dot.ok { background: var(--success); box-shadow: 0 0 12px rgba(94,227,143,.5); }
    .dot.busy { background: var(--warning); box-shadow: 0 0 12px rgba(255,200,87,.5); }
    .layout { display: grid; grid-template-columns: minmax(390px, .9fr) minmax(620px, 1.4fr); gap: 14px; }
    .panel {
      background: rgba(16,28,47,.92); border: 1px solid var(--border);
      border-radius: 14px; overflow: hidden; box-shadow: 0 12px 40px rgba(0,0,0,.22);
    }
    .panel-head {
      display: flex; align-items: center; justify-content: space-between;
      gap: 10px; padding: 12px 14px; border-bottom: 1px solid var(--border);
      background: rgba(21,36,59,.75);
    }
    .panel-title { font-weight: 650; }
    .panel-body { padding: 14px; }
    .conversation { display: grid; grid-template-rows: auto minmax(420px, 1fr) auto; min-height: calc(100vh - 116px); }
    .messages { overflow: auto; padding: 14px; }
    .message { margin: 0 0 14px; display: flex; }
    .message.user { justify-content: flex-end; }
    .bubble {
      max-width: 88%; border-radius: 13px; padding: 10px 12px; white-space: pre-wrap;
      overflow-wrap: anywhere; border: 1px solid var(--border); background: var(--panel-2);
    }
    .user .bubble { background: #214c68; border-color: #337594; }
    .assistant .bubble { background: #14243a; }
    .error .bubble { border-color: rgba(255,123,139,.55); background: rgba(108,31,44,.42); }
    .composer { border-top: 1px solid var(--border); padding: 12px; }
    textarea, input {
      width: 100%; color: var(--text); background: #0b1728; border: 1px solid var(--border);
      border-radius: 9px; padding: 9px 10px; outline: none;
    }
    textarea:focus, input:focus { border-color: var(--accent-2); box-shadow: 0 0 0 3px rgba(122,162,255,.12); }
    #question { min-height: 86px; resize: vertical; }
    .actions { display: flex; gap: 8px; margin-top: 9px; justify-content: flex-end; }
    .btn {
      border: 1px solid var(--border); background: #192b45; color: var(--text);
      border-radius: 8px; padding: 8px 13px;
    }
    .btn.primary { background: #176b62; border-color: #258f83; }
    .btn.danger { background: #6d2935; border-color: #a04354; }
    .btn:disabled { opacity: .45; cursor: not-allowed; }
    details.settings { border-top: 1px solid var(--border); }
    details.settings summary { cursor: pointer; padding: 10px 14px; color: var(--muted); }
    .settings-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 9px 12px; padding: 0 14px 14px; }
    .field label { display: block; color: var(--muted); font-size: 12px; margin-bottom: 4px; }
    .field.full { grid-column: 1 / -1; }
    .checks { display: flex; gap: 15px; align-items: center; }
    .checks label { display: inline-flex; align-items: center; gap: 6px; color: var(--text); }
    .checks input { width: auto; }
    .trace { min-height: calc(100vh - 116px); }
    .stages { display: flex; flex-wrap: wrap; gap: 7px; padding: 12px 14px; border-bottom: 1px solid var(--border); }
    .stage {
      padding: 5px 8px; border: 1px solid var(--border); border-radius: 7px; color: var(--muted);
      background: #0c1829; font-size: 12px;
    }
    .stage.active { color: #071713; background: var(--accent); border-color: var(--accent); font-weight: 700; }
    .stage.done { color: var(--success); border-color: rgba(94,227,143,.4); }
    .grid { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 12px; padding: 12px; }
    .card { border: 1px solid var(--border); background: rgba(11,23,40,.8); border-radius: 11px; overflow: hidden; min-height: 140px; }
    .card.wide { grid-column: 1 / -1; }
    .card-head { padding: 9px 11px; border-bottom: 1px solid var(--border); color: var(--accent-2); font-weight: 650; }
    .card-body { padding: 10px 11px; overflow: auto; max-height: 340px; }
    .empty { color: var(--muted); }
    .tags { display: flex; flex-wrap: wrap; gap: 6px; }
    .tag { padding: 4px 7px; border-radius: 6px; background: #1a304d; border: 1px solid #2b4d75; }
    pre { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font: 12px/1.55 Consolas, monospace; color: #c9d7e8; }
    table { width: 100%; border-collapse: collapse; font-size: 12px; }
    th, td { text-align: left; padding: 7px 8px; border-bottom: 1px solid #233a58; vertical-align: top; max-width: 340px; overflow-wrap: anywhere; }
    th { position: sticky; top: 0; background: #13243b; color: var(--accent); }
    .timeline { display: grid; gap: 7px; }
    .event { border-left: 3px solid var(--accent-2); padding: 6px 8px; background: #0c1829; border-radius: 0 7px 7px 0; }
    .event .meta { color: var(--muted); font-size: 11px; margin-bottom: 2px; }
    .event.error { border-color: var(--danger); }
    .kpi { font-size: 34px; font-weight: 750; color: var(--accent); padding: 12px 0 4px; }
    canvas { width: 100%; height: 220px; background: #0b1728; border-radius: 8px; }
    .run-meta { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
    @media (max-width: 1050px) {
      .layout { grid-template-columns: 1fr; }
      .conversation, .trace { min-height: auto; }
      .messages { min-height: 420px; max-height: 65vh; }
    }
    @media (max-width: 650px) {
      .shell { padding: 10px; }
      header { align-items: flex-start; flex-direction: column; }
      .grid, .settings-grid { grid-template-columns: 1fr; }
      .card.wide, .field.full { grid-column: auto; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <header>
      <div>
        <h1>DataAgent 结构化调试台</h1>
        <div class="subtitle">对话、执行阶段、检索、SQL 与结果数据在同一页面联动展示</div>
      </div>
      <div class="status-row">
        <span class="pill"><span id="health-dot" class="dot"></span><span id="health-text">检查环境中</span></span>
        <span class="pill">会话 <span id="thread-label">-</span></span>
        <button id="new-session" class="btn" type="button">新会话</button>
      </div>
    </header>

    <section class="layout">
      <article class="panel conversation">
        <div>
          <div class="panel-head">
            <span class="panel-title">对话</span>
            <span id="run-meta" class="run-meta">尚未运行</span>
          </div>
          <details class="settings">
            <summary>运行设置与黑话映射</summary>
            <div class="settings-grid">
              <div class="field">
                <label for="model-name">模型名（留空使用服务默认）</label>
                <input id="model-name" autocomplete="off">
              </div>
              <div class="field">
                <label for="recursion-limit">Recursion limit</label>
                <input id="recursion-limit" type="number" min="10" max="1000" value="150">
              </div>
              <div class="field full">
                <label for="aliases">黑话映射，每行：黑话=标准值</label>
                <textarea id="aliases" rows="3" placeholder="黑金=高价值会员"></textarea>
              </div>
              <div class="field full checks">
                <label><input id="thinking-enabled" type="checkbox" checked>启用 Thinking</label>
                <label><input id="skip-preflight" type="checkbox">跳过 MySQL 预检</label>
              </div>
            </div>
          </details>
        </div>
        <div id="messages" class="messages" aria-live="polite"></div>
        <div class="composer">
          <textarea id="question" placeholder="例如：查询原因不明病例数，并生成 KPI 图表"></textarea>
          <div class="actions">
            <button id="stop" class="btn danger" type="button" disabled>停止接收</button>
            <button id="send" class="btn primary" type="button">发送</button>
          </div>
        </div>
      </article>

      <article class="panel trace">
        <div class="panel-head">
          <span class="panel-title">数据执行轨迹</span>
          <span id="log-file" class="run-meta">日志：-</span>
        </div>
        <div id="stages" class="stages"></div>
        <div class="grid">
          <section class="card" data-panel="query-context">
            <div class="card-head">QueryContext / 实体标签</div>
            <div id="query-context" class="card-body"><span class="empty">等待问题分析</span></div>
          </section>
          <section class="card" data-panel="retrieval">
            <div class="card-head">TableRAG 检索</div>
            <div id="retrieval" class="card-body"><span class="empty">等待表结构检索</span></div>
          </section>
          <section class="card wide" data-panel="sql">
            <div class="card-head">生成 SQL / 校验结果</div>
            <div id="sql" class="card-body"><span class="empty">等待 SQL</span></div>
          </section>
          <section class="card wide" data-panel="sql-execution">
            <div class="card-head">SQL 执行结果</div>
            <div id="execution" class="card-body"><span class="empty">等待数据库结果</span></div>
          </section>
          <section class="card" data-panel="chart">
            <div class="card-head">ChartSpec 预览</div>
            <div id="chart" class="card-body"><span class="empty">等待图表数据</span></div>
          </section>
          <section class="card" data-panel="tools">
            <div class="card-head">工具调用</div>
            <div id="tools" class="card-body"><span class="empty">等待工具调用</span></div>
          </section>
          <section class="card wide" data-panel="timeline">
            <div class="card-head">原始事件时间线</div>
            <div id="timeline" class="card-body timeline"><span class="empty">等待运行事件</span></div>
          </section>
        </div>
      </article>
    </section>
  </main>

  <script>
    const stageOrder = [
      "query_context", "retrieval_completed", "sql_validated",
      "sql_executed", "chart_ready"
    ];
    const stageLabels = {
      query_context: "问题分析",
      retrieval_completed: "TableRAG",
      sql_validation_failed: "SQL校验失败",
      sql_validated: "SQL已校验",
      sql_execution_failed: "SQL执行失败",
      sql_executed: "SQL已执行",
      chart_failed: "图表失败",
      chart_ready: "图表就绪"
    };
    const state = {
      busy: false,
      controller: null,
      threadId: sessionStorage.getItem("dataAgentThreadId") || newThreadId(),
      assistantNode: null,
      generatedSql: "",
      validation: null,
      eventCount: 0,
      defaultsLoaded: false
    };

    function newThreadId() {
      return "data-agent-web-" + crypto.randomUUID();
    }
    function el(id) { return document.getElementById(id); }
    function clearNode(node) { while (node.firstChild) node.removeChild(node.firstChild); }
    function textNode(tag, text, className) {
      const node = document.createElement(tag);
      if (className) node.className = className;
      node.textContent = text;
      return node;
    }
    function setEmpty(id, text) {
      const node = el(id); clearNode(node); node.appendChild(textNode("span", text, "empty"));
    }
    function appendMessage(role, text) {
      const row = document.createElement("div");
      row.className = "message " + role;
      const bubble = textNode("div", text, "bubble");
      row.appendChild(bubble);
      el("messages").appendChild(row);
      el("messages").scrollTop = el("messages").scrollHeight;
      return bubble;
    }
    function parseAliases() {
      const aliases = {};
      for (const rawLine of el("aliases").value.split(/\r?\n/)) {
        const line = rawLine.trim();
        if (!line) continue;
        const index = line.indexOf("=");
        if (index <= 0 || index === line.length - 1) throw new Error("黑话映射格式错误：" + line);
        aliases[line.slice(0, index).trim()] = line.slice(index + 1).trim();
      }
      return aliases;
    }
    function resetTrace() {
      state.generatedSql = "";
      state.validation = null;
      state.eventCount = 0;
      for (const [id, text] of [
        ["query-context", "等待问题分析"], ["retrieval", "等待表结构检索"],
        ["sql", "等待 SQL"], ["execution", "等待数据库结果"],
        ["chart", "等待图表数据"], ["tools", "等待工具调用"],
        ["timeline", "等待运行事件"]
      ]) setEmpty(id, text);
      renderStages(null);
      el("log-file").textContent = "日志：-";
      el("run-meta").textContent = "运行中";
    }
    function renderStages(current) {
      const node = el("stages"); clearNode(node);
      const currentIndex = stageOrder.indexOf(current);
      for (const stage of stageOrder) {
        const item = textNode("span", stageLabels[stage] || stage, "stage");
        const index = stageOrder.indexOf(stage);
        if (stage === current) item.classList.add("active");
        else if (currentIndex >= 0 && index < currentIndex) item.classList.add("done");
        node.appendChild(item);
      }
      if (current && !stageOrder.includes(current)) {
        const item = textNode("span", stageLabels[current] || current, "stage active");
        node.appendChild(item);
      }
    }
    function renderQueryContext(payload) {
      const node = el("query-context"); clearNode(node);
      node.appendChild(textNode("div", "意图：" + (payload.intent || "-")));
      node.appendChild(textNode("div", "归一化：" + (payload.normalized_query || payload.original_query || "-")));
      const tags = document.createElement("div"); tags.className = "tags"; tags.style.marginTop = "8px";
      const entities = [...(payload.aliases || []), ...(payload.entities || []), ...(payload.labels || [])];
      for (const item of entities) {
        const label = item.label ? item.label + "：" : "";
        tags.appendChild(textNode("span", label + (item.normalized || item.value || ""), "tag"));
      }
      if (!entities.length) tags.appendChild(textNode("span", "未识别实体", "empty"));
      node.appendChild(tags);
    }
    function renderRetrieval(payload) {
      const node = el("retrieval"); clearNode(node);
      node.appendChild(textNode("div", "工具：" + (payload.tool_name || "-")));
      node.appendChild(textNode("div", "查询：" + (payload.query || "-")));
      const pre = textNode("pre", payload.result_preview || JSON.stringify(payload, null, 2));
      pre.style.marginTop = "8px"; node.appendChild(pre);
    }
    function renderSql() {
      const node = el("sql"); clearNode(node);
      if (state.generatedSql) {
        node.appendChild(textNode("div", "生成 SQL", "run-meta"));
        node.appendChild(textNode("pre", state.generatedSql));
      }
      if (state.validation) {
        const title = "校验：" + (state.validation.valid ? "通过" : "失败");
        const titleNode = textNode("div", title, "run-meta"); titleNode.style.marginTop = "10px";
        node.appendChild(titleNode);
        node.appendChild(textNode("pre", JSON.stringify(state.validation, null, 2)));
      }
      if (!state.generatedSql && !state.validation) node.appendChild(textNode("span", "等待 SQL", "empty"));
    }
    function renderTable(container, execution) {
      const columns = execution.columns || [];
      const rows = execution.rows || [];
      if (!columns.length) {
        container.appendChild(textNode("pre", JSON.stringify(execution, null, 2)));
        return;
      }
      const meta = `rows=${execution.row_count ?? rows.length} · elapsed=${execution.elapsed_ms ?? "-"}ms · truncated=${Boolean(execution.truncated)}`;
      container.appendChild(textNode("div", meta, "run-meta"));
      const table = document.createElement("table");
      const head = document.createElement("thead");
      const headRow = document.createElement("tr");
      for (const column of columns) headRow.appendChild(textNode("th", String(column)));
      head.appendChild(headRow); table.appendChild(head);
      const body = document.createElement("tbody");
      for (const row of rows) {
        const tr = document.createElement("tr");
        for (const column of columns) {
          const value = row && Object.hasOwn(row, column) ? row[column] : "";
          tr.appendChild(textNode("td", typeof value === "object" ? JSON.stringify(value) : String(value ?? "")));
        }
        body.appendChild(tr);
      }
      table.appendChild(body); container.appendChild(table);
    }
    function renderExecution(payload, lastSuccessful) {
      const node = el("execution"); clearNode(node);
      if (payload && payload.ok) renderTable(node, payload);
      else {
        node.appendChild(textNode("pre", JSON.stringify(payload || {}, null, 2)));
        if (lastSuccessful && lastSuccessful.ok) {
          const title = textNode("div", "最后一次成功结果", "run-meta"); title.style.marginTop = "10px";
          node.appendChild(title); renderTable(node, lastSuccessful);
        }
      }
    }
    function numeric(value) {
      const number = Number(value);
      return Number.isFinite(number) ? number : null;
    }
    function renderChart(spec) {
      const node = el("chart"); clearNode(node);
      const type = spec.type || "table";
      const rows = spec.data || [];
      const yFields = Array.isArray(spec.y) ? spec.y : [];
      node.appendChild(textNode("div", `${spec.title || "图表"} · ${type}`, "run-meta"));
      if (type === "kpi" && rows.length && yFields.length) {
        node.appendChild(textNode("div", String(rows[0][yFields[0]] ?? "-"), "kpi"));
        return;
      }
      if (!["bar", "line"].includes(type) || !rows.length || !yFields.length) {
        node.appendChild(textNode("pre", JSON.stringify(spec, null, 2)));
        return;
      }
      const canvas = document.createElement("canvas");
      canvas.width = 700; canvas.height = 260; node.appendChild(canvas);
      const ctx = canvas.getContext("2d");
      const values = rows.map(row => numeric(row[yFields[0]]) ?? 0);
      const max = Math.max(...values.map(Math.abs), 1);
      const pad = 38, width = canvas.width - pad * 2, height = canvas.height - pad * 2;
      ctx.strokeStyle = "#36506f"; ctx.beginPath(); ctx.moveTo(pad, pad); ctx.lineTo(pad, pad + height); ctx.lineTo(pad + width, pad + height); ctx.stroke();
      ctx.fillStyle = "#8fa5bf"; ctx.font = "11px Segoe UI";
      if (type === "bar") {
        const slot = width / Math.max(values.length, 1);
        values.forEach((value, index) => {
          const barHeight = Math.abs(value) / max * (height - 16);
          ctx.fillStyle = "#55d6be";
          ctx.fillRect(pad + index * slot + slot * .18, pad + height - barHeight, slot * .64, barHeight);
        });
      } else {
        ctx.strokeStyle = "#7aa2ff"; ctx.lineWidth = 3; ctx.beginPath();
        values.forEach((value, index) => {
          const x = pad + (values.length === 1 ? width / 2 : index * width / (values.length - 1));
          const y = pad + height - Math.abs(value) / max * (height - 16);
          if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        });
        ctx.stroke();
      }
    }
    function appendTool(event) {
      const node = el("tools");
      if (node.querySelector(".empty")) clearNode(node);
      const item = document.createElement("div"); item.className = "event";
      item.appendChild(textNode("div", event.type === "tool_call" ? "调用 " + event.name : "结果 " + event.name, "meta"));
      if (event.content) item.appendChild(textNode("pre", event.content.slice(0, 4000)));
      node.appendChild(item);
    }
    function appendTimeline(event) {
      const node = el("timeline");
      if (node.querySelector(".empty")) clearNode(node);
      state.eventCount += 1;
      const item = document.createElement("div");
      item.className = "event" + (event.type === "error" ? " error" : "");
      item.appendChild(textNode("div", `${new Date().toLocaleTimeString()} · ${event.type}`, "meta"));
      const preview = JSON.stringify(event, null, 2);
      item.appendChild(textNode("pre", preview.length > 3500 ? preview.slice(0, 3500) + "\n..." : preview));
      node.appendChild(item);
      while (node.children.length > 500) node.removeChild(node.firstChild);
    }
    function handleEvent(event) {
      if (event.type !== "ai_delta") appendTimeline(event);
      switch (event.type) {
        case "run_started":
          el("log-file").textContent = "日志：" + event.log_file;
          el("run-meta").textContent = "run " + event.run_id;
          break;
        case "ai_delta":
          if (!state.assistantNode) state.assistantNode = appendMessage("assistant", "");
          state.assistantNode.textContent += event.text || "";
          el("messages").scrollTop = el("messages").scrollHeight;
          break;
        case "query_context": renderQueryContext(event.payload || {}); break;
        case "stage": renderStages(event.stage); break;
        case "retrieval": renderRetrieval(event.payload || {}); break;
        case "generated_sql": state.generatedSql = String(event.payload || ""); renderSql(); break;
        case "sql_validation": state.validation = event.payload || {}; renderSql(); break;
        case "sql_execution": renderExecution(event.payload || {}, event.last_successful); break;
        case "chart_spec": renderChart(event.payload || {}); break;
        case "tool_call":
        case "tool_result": appendTool(event); break;
        case "preflight":
          el("run-meta").textContent = "数据库预检：" + event.status;
          break;
        case "run_completed":
          el("run-meta").textContent = `完成 · ${event.elapsed_ms}ms`;
          break;
        case "error":
          appendMessage("error", `${event.error_type || "Error"}: ${event.message || "运行失败"}`);
          el("run-meta").textContent = "运行失败";
          break;
      }
    }
    async function sendQuestion() {
      if (state.busy) return;
      const question = el("question").value.trim();
      if (!question) return;
      let aliases;
      try { aliases = parseAliases(); }
      catch (error) { appendMessage("error", error.message); return; }
      const recursion = Number(el("recursion-limit").value || "150");
      if (recursion < 10 || recursion > 1000) {
        appendMessage("error", "Recursion limit 必须在 10 到 1000 之间"); return;
      }
      resetTrace();
      appendMessage("user", question);
      state.assistantNode = null;
      state.busy = true;
      el("send").disabled = true; el("stop").disabled = false; el("question").disabled = true;
      const controller = new AbortController(); state.controller = controller;
      const payload = {
        question,
        thread_id: state.threadId,
        aliases,
        model_name: el("model-name").value.trim() || null,
        thinking_enabled: el("thinking-enabled").checked,
        skip_db_preflight: el("skip-preflight").checked,
        recursion_limit: recursion
      };
      try {
        const response = await fetch("/api/chat", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload),
          signal: controller.signal
        });
        if (!response.ok) throw new Error((await response.text()) || `HTTP ${response.status}`);
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
          const {value, done} = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, {stream: true});
          const lines = buffer.split("\n"); buffer = lines.pop();
          for (const line of lines) if (line.trim()) handleEvent(JSON.parse(line));
        }
        buffer += decoder.decode();
        if (buffer.trim()) handleEvent(JSON.parse(buffer));
      } catch (error) {
        if (error.name !== "AbortError") appendMessage("error", error.message);
        else el("run-meta").textContent = "已请求停止";
      } finally {
        state.busy = false; state.controller = null;
        el("send").disabled = false; el("stop").disabled = true; el("question").disabled = false;
        el("question").value = ""; el("question").focus();
        refreshHealth();
      }
    }
    async function refreshHealth() {
      try {
        const response = await fetch("/api/health", {cache: "no-store"});
        const data = await response.json();
        el("health-dot").className = "dot " + (data.busy ? "busy" : "ok");
        el("health-text").textContent = data.busy ? "DataAgent 运行中" : "服务就绪";
        const defaults = data.runtime.server || {};
        if (!state.defaultsLoaded) {
          if (defaults.model_name) el("model-name").value = defaults.model_name;
          el("thinking-enabled").checked = defaults.thinking_enabled !== false;
          el("skip-preflight").checked = defaults.skip_db_preflight === true;
          el("recursion-limit").value = defaults.recursion_limit || 150;
          state.defaultsLoaded = true;
        }
      } catch {
        el("health-dot").className = "dot";
        el("health-text").textContent = "服务不可用";
      }
    }
    el("send").addEventListener("click", sendQuestion);
    el("stop").addEventListener("click", () => state.controller?.abort());
    el("question").addEventListener("keydown", event => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) sendQuestion();
    });
    el("new-session").addEventListener("click", () => {
      if (state.busy) return;
      state.threadId = newThreadId();
      sessionStorage.setItem("dataAgentThreadId", state.threadId);
      clearNode(el("messages")); resetTrace();
      el("run-meta").textContent = "新会话";
      el("thread-label").textContent = state.threadId.slice(-12);
    });
    sessionStorage.setItem("dataAgentThreadId", state.threadId);
    el("thread-label").textContent = state.threadId.slice(-12);
    renderStages(null);
    refreshHealth();
    setInterval(refreshHealth, 5000);
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
