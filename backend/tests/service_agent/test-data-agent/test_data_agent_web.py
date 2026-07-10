from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
from langchain_core.messages import AIMessageChunk, ToolMessage

TEST_DIR = Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

import run_data_agent_web as web  # noqa: E402


def _fake_events(runtime: web.WebRuntime, request: web.ChatRequest):
    """生成不访问模型和数据库的页面测试事件。

    Args:
        runtime: Web 运行时。
        request: 浏览器请求。

    Yields:
        固定结构化事件。
    """
    yield {
        "type": "run_started",
        "run_id": "run-test",
        "thread_id": request.thread_id,
        "question": request.question,
        "log_file": str(runtime.settings.log_path / "log_test.txt"),
    }
    yield {"type": "stage", "stage": "query_context"}
    yield {"type": "ai_delta", "text": "测试回答"}
    yield {"type": "run_completed", "run_id": "run-test", "elapsed_ms": 5}


@pytest.mark.asyncio
async def test_web_page_exposes_chat_and_structured_trace_panels(tmp_path, monkeypatch) -> None:
    """校验页面包含对话区和主要结构化数据面板。

    Args:
        tmp_path: pytest 临时目录 fixture。
        monkeypatch: pytest monkeypatch fixture。

    Return:
        None。
    """
    monkeypatch.setenv("DATA_AGENT_MYSQL_DSN", "mysql+pymysql://reporter:test%40password@db.internal/analytics")
    app = web.create_web_app(web.WebSettings(log_path=tmp_path), event_source=_fake_events)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/")
        health = await client.get("/api/health")

    assert response.status_code == 200
    assert "DataAgent 结构化调试台" in response.text
    assert 'data-panel="query-context"' in response.text
    assert 'data-panel="sql-execution"' in response.text
    assert 'data-panel="timeline"' in response.text
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert health.status_code == 200
    assert health.json()["runtime"]["environment"]["DATA_AGENT_MYSQL_DSN"] == "mysql+pymysql://reporter:***@db.internal/analytics"
    assert "test%40password" not in health.text


@pytest.mark.asyncio
async def test_web_chat_endpoint_streams_ndjson_events(tmp_path) -> None:
    """校验页面接口按 NDJSON 返回结构化轨迹。

    Args:
        tmp_path: pytest 临时目录 fixture。

    Return:
        None。
    """
    app = web.create_web_app(web.WebSettings(log_path=tmp_path), event_source=_fake_events)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/chat",
            json={
                "question": "查询测试指标",
                "thread_id": "thread-1",
                "aliases": {"黑金": "高价值会员"},
            },
        )

    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert [event["type"] for event in events] == ["run_started", "stage", "ai_delta", "run_completed"]
    assert events[0]["thread_id"] == "thread-1"
    assert events[2]["text"] == "测试回答"


@pytest.mark.asyncio
async def test_web_chat_validates_request_and_single_run_limit(tmp_path) -> None:
    """校验页面拒绝非法请求和并发运行。

    Args:
        tmp_path: pytest 临时目录 fixture。

    Return:
        None。
    """
    app = web.create_web_app(web.WebSettings(log_path=tmp_path), event_source=_fake_events)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        empty_question = await client.post("/api/chat", json={"question": "   "})
        invalid_thread = await client.post("/api/chat", json={"question": "测试", "thread_id": "../bad"})
        oversized = await client.post("/api/chat", content=b'{"question":"' + (b"x" * (65 * 1024)) + b'"}', headers={"Content-Type": "application/json"})
        runtime = app.state.data_agent_runtime
        assert runtime.run_lock.acquire(blocking=False)
        try:
            busy = await client.post("/api/chat", json={"question": "测试"})
        finally:
            runtime.run_lock.release()

    assert empty_question.status_code == 422
    assert invalid_thread.status_code == 422
    assert oversized.status_code == 413
    assert busy.status_code == 409


@pytest.mark.asyncio
async def test_web_stream_wrapper_redacts_unhandled_errors(tmp_path, monkeypatch) -> None:
    """校验页面流包装器不会把提供商密钥写回浏览器。

    Args:
        tmp_path: pytest 临时目录 fixture。
        monkeypatch: pytest monkeypatch fixture。

    Return:
        None。
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-web-test-secret")

    def failing_events(runtime: web.WebRuntime, request: web.ChatRequest):
        """模拟包含提供商密钥的未处理异常。

        Args:
            runtime: Web 运行时。
            request: 浏览器请求。

        Yields:
            不产出事件，直接抛出异常。
        """
        raise RuntimeError("provider failed: sk-web-test-secret")
        yield

    app = web.create_web_app(web.WebSettings(log_path=tmp_path), event_source=failing_events)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/chat", json={"question": "测试"})

    event = json.loads(response.text)
    assert response.status_code == 200
    assert event["type"] == "error"
    assert event["message"] == "provider failed: ***"
    assert "sk-web-test-secret" not in response.text


def test_web_values_events_expose_data_trajectory_and_deduplicate() -> None:
    """校验 values 状态会转换为完整数据轨迹并去重。

    Args:
        无。

    Return:
        None。
    """
    observed: dict[str, object] = {}
    state = {
        "data_agent_stage": "sql_executed",
        "data_query_context": {"intent": "metric_query", "entities": []},
        "data_retrieval_context": {"ok": True, "tool_name": "tablerag_retrieve", "query": "病例数"},
        "data_generated_sql": "SELECT count(*) FROM report",
        "data_sql_validation": {"valid": True, "executable_sql": "SELECT count(*) FROM report LIMIT 100"},
        "data_sql_execution": {
            "ok": True,
            "columns": ["count"],
            "rows": [{"count": 3}],
            "row_count": 1,
        },
        "data_last_successful_sql_execution": {"ok": True, "rows": [{"count": 3}]},
        "data_chart_spec": {"type": "kpi", "y": ["count"], "data": [{"count": 3}]},
    }

    events = web._values_events(state, observed)

    assert [event["type"] for event in events] == [
        "stage",
        "query_context",
        "retrieval",
        "generated_sql",
        "sql_validation",
        "sql_execution",
        "chart_spec",
    ]
    assert events[5]["last_successful"]["rows"] == [{"count": 3}]
    assert web._values_events(state, observed) == []


def test_web_custom_query_context_deduplicates_against_values_event() -> None:
    """校验 custom 与 values 中的同一 QueryContext 不会重复展示。

    Args:
        无。

    Return:
        None。
    """
    observed: dict[str, object] = {}
    context = {"intent": "metric_query", "normalized_query": "病例数"}

    custom_events = web._custom_events(
        {"type": "data_query_context", "context": context},
        observed,
    )
    values_events = web._values_events(
        {"data_query_context": context},
        observed,
    )

    assert custom_events == [{"type": "query_context", "payload": context}]
    assert values_events == []


def test_web_message_events_expose_ai_tool_calls_and_results() -> None:
    """校验 messages 状态会转换为 AI、工具调用和工具结果事件。

    Args:
        无。

    Return:
        None。
    """
    observed: dict[str, object] = {}
    ai_chunk = AIMessageChunk(
        content="正在检索",
        tool_call_chunks=[
            {
                "name": "tablerag_retrieve",
                "args": "",
                "id": "call-1",
                "index": 0,
            }
        ],
    )
    tool_message = ToolMessage(
        content="schema result",
        name="tablerag_retrieve",
        tool_call_id="call-1",
    )

    ai_events = web._message_events(ai_chunk, observed)
    duplicate_events = web._message_events(ai_chunk, observed)
    tool_events = web._message_events(tool_message, observed)

    assert ai_events == [
        {"type": "ai_delta", "text": "正在检索"},
        {
            "type": "tool_call",
            "name": "tablerag_retrieve",
            "tool_call_id": "call-1",
        },
    ]
    assert duplicate_events == [{"type": "ai_delta", "text": "正在检索"}]
    assert tool_events == [
        {
            "type": "tool_result",
            "name": "tablerag_retrieve",
            "tool_call_id": "call-1",
            "content": "schema result",
            "truncated": False,
        }
    ]
