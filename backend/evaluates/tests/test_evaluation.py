"""使用合成事件检查独立对话评估协议，不连接模型或业务数据。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deerflow_evidence import SseDecoder, TraceBuilder, audit_prediction, make_prediction, normalize_name, recorded_chunks, replay_trace
from eval_common import BUDGETS, digest, write_json
from run_deerflow import Recorder, observed_budget
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
