"""通过正在运行的 DeerFlow Gateway 串行执行独立对话并自动归档原始证据。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from deerflow_evidence import SseDecoder, TraceBuilder, content_text, make_prediction, normalize_name, replay_trace, tool_result_paths
from eval_common import append_json, check_frozen, digest, read_json, utc_now, write_json
from prepare_dataset import build_prompt


class GatewayFailure(RuntimeError):
    """Gateway 鉴权、连接或协议问题；停止当前批次以便检查证据。"""


class Recorder:
    """同步落盘原始字节和 monotonic 接收收据，不保存认证响应。"""

    def __init__(self, trace: Path, started_at: str) -> None:
        """初始化接收时钟。Args: trace/started_at。Returns: 无。"""
        self.trace = trace
        self.origin = time.monotonic()
        self.base_elapsed = max(0.0, (datetime.now(UTC) - datetime.fromisoformat(started_at)).total_seconds())
        for file in ["stream.sse", "receipts.jsonl", "http-bodies.bin", "http-receipts.jsonl"]:
            (trace / file).touch(exist_ok=True)
        for receipt_name in ["receipts.jsonl", "http-receipts.jsonl"]:
            lines = (trace / receipt_name).read_text(encoding="utf-8").splitlines()
            if lines:
                self.base_elapsed = max(self.base_elapsed, json.loads(lines[-1])["elapsed_seconds"])

    def elapsed(self) -> float:
        """返回本题累计时间。Args: 无。Returns: 秒。"""
        return self.base_elapsed + time.monotonic() - self.origin

    def save(self, raw: bytes, *, stream: bool = False, **metadata) -> dict:
        """保存完整响应字节。Args: raw/stream/metadata。Returns: 接收收据。"""
        path = self.trace / ("stream.sse" if stream else "http-bodies.bin")
        receipt = {"byte_offset": path.stat().st_size, "byte_length": len(raw), "sha256": digest(raw), "received_at_utc": utc_now(), "elapsed_seconds": self.elapsed(), **metadata}
        with path.open("ab") as file:
            file.write(raw)
            file.flush()
        append_json(self.trace / ("receipts.jsonl" if stream else "http-receipts.jsonl"), receipt)
        return receipt


class Gateway:
    """只使用现有账号会话的 API 客户端；凭据仅存运行内存。"""

    def __init__(self, base_url: str, email: str, password: str) -> None:
        """初始化客户端。Args: base_url/email/password。Returns: 无。"""
        self.email, self.password = email, password
        self.client = httpx.AsyncClient(base_url=base_url, headers={"Origin": base_url}, timeout=httpx.Timeout(30, read=45), trust_env=False)

    async def login(self) -> None:
        """完成账号登录与 CSRF 配对。Args: 无。Returns: 无。"""
        response = await self.client.post("/api/v1/auth/login/local", data={"username": self.email, "password": self.password, "remember_me": "false"})
        if response.status_code != 200:
            raise GatewayFailure(f"登录失败 HTTP {response.status_code}")
        csrf = self.client.cookies.get("csrf_token")
        if not csrf:
            raise GatewayFailure("登录响应缺少 CSRF cookie")
        self.client.headers["X-CSRF-Token"] = csrf

    async def request(self, method: str, path: str, *, recorder: Recorder | None = None, json_body: dict | None = None, params: dict | None = None, headers: dict | None = None) -> httpx.Response:
        """请求 API 并登记原始正文。Args: 请求字段/recorder。Returns: HTTP 响应。"""
        attempts = 3 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                response = await self.client.request(method, path, json=json_body, params=params, headers=headers)
                if response.status_code == 401:
                    await self.login()
                    response = await self.client.request(method, path, json=json_body, params=params, headers=headers)
                if recorder:
                    recorder.save(response.content, method=method, path=path, params=params or {}, status_code=response.status_code)
                if response.status_code >= 400:
                    raise GatewayFailure(f"{method} {path} HTTP {response.status_code}")
                return response
            except httpx.HTTPError as error:
                if attempt + 1 == attempts:
                    raise GatewayFailure(type(error).__name__) from error
                await asyncio.sleep(attempt + 1)
        raise GatewayFailure("请求未返回")


def observed_budget(events: list[dict]) -> tuple[int, int, bool]:
    """从真实 AI 调用和子代理步骤计预算。Args: events。Returns: MCP/SQL/学习标记。"""
    calls, child_counts = {}, Counter()
    for event in events:
        kind, content = event.get("event_type"), event.get("content")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except ValueError:
                continue
        if not isinstance(content, dict):
            continue
        if kind == "llm.ai.response":
            for call in content.get("tool_calls", []):
                name = call.get("name", "")
                if name.startswith("vfssql_"):
                    calls[(str((event.get("metadata") or {}).get("caller", "lead_agent")), call.get("id"))] = normalize_name(name)
        elif kind == "subagent.step":
            step = content.get("step", content)
            if step.get("kind") == "ai":
                for call in step.get("tool_calls", []):
                    name = call.get("name", "")
                    if name.startswith("vfssql_"):
                        child_counts[normalize_name(name)] += 1
    counts = Counter(calls.values()) + child_counts
    return sum(counts.values()), counts["sql_execute"], bool(counts["sql_learn"])


async def save_stream(gateway: Gateway, path: str, recorder: Recorder, issues: list[str], live: TraceBuilder) -> None:
    """归档原始 SSE 字节。Args: gateway/path/recorder/issues。Returns: 无。"""
    decoder = SseDecoder()
    try:
        async with gateway.client.stream("GET", path, timeout=httpx.Timeout(20, read=45)) as response:
            if response.status_code != 200:
                issues.append("sse_http_" + str(response.status_code))
                return
            async for chunk in response.aiter_bytes():
                if chunk:
                    receipt = recorder.save(chunk, stream=True)
                    try:
                        for name, data in decoder.accept(chunk):
                            live.accept_frame(name, data, receipt)
                    except (ValueError, TypeError, UnicodeError):
                        issues.append("live_sse_decode_error")
    except (httpx.HTTPError, OSError) as error:
        issues.append("sse_transport_" + type(error).__name__)


async def recover_tool_files(gateway: Gateway, run_dir: Path, case_id: str, recorder: Recorder) -> None:
    """下载本题工具公开引用的原始结果文件。Args: gateway/run_dir/case_id/recorder。Returns: 无。"""
    trace = run_dir / "traces" / case_id
    admission = read_json(trace / "admission.json")
    builder = replay_trace(trace)
    paths = set()
    for entry in builder.messages.values():
        if entry["message"].get("type") != "tool":
            continue
        paths.update(tool_result_paths(content_text(entry["message"].get("content"))))
    manifest = []
    for virtual in sorted(paths):
        # 仅下载当前 thread 中工具明确公开的结果路径，不扫描宿主目录。
        path = f"/api/threads/{admission['thread_id']}/artifacts/{virtual.lstrip('/')}"
        try:
            response = await gateway.request("GET", path, recorder=recorder)
        except GatewayFailure:
            manifest.append({"virtual_path": virtual, "available": False})
            continue
        relative = "tool-results/" + digest(virtual.encode()) + ".txt"
        target = trace / relative
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(response.content)
        manifest.append({"virtual_path": virtual, "available": True, "file": relative, "sha256": digest(response.content)})
    write_json(trace / "tool_results_manifest.json", manifest)


async def fetch_events(gateway: Gateway, prefix: str, recorder: Recorder, after_seq: int | None) -> list[dict]:
    """按游标读完本次新增事件，避免单页上限截尾。Args: API 与游标。Returns: 完整新增页。"""
    events = []
    while True:
        params = {"limit": 2000, **({"after_seq": after_seq} if after_seq is not None else {})}
        page = (await gateway.request("GET", prefix + "/events", recorder=recorder, params=params)).json()
        events.extend(page)
        if len(page) < 2000:
            return events
        cursor = max(event["seq"] for event in page)
        if after_seq is not None and cursor <= after_seq:
            raise GatewayFailure("事件分页游标未前进")
        after_seq = cursor


async def run_case(gateway: Gateway, run_dir: Path, question: dict, config: dict) -> dict:
    """执行或恢复一题，不重新提交已受理运行。Args: 当前题及配置。Returns: 冻结预测。"""
    case_id = question["case_id"]
    trace = run_dir / "traces" / case_id
    trace.mkdir(exist_ok=True)
    admission_path = trace / "admission.json"
    if admission_path.exists():
        admission = read_json(admission_path)
    else:
        admission = {"thread_id": str(uuid4()), "run_id": None, "idempotency_key": f"vfssql-eval:{run_dir.name}:{case_id}", "started_at_utc": utc_now()}
        write_json(admission_path, admission, exclusive=True)
    recorder = Recorder(trace, admission["started_at_utc"])
    if (trace / "terminal.json").exists():
        record = make_prediction(run_dir, case_id)
        write_json(run_dir / "predictions" / f"{case_id}.json", record, exclusive=True)
        return record
    if not admission["run_id"]:
        check = await gateway.client.get(f"/api/threads/{admission['thread_id']}")
        if check.status_code == 404:
            await gateway.request(
                "POST",
                "/api/threads",
                recorder=recorder,
                json_body={"thread_id": admission["thread_id"], "assistant_id": "lead_agent", "metadata": {"title": f"VFSSQL {config['purpose']} {case_id}", "vfssql_evaluation": run_dir.name, "case_id": case_id}},
            )
        elif check.status_code != 200:
            raise GatewayFailure(f"读取评估 thread HTTP {check.status_code}")
        prompt = build_prompt(question, config)
        body = {
            "assistant_id": "lead_agent",
            "input": {"messages": [{"type": "human", "id": f"eval-{case_id}-{admission['thread_id']}", "content": prompt}]},
            "context": {**config["context"], "thread_id": admission["thread_id"]},
            "config": {"recursion_limit": config["recursion_limit"]},
            "stream_mode": config["stream_mode"],
            "stream_subgraphs": config["stream_subgraphs"],
            "on_disconnect": "continue",
            "metadata": {"vfssql_evaluation": run_dir.name, "case_id": case_id},
        }
        request_path = trace / "request.json"
        if request_path.exists() and read_json(request_path) != body:
            raise GatewayFailure("受理前请求发生变化")
        if not request_path.exists():
            write_json(request_path, body, exclusive=True)
        response = await gateway.request("POST", f"/api/threads/{admission['thread_id']}/runs", recorder=recorder, json_body=body, headers={"Idempotency-Key": admission["idempotency_key"]})
        admission["run_id"] = response.json()["run_id"]
        write_json(admission_path, admission)
        append_json(run_dir / "case_threads.jsonl", {"case_id": case_id, **admission})
    prefix = f"/api/threads/{admission['thread_id']}/runs/{admission['run_id']}"
    issues: list[str] = []
    live = TraceBuilder()
    stream = asyncio.create_task(save_stream(gateway, prefix + "/join", recorder, issues, live))
    events: dict[int, dict] = {}
    after_seq, stop_reason, last_progress, terminal = None, None, -30.0, {}
    try:
        while True:
            page = await fetch_events(gateway, prefix, recorder, after_seq)
            for event in page:
                events[event["seq"]] = event
                live.accept_event(event, {"elapsed_seconds": recorder.elapsed(), "received_at_utc": utc_now()})
            if page:
                after_seq = max(event["seq"] for event in page)
            http_count, http_sql_count, http_learning = observed_budget(list(events.values()))
            live_count, live_sql_count, live_learning = live.budget_counts()
            count, sql_count, learning = max(http_count, live_count), max(http_sql_count, live_sql_count), http_learning or live_learning
            if recorder.elapsed() >= config["budgets"]["timeout_seconds"]:
                stop_reason = "case_timeout"
            elif count > config["budgets"]["max_tool_calls"]:
                stop_reason = "tool_call_budget_exceeded"
            elif sql_count > config["budgets"]["max_sql_executions"]:
                stop_reason = "sql_execution_budget_exceeded"
            elif learning:
                stop_reason = "learning_tool_called"
            if stop_reason:
                try:
                    await gateway.request("POST", prefix + "/cancel", recorder=recorder, params={"wait": "false", "action": "interrupt"})
                except GatewayFailure:
                    pass
            terminal = (await gateway.request("GET", prefix, recorder=recorder)).json()
            status = terminal.get("status")
            if recorder.elapsed() - last_progress >= 30:
                print(json.dumps({"case_id": case_id, "status": status, "elapsed_seconds": round(recorder.elapsed(), 1), "mcp_observed": count, "sql_observed": sql_count}, ensure_ascii=False), flush=True)
                last_progress = recorder.elapsed()
            if status not in {"pending", "running"}:
                break
            if stop_reason:
                await asyncio.sleep(1)
            else:
                await asyncio.sleep(2)
        # 终态后等待已有流收尾，再获取落库的最后一页；不再次调用模型。
        try:
            await asyncio.wait_for(asyncio.shield(stream), timeout=15)
        except TimeoutError:
            issues.append("sse_did_not_close_after_terminal")
        page = await fetch_events(gateway, prefix, recorder, after_seq)
        for event in page:
            events[event["seq"]] = event
        before = None
        while True:
            params = {"limit": 200, **({"before_seq": before} if before else {})}
            messages = (await gateway.request("GET", prefix + "/messages", recorder=recorder, params=params)).json()
            if not messages.get("has_more"):
                break
            before = min(row["seq"] for row in messages["data"])
        await recover_tool_files(gateway, run_dir, case_id, recorder)
    except (GatewayFailure, OSError, ValueError) as error:
        issues.append("gateway_capture_" + type(error).__name__)
        try:
            await gateway.request("POST", prefix + "/cancel", recorder=recorder, params={"wait": "false"})
        except (GatewayFailure, httpx.HTTPError):
            pass
        stop_reason = "gateway_capture_failure"
    finally:
        if not stream.done():
            stream.cancel()
        await asyncio.gather(stream, return_exceptions=True)
    elapsed = recorder.elapsed()
    write_json(trace / "terminal.json", {"run_status": terminal.get("status", "unknown"), "elapsed_seconds": elapsed, "stop_reason": stop_reason, "capture_issues": issues, "finished_at_utc": utc_now()}, exclusive=True)
    record = make_prediction(run_dir, case_id)
    write_json(run_dir / "predictions" / f"{case_id}.json", record, exclusive=True)
    append_json(
        run_dir / "progress.jsonl",
        {
            "case_id": case_id,
            "run_status": record["run_status"],
            "model_status": record["status"],
            "mcp_calls": len(record["tool_calls"]),
            "sql_calls": sum(c["tool"] == "sql_execute" for c in record["tool_calls"]),
            "capture_complete": record["capture"]["complete"],
            "capture_issues": record["capture"]["issues"],
            "protocol_issues": record["protocol_issues"],
            "elapsed_seconds": elapsed,
        },
    )
    return record


async def run(run_dir: Path, limit: int | None) -> None:
    """执行当前授权批次，环境或采集故障时暂停。Args: run_dir/limit。Returns: 无。"""
    config = check_frozen(run_dir)
    email, password = os.environ.get("VFSSQL_EVAL_EMAIL"), os.environ.get("VFSSQL_EVAL_PASSWORD")
    if not email or not password:
        raise GatewayFailure("需设置 VFSSQL_EVAL_EMAIL 与 VFSSQL_EVAL_PASSWORD")
    gateway = Gateway(config["base_url"], email, password)
    try:
        await gateway.login()
        models = (await gateway.request("GET", "/api/models")).json()["models"]
        model = next((m for m in models if m["name"] == config["context"]["model_name"]), None)
        if not model or not model.get("supports_thinking") or not model.get("supports_reasoning_effort"):
            raise GatewayFailure("指定模型或推理能力不可用")
        if not (run_dir / "model_preflight.json").exists():
            write_json(run_dir / "model_preflight.json", {"checked_at_utc": utc_now(), "model": model}, exclusive=True)
        questions = [json.loads(line) for line in (run_dir / "questions.jsonl").read_text(encoding="utf-8").splitlines()]
        completed = 0
        for question in questions:
            check_frozen(run_dir)
            prediction = run_dir / "predictions" / f"{question['case_id']}.json"
            if prediction.exists():
                continue
            record = await run_case(gateway, run_dir, question, config)
            completed += 1
            print(
                json.dumps(
                    {
                        "case_id": question["case_id"],
                        "finished": True,
                        "model_status": record["status"],
                        "run_status": record["run_status"],
                        "mcp_calls": len(record["tool_calls"]),
                        "capture_complete": record["capture"]["complete"],
                        "elapsed_seconds": round(record["elapsed_seconds"], 1),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            expected_cancel_gap = record.get("stop_reason") in {"case_timeout", "tool_call_budget_exceeded", "sql_execution_budget_exceeded"} and set(record["capture"]["issues"]) <= {
                "native_tool_result_missing",
                "sse_terminal_frame_missing",
            }
            if not record["capture"]["complete"] and not expected_cancel_gap:
                raise GatewayFailure("原始证据采集不完整，已保存本题并停止批次")
            if any(c["tool"] == "sql_learn" for c in record["tool_calls"]):
                raise GatewayFailure("检测到知识学习调用，已停止批次")
            if not record["tool_calls"] and record["run_status"] != "success" and record.get("stop_reason") != "case_timeout":
                raise GatewayFailure("模型未进入 MCP 流程且运行失败，停止批次检查环境")
            if limit is not None and completed >= limit:
                break
        if all((run_dir / "predictions" / f"{q['case_id']}.json").is_file() for q in questions):
            hashes = {q["case_id"]: digest((run_dir / "predictions" / f"{q['case_id']}.json").read_bytes()) for q in questions}
            target = run_dir / "predictions_frozen.json"
            if target.exists() and read_json(target)["prediction_hashes"] != hashes:
                raise GatewayFailure("已有冻结预测哈希不一致")
            if not target.exists():
                write_json(target, {"frozen_at_utc": utc_now(), "planned_cases": len(questions), "prediction_hashes": hashes}, exclusive=True)
            print(json.dumps({"complete": True, "predictions_frozen": len(questions)}, ensure_ascii=False), flush=True)
    finally:
        await gateway.client.aclose()


def main() -> None:
    """批次命令入口。Args: 命令行参数。Returns: 无。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    try:
        asyncio.run(run(args.run_dir.resolve(), args.limit))
    except (GatewayFailure, ValueError, OSError) as error:
        print(json.dumps({"stopped": True, "error": str(error)}, ensure_ascii=False), flush=True)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
