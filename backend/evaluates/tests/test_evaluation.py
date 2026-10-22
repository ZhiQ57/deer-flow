"""使用合成事件检查独立对话评估协议，不连接模型或业务数据。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deerflow_evidence import SseDecoder, TraceBuilder, audit_prediction, make_prediction, normalize_name, recorded_chunks, replay_trace
from eval_common import BUDGETS, NATIVE_BUDGETS, NATIVE_MODE, digest, write_json
from prepare_dataset import build_prompt
from run_deerflow import Recorder, execution_timing, observed_budget
from schema_metrics import retrieval_metrics
from score_results import retrieval_rounds, score_one


def call(tool: str, args: dict, payload: dict) -> dict:
    """构造明确标记为合成的工具调用。Args: tool/args/payload。Returns: 调用。"""
    return {"tool": tool, "arguments": args, "result": {"structuredContent": payload}, "scope": "", "is_error": False}


def synthetic() -> tuple[dict, dict]:
    """返回虚构 alpha 表样本。Args: 无。Returns: 样本及预测。"""
    sample = {"target_tables": ["alpha"], "target_columns": [{"table": "alpha", "column": "id"}, {"table": "alpha", "column": "amount"}]}
    sql = "SELECT SUM(a.amount) AS total FROM alpha AS a"
    calls = [
        call("tree_folder", {}, {"status": "success", "workspace_content": "/sql/mysql/demo", "file_summaries": "synthetic"}),
        call("vfs_grep_file", {"querys": ["synthetic"]}, {"status": "success", "matches": [{"path": "/sql/mysql/demo/table/alpha/desc.md", "text": "synthetic"}]}),
        call("vfs_cat_read", {"file_path": "/sql/mysql/demo/table/alpha/desc.md"}, {"status": "success", "result_content": "# 合成表\n## 字段\n| 字段 | 类型 |\n| --- | --- |\n| id | int |\n| amount | decimal |\n"}),
        call("sql_execute", {"sql": sql, "parameters": {}}, {"ok": True, "executed": True, "status": "empty", "rows": [], "error": None}),
    ]
    record = {
        "case_id": "synthetic-01",
        "status": "completed",
        "sql": sql,
        "parameters": {},
        "notes": "synthetic",
        "tool_calls": calls,
        "all_tool_calls": calls,
        "elapsed_seconds": 20,
        "run_status": "success",
        "protocol_issues": [],
        "stop_reason": None,
        "capture": {"source": "deerflow_gateway", "complete": True, "issues": []},
    }
    return sample, record


def test_budget_is_user_approved_double() -> None:
    assert BUDGETS == {"timeout_seconds": 1200, "max_tool_calls": 24, "max_sql_executions": 4}


@pytest.mark.parametrize("name", ["vfssql_vfs_tree_folder", "mcp__vfssql__vfs_tree_folder", "vfs_tree_folder"])
def test_tool_prefixes(name: str) -> None:
    assert normalize_name(name) == "tree_folder"


def test_primary_and_strict_sql_are_separate() -> None:
    sample, record = synthetic()
    score = score_one(sample, record, BUDGETS, audit_ok=True)
    assert score["primary_success"] and score["execution_ok"]
    assert not score["strict_sql_success"]
    assert score["first_complete_search_round"] == 1
    assert score["first_complete_retrieval_call"] == 2
    assert score["first_complete_mcp_call"] == 3


def test_missing_column_is_not_zero_round_success() -> None:
    sample, record = synthetic()
    sample["target_columns"].append({"table": "alpha", "column": "absent"})
    result = retrieval_rounds(sample, record["tool_calls"], {})
    assert result["first_complete_search_round"] is None
    assert not score_one(sample, record, BUDGETS, audit_ok=True)["primary_success"]


def test_same_name_column_does_not_cross_table() -> None:
    sample, record = synthetic()
    sample["target_columns"].append({"table": "beta", "column": "id"})
    assert not retrieval_metrics(sample, record["tool_calls"], {})["structure_complete"]


@pytest.mark.parametrize("change", ["audit", "timeout", "run_error", "capture", "parameters"])
def test_invalid_evidence_does_not_pass(change: str) -> None:
    sample, record = synthetic()
    if change == "timeout":
        record["elapsed_seconds"] = 1201
    elif change == "run_error":
        record["run_status"] = "error"
    elif change == "capture":
        record["capture"]["complete"] = False
    elif change == "parameters":
        record["parameters"] = {"extra": 1}
    assert not score_one(sample, record, BUDGETS, audit_ok=change != "audit")["primary_success"]


def test_sse_arbitrary_byte_boundaries() -> None:
    raw = ("event: values|child\ndata: " + json.dumps({"messages": [{"type": "tool", "tool_call_id": "native", "name": "vfssql_vfs_cat_read", "content": "合成字段"}]}, ensure_ascii=False) + "\n\nevent: end\ndata:\n\n").encode()
    decoder, frames = SseDecoder(), []
    for byte in raw:
        frames.extend(decoder.accept(bytes([byte])))
    assert frames[0][0] == "values|child"
    assert frames[0][1]["messages"][0]["content"] == "合成字段"
    assert frames[-1] == ("end", None)


def test_scope_and_tool_ids_are_preserved() -> None:
    builder = TraceBuilder()
    receipt = {"elapsed_seconds": 1, "received_at_utc": "2026-10-22T00:00:00+00:00"}
    for scope in ["", "child"]:
        builder.add_message({"type": "ai", "id": "ai-" + scope, "content": "", "tool_calls": [{"id": "same-provider-id", "name": "vfssql_vfs_tree_folder", "args": {}}]}, scope, receipt)
        builder.add_message({"type": "tool", "tool_call_id": "same-provider-id", "name": "vfssql_vfs_tree_folder", "content": '{"status":"success"}'}, scope, receipt)
    mcp, _, issues = builder.calls()
    assert len(mcp) == 2 and not issues
    assert {c["scope"] for c in mcp} == {"", "child"}


def test_summary_cannot_fabricate_native_child_call() -> None:
    builder = TraceBuilder()
    builder.accept_event({"event_type": "subagent.step", "seq": 1, "content": {"task_id": "synthetic", "message_index": 0, "kind": "tool", "tool_name": "vfssql_vfs_cat_read", "text": "truncated", "truncated": True}}, {})
    calls, _, issues = builder.calls()
    assert calls == [] and "subagent_native_tool_messages_missing" in issues


def test_budget_counts_failed_requests_and_children() -> None:
    events = [
        {"event_type": "llm.ai.response", "content": {"tool_calls": [{"id": "a", "name": "vfssql_sql_execute"}]}},
        {"event_type": "subagent.step", "content": {"kind": "ai", "tool_calls": [{"name": "vfssql_vfs_grep_file"}, {"name": "vfssql_sql_execute"}]}},
    ]
    assert observed_budget(events) == (3, 2, False)


def test_receipts_detect_changed_raw_bytes(tmp_path: Path) -> None:
    recorder = Recorder(tmp_path, "2026-10-22T00:00:00+00:00")
    recorder.save(b"synthetic", stream=True)
    assert recorded_chunks(tmp_path, "stream")[0][0] == b"synthetic"
    (tmp_path / "stream.sse").write_bytes(b"altered")
    with pytest.raises(ValueError):
        recorded_chunks(tmp_path, "stream")


def test_artifact_only_recovers_explicit_current_thread_reference(tmp_path: Path) -> None:
    recorder = Recorder(tmp_path, "2026-10-22T00:00:00+00:00")
    virtual = "/mnt/user-data/outputs/tool-results/synthetic.json"
    message = {"type": "tool", "tool_call_id": "synthetic", "name": "vfssql_vfs_cat_read", "content": "preview " + virtual}
    raw = ("event: values\ndata: " + json.dumps({"messages": [message]}) + "\n\nevent: end\ndata: null\n\n").encode()
    recorder.save(raw, stream=True)
    payload = {"status": "success", "result_content": "synthetic full content"}
    write_json(tmp_path / "tool-results/original.json", payload)
    write_json(tmp_path / "tool_results_manifest.json", [{"virtual_path": virtual, "available": True, "file": "tool-results/original.json", "sha256": digest((tmp_path / "tool-results/original.json").read_bytes())}])
    builder = replay_trace(tmp_path)
    assert next(iter(builder.messages.values()))["message"]["captured_original_payload"] == payload


def test_prediction_is_replayed_not_trusted(tmp_path: Path) -> None:
    trace = tmp_path / "traces/synthetic-01"
    trace.mkdir(parents=True)
    recorder = Recorder(trace, "2026-10-22T00:00:00+00:00")
    final = {"status": "failed", "sql": "", "parameters_json": "{}", "notes": "synthetic"}
    recorder.save(("event: values\ndata: " + json.dumps({"messages": [{"id": "final", "type": "ai", "content": json.dumps(final)}]}) + "\n\nevent: end\ndata: null\n\n").encode(), stream=True)
    write_json(trace / "admission.json", {"thread_id": "synthetic-thread", "run_id": "synthetic-run"})
    write_json(trace / "terminal.json", {"run_status": "success", "elapsed_seconds": recorder.elapsed(), "capture_issues": []})
    write_json(tmp_path / "capture_config.json", {"synthetic": True})
    prediction = make_prediction(tmp_path, "synthetic-01")
    assert audit_prediction(tmp_path, prediction)
    prediction["sql"] = "SELECT 1"
    assert not audit_prediction(tmp_path, prediction)


def test_unique_final_json_can_follow_narrative() -> None:
    builder = TraceBuilder()
    final = {"status": "completed", "sql": "SELECT amount FROM alpha", "parameters_json": "{}", "notes": "synthetic"}
    builder.add_message({"type": "ai", "id": "final", "content": "合成说明。\n" + json.dumps(final)}, "", {"elapsed_seconds": 1})
    parsed, issues = builder.final()
    assert parsed["status"] == "completed" and not issues


def test_ambiguous_final_json_is_not_selected_for_score() -> None:
    builder = TraceBuilder()
    first = {"status": "completed", "sql": "SELECT amount FROM alpha", "parameters_json": "{}", "notes": "synthetic"}
    second = {**first, "sql": "SELECT id FROM alpha"}
    builder.add_message({"type": "ai", "id": "final", "content": json.dumps(first) + "\n" + json.dumps(second)}, "", {"elapsed_seconds": 1})
    parsed, issues = builder.final()
    assert parsed["status"] == "failed" and issues


def test_deerflow_messages_event_preserves_native_child_chunks() -> None:
    builder = TraceBuilder()
    for text in ['{"show_summary":', "false}"]:
        first = text.startswith("{")
        builder.accept_frame(
            "messages|child",
            [{"id": "native-ai", "type": "AIMessageChunk", "content": "", "tool_call_chunks": [{"index": 0, "id": "native-tool" if first else None, "name": "vfssql_vfs_tree_folder" if first else None, "args": text}]}, {}],
            {"elapsed_seconds": 1},
        )
    builder.add_message({"type": "tool", "tool_call_id": "native-tool", "name": "vfssql_vfs_tree_folder", "content": '{"status":"success"}'}, "child", {"elapsed_seconds": 2})
    calls, _, issues = builder.calls()
    assert len(calls) == 1 and calls[0]["tool_call_id"] == "native-tool" and not issues


def test_unfinished_generation_is_not_an_executed_tool_call() -> None:
    builder = TraceBuilder()
    builder.accept_frame("messages", [{"id": "partial-ai", "type": "AIMessageChunk", "content": "", "tool_call_chunks": [{"index": 0, "id": "partial-tool", "name": "vfssql_sql_execute", "args": '{"sql":'}]}, {}], {"elapsed_seconds": 1})
    assert builder.budget_counts() == (0, 0, False)
    calls, _, issues = builder.calls()
    assert not calls and not issues


def test_finished_native_chunk_counts_toward_budget() -> None:
    builder = TraceBuilder()
    builder.accept_frame("messages", [{"id": "ai", "type": "AIMessageChunk", "content": "", "tool_call_chunks": [{"index": 0, "id": "tool", "name": "vfssql_vfs_tree_folder", "args": "{}"}]}, {}], {"elapsed_seconds": 1})
    builder.accept_frame("messages", [{"id": "ai", "type": "AIMessageChunk", "content": "", "response_metadata": {"finish_reason": "tool_calls"}}, {}], {"elapsed_seconds": 2})
    assert builder.budget_counts() == (1, 0, False)


def test_native_message_is_exact_question_only() -> None:
    question = '  原题中的空格、引号"与换行\n必须保留。  '
    assert build_prompt({"case_id": "synthetic-01", "question": question}, {"evaluation_mode": NATIVE_MODE, "budgets": NATIVE_BUDGETS}) == question
    with pytest.raises(ValueError):
        build_prompt({"case_id": "synthetic-01", "question": question, "gold_sql": "禁止泄漏"}, {"evaluation_mode": NATIVE_MODE})


def test_observation_thresholds_do_not_cancel() -> None:
    assert NATIVE_BUDGETS["timeout_seconds"] is None
    assert NATIVE_BUDGETS["enforce_tool_limits"] is False
    assert "/cancel" not in (Path(__file__).resolve().parents[1] / "run_deerflow.py").read_text(encoding="utf-8")


def test_native_score_has_no_tree_first_or_count_limit() -> None:
    sample, record = synthetic()
    record["tool_calls"] = record["tool_calls"][1:3] * 15 + [record["tool_calls"][-1]] * 5
    record["elapsed_seconds"] = 7200
    assert score_one(sample, record, NATIVE_BUDGETS, audit_ok=True)["primary_success"]
    assert not score_one(sample, record, BUDGETS, audit_ok=True)["primary_success"]


def test_server_timing_excludes_collector_handover_gap() -> None:
    timing = execution_timing({"created_at": "2026-10-22T06:00:00+00:00", "updated_at": "2026-10-22T06:30:00+00:00"}, 3600)
    assert timing["elapsed_seconds"] == 1800
    assert timing["observed_elapsed_seconds"] == 3600
    assert timing["timing_source"] == "gateway_run_created_at_to_updated_at"
    assert execution_timing({}, 10)["elapsed_seconds"] == 10


def native_trace(tmp_path: Path, *, final: bool = True, learning: bool = False) -> dict:
    """构造无 SSE 末帧的原生消息导出。Args: 目录及响应开关。Returns: 重放预测。"""
    trace = tmp_path / "traces/synthetic-01"
    trace.mkdir(parents=True)
    recorder = Recorder(trace, "2026-10-22T00:00:00+00:00")
    sql = "SELECT amount FROM alpha"
    tool = "vfssql_sql_learn" if learning else "vfssql_sql_execute"
    messages = [
        {"type": "ai", "id": "skill-ai", "content": "", "tool_calls": [{"id": "skill", "name": "read_file", "args": {"path": "/mnt/skills/public/z-vfssql-retrieval/SKILL.md"}}]},
        {"type": "tool", "id": "skill-result", "tool_call_id": "skill", "name": "read_file", "content": "合成技能内容"},
        {"type": "ai", "id": "sql-ai", "content": "", "tool_calls": [{"id": "execute", "name": tool, "args": {"sql": sql, "parameters": {}}}]},
        {"type": "tool", "id": "sql-result", "tool_call_id": "execute", "name": tool, "content": json.dumps({"ok": True, "executed": True, "status": "empty", "rows": []})},
    ]
    if final:
        messages.append({"type": "ai", "id": "final", "content": "当前条件无匹配记录。无需特殊输出格式。"})
    rows = [{"seq": i, "event_type": "llm.tool.result" if m["type"] == "tool" else "llm.ai.response", "content": m, "metadata": {"caller": "lead_agent"}} for i, m in enumerate(messages)]
    recorder.save(json.dumps({"data": rows, "has_more": False}).encode(), method="GET", path="/api/threads/synthetic/runs/synthetic/messages", params={}, status_code=200)
    write_json(trace / "admission.json", {"thread_id": "synthetic-thread", "run_id": "synthetic-run"})
    write_json(trace / "terminal.json", {"run_status": "success", "terminal_confirmed": True, "elapsed_seconds": 10, "capture_issues": ["sse_did_not_close_after_terminal"]})
    write_json(tmp_path / "capture_config.json", {"evaluation_mode": NATIVE_MODE})
    return make_prediction(tmp_path, "synthetic-01")


def test_native_durable_recovery_natural_answer_and_skill_allowed(tmp_path: Path) -> None:
    record = native_trace(tmp_path)
    assert record["status"] == "completed" and record["answer_text"].startswith("当前条件")
    assert record["sql"] == "SELECT amount FROM alpha" and record["sql_source"] == "last_sql_execute_request"
    assert record["capture"]["complete"] and record["capture"]["durable_messages_complete"]
    assert "sse_did_not_close_after_terminal" in record["capture"]["warnings"]
    assert not record["protocol_issues"] and len(record["all_tool_calls"]) == 2
    assert audit_prediction(tmp_path, record)


def test_native_sql_success_without_final_answer_fails(tmp_path: Path) -> None:
    record = native_trace(tmp_path, final=False)
    assert record["status"] == "failed" and "final_response_missing" in record["protocol_issues"]


def test_lagging_journal_does_not_hide_final_sse_answer() -> None:
    builder = TraceBuilder()
    old = {"id": "old", "type": "tool", "tool_call_id": "old-call", "content": "旧工具返回"}
    final = {"id": "final", "type": "ai", "content": "已经完成的自然语言答复"}
    builder.accept_frame("values", {"messages": [old, final]}, {"elapsed_seconds": 1})
    builder.accept_event({"seq": 5, "event_type": "llm.tool.result", "content": old}, {"elapsed_seconds": 2})
    value, issues = builder.native_final([], "success")
    assert value["answer_text"] == final["content"] and not issues


def test_native_learning_is_flagged(tmp_path: Path) -> None:
    assert native_trace(tmp_path, learning=True)["protocol_issues"] == ["unauthorized_learning_tool:vfssql_sql_learn"]


@pytest.mark.parametrize("confirmed", [True, False])
def test_terminal_capture_gap_continues_but_unknown_terminal_stops(tmp_path: Path, monkeypatch, confirmed: bool) -> None:
    from types import SimpleNamespace

    import run_deerflow as runner

    config = {"context": {"model_name": "synthetic-model"}, "concurrency": 1, "native_skill": {"name": "vfssql-retrieval"}, "base_url": "synthetic", "budgets": NATIVE_BUDGETS}
    monkeypatch.setattr(runner, "check_frozen", lambda _: config)
    monkeypatch.setenv("VFSSQL_EVAL_EMAIL", "synthetic")
    monkeypatch.setenv("VFSSQL_EVAL_PASSWORD", "synthetic")
    (tmp_path / "questions.jsonl").write_text(' {"case_id":"synthetic-01","question":"a"}\n{"case_id":"synthetic-02","question":"b"}\n', encoding="utf-8")
    handled = []

    class SyntheticGateway:
        """仅提供合成预检数据，不访问网络。"""

        def __init__(self, *args):
            self.client = self

        async def login(self):
            pass

        async def aclose(self):
            pass

        async def request(self, method, path):
            value = {"models": [{"name": "synthetic-model", "supports_thinking": True, "supports_reasoning_effort": True}]} if path.endswith("models") else {"skills": [{"name": "vfssql-retrieval", "enabled": True}]}
            return SimpleNamespace(json=lambda: value)

    async def synthetic_run_case(gateway, run_dir, question, config):
        handled.append(question["case_id"])
        _, record = synthetic()
        record["case_id"] = question["case_id"]
        if len(handled) == 1:
            record["capture"].update(complete=False, issues=["native_tool_result_missing"])
        write_json(run_dir / "predictions" / (record["case_id"] + ".json"), record)
        write_json(run_dir / "traces" / record["case_id"] / "terminal.json", {"terminal_confirmed": confirmed})
        return record

    monkeypatch.setattr(runner, "Gateway", SyntheticGateway)
    monkeypatch.setattr(runner, "run_case", synthetic_run_case)
    if confirmed:
        asyncio.run(runner.run(tmp_path, None))
        assert handled == ["synthetic-01", "synthetic-02"]
        assert (tmp_path / "predictions_frozen.json").exists()
    else:
        with pytest.raises(runner.GatewayFailure, match="前题终态"):
            asyncio.run(runner.run(tmp_path, None))
        assert handled == ["synthetic-01"]


def test_transient_retry_reuses_run_idempotency_key(monkeypatch) -> None:
    import httpx
    import run_deerflow as runner

    requests = []

    def synthetic_http(request):
        requests.append(request)
        return httpx.Response(502 if len(requests) == 1 else 200, json={"run_id": "same-synthetic-run"})

    async def no_delay(seconds):
        pass

    async def invoke():
        gateway = runner.Gateway("http://synthetic", "synthetic", "synthetic")
        await gateway.client.aclose()
        gateway.client = httpx.AsyncClient(base_url="http://synthetic", transport=httpx.MockTransport(synthetic_http))
        try:
            response = await gateway.request("POST", "/api/threads/synthetic/runs", json_body={"input": "synthetic"}, headers={"Idempotency-Key": "same-synthetic-key"})
            assert response.json()["run_id"] == "same-synthetic-run"
        finally:
            await gateway.client.aclose()

    monkeypatch.setattr(runner.asyncio, "sleep", no_delay)
    asyncio.run(invoke())
    assert len(requests) == 2
    assert all(r.headers["Idempotency-Key"] == "same-synthetic-key" for r in requests)
    assert requests[0].content == requests[1].content


def test_adoption_preserves_conversation_and_source_bytes(tmp_path: Path) -> None:
    from start_evaluation import adopt_capture

    source, target = tmp_path / "old", tmp_path / "new"
    config = {"evaluation_mode": NATIVE_MODE, "skill_loading": "deerflow_autodiscovery", "questions_sha256": "synthetic", "dataset_sha256": "synthetic", "context": {}, "native_skill": {"name": "synthetic"}}
    for p in [source, target]:
        write_json(p / "capture_config.json", config)
    write_json(source / "pipeline_status.json", {"pid": None})
    (target / "questions.jsonl").write_text('{"case_id":"synthetic-01","question":"原题"}\n', encoding="utf-8")
    trace = source / "traces/synthetic-01"
    admission = {"thread_id": "same-thread", "run_id": "same-run"}
    write_json(trace / "admission.json", admission)
    write_json(trace / "request.json", {"input": {"messages": [{"type": "human", "content": "原题"}]}})
    (trace / "stream.sse").write_bytes(b"synthetic-original-bytes")
    before = (trace / "stream.sse").read_bytes()
    adopt_capture(target, source)
    assert (target / "traces/synthetic-01/stream.sse").read_bytes() == before
    assert json.loads((target / "traces/synthetic-01/admission.json").read_text(encoding="utf-8")) == admission
    assert (trace / "stream.sse").read_bytes() == before
    assert json.loads((target / "_private/adoption.json").read_text(encoding="utf-8"))["same_conversations_no_resubmission"]


def test_supervisor_only_accepts_learning_schema_rejection() -> None:
    from supervise_evaluation import learning_was_rejected

    rejected = {"tool": "sql_learn", "is_error": True, "result": {"isError": True, "content": "Error: Tool 'vfssql_sql_learn' failed with McpError: invalid tool arguments; use the advertised input schema. Continue with available context."}}
    assert learning_was_rejected({"tool_calls": [rejected]})
    assert not learning_was_rejected({"tool_calls": []})
    for payload in [{"ok": True, "applied": False, "status": "pending"}, {"ok": True, "applied": True}, {"ok": False, "error": "unknown_server_failure"}]:
        uncertain = {"tool": "sql_learn", "is_error": True, "result": {"structuredContent": payload}}
        assert not learning_was_rejected({"tool_calls": [uncertain]})
        assert not learning_was_rejected({"tool_calls": [rejected, uncertain]})


def test_supervisor_preserves_failed_prediction_and_prevents_repeat(tmp_path: Path, monkeypatch) -> None:
    import deerflow_evidence
    from supervise_evaluation import record_safe_continuation

    monkeypatch.setattr(deerflow_evidence, "audit_prediction", lambda *_: True)
    write_json(tmp_path / "pipeline_status.json", {"error_type": "GatewayFailure", "error": "检测到知识学习调用，已停止批次"})
    (tmp_path / "progress.jsonl").write_text('{"case_id":"case-0029"}\n', encoding="utf-8")
    record = {
        "status": "failed",
        "run_status": "success",
        "capture": {"complete": True},
        "tool_calls": [{"tool": "sql_learn", "is_error": True, "result": {"isError": True, "content": "McpError: invalid tool arguments; use the advertised input schema"}}],
    }
    prediction = tmp_path / "predictions/case-0029.json"
    write_json(prediction, record)
    write_json(tmp_path / "traces/case-0029/terminal.json", {"terminal_confirmed": True})
    before, handled = prediction.read_bytes(), set()
    assert record_safe_continuation(tmp_path, handled) == "case-0029"
    assert prediction.read_bytes() == before and handled == {"case-0029"}
    with pytest.raises(RuntimeError, match="同题重复停批"):
        record_safe_continuation(tmp_path, handled)
