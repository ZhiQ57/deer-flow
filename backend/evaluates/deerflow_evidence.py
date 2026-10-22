"""从原始 DeerFlow SSE 和运行事件重建调用，保留原生 ID 与证据缺口。"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from eval_common import NATIVE_MODE, digest, read_json

VFSSQL_TOOLS = {"tree_folder", "vfs_find_file", "vfs_grep_file", "vfs_cat_read", "vfs_sed", "vfs_bash_execute", "sql_execute"}
ORCHESTRATION_TOOLS = {"tool_search", "write_todos", "task", "task_list", "task_wait", "ask_clarification"}


def tool_result_paths(text: str) -> set[str]:
    """提取工具明确公开的本题结果路径。Args: text 工具正文。Returns: 路径集合。"""
    return set(re.findall(r"/mnt/user-data/outputs/\.?tool-results/[A-Za-z0-9_.-]+", text))


def normalize_name(name: str) -> str:
    """规范宿主工具名前缀。Args: name 原始名称。Returns: 基础名称。"""
    if name.startswith("mcp__"):
        name = name.rsplit("__", 1)[-1]
    if name.startswith("vfssql_"):
        name = name[7:]
    return "tree_folder" if name == "vfs_tree_folder" else name


def content_text(content: Any) -> str:
    """只提取文本块。Args: content 消息正文。Returns: 文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(block["text"] for block in content if isinstance(block, dict) and isinstance(block.get("text"), str))
    return ""


def result_payload(message: dict) -> dict:
    """解包真实工具消息；不将模型摘要当业务成功。Args: message。Returns: 业务字典。"""
    if isinstance(message.get("captured_original_payload"), dict):
        return message["captured_original_payload"]
    artifact = message.get("artifact")
    if isinstance(artifact, dict):
        for key in ["structured_content", "structuredContent"]:
            if isinstance(artifact.get(key), dict):
                return artifact[key]
    text = content_text(message.get("content"))
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


class SseDecoder:
    """按原始字节边界增量解码 SSE；不依赖网络分块与事件对齐。"""

    def __init__(self) -> None:
        """初始化残留字节。Args: 无。Returns: 无。"""
        self.buffer = b""

    def accept(self, data: bytes) -> list[tuple[str, Any]]:
        """接受网络字节并输出完整事件。Args: data。Returns: 事件列表。"""
        self.buffer += data
        frames = []
        while match := re.search(rb"\r?\n\r?\n", self.buffer):
            raw, self.buffer = self.buffer[: match.start()], self.buffer[match.end() :]
            name, parts = "message", []
            for line in raw.decode("utf-8").splitlines():
                if line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith("data:"):
                    parts.append(line[5:].lstrip(" "))
            if parts:
                text = "\n".join(parts)
                frames.append((name, None if text in {"[DONE]", ""} else json.loads(text)))
        return frames


class TraceBuilder:
    """按原生消息/调用 ID 去重，将主代理和子图证据分开关联。"""

    def __init__(self) -> None:
        """初始化。Args: 无。Returns: 无。"""
        self.messages: dict[tuple[str, str], dict] = {}
        self.chunks: dict[tuple[str, str], dict] = {}
        self.substeps: dict[tuple[str, int], dict] = {}
        self.issues: list[str] = []
        self.order = 0
        self.terminal_sse = False
        self.durable_messages_complete = False
        self.root_journal_messages: dict[int, dict] = {}
        self.root_snapshot_messages: list[dict] = []

    def add_message(self, message: dict, scope: str, receipt: dict, *, priority: int = 1) -> None:
        """加入完整原生消息。Args: message/scope/receipt/priority。Returns: 无。"""
        if not isinstance(message, dict) or message.get("type") not in {"ai", "AIMessageChunk", "tool"}:
            return
        if message.get("type") == "tool":
            identity = "tool:" + str(message.get("tool_call_id") or message.get("id") or "")
        else:
            identity = "ai:" + str(message.get("id") or digest(json.dumps(message, sort_keys=True, ensure_ascii=False).encode()))
        key = (scope, identity)
        previous = self.messages.get(key)
        if previous is not None:
            if priority >= previous["priority"] or result_payload(message) and not result_payload(previous["message"]):
                previous["message"] = message
                previous["priority"] = priority
            return
        self.order += 1
        self.messages[key] = {"message": message, "scope": scope, "order": self.order, "elapsed_seconds": receipt.get("elapsed_seconds"), "received_at_utc": receipt.get("received_at_utc"), "priority": priority}

    def accept_frame(self, name: str, data: Any, receipt: dict) -> None:
        """处理真实流事件，保留子图 namespace。Args: name/data/receipt。Returns: 无。"""
        base, _, scope = name.partition("|")
        if base == "end":
            self.terminal_sse = True
        if base == "values" and isinstance(data, dict):
            if not scope:
                self.root_snapshot_messages = data.get("messages", [])
            for msg in data.get("messages", []):
                self.add_message(msg, scope, receipt)
        elif base == "updates" and isinstance(data, dict):
            for update in data.values():
                if isinstance(update, dict):
                    for msg in update.get("messages", []):
                        self.add_message(msg, scope, receipt)
        elif base in {"messages-tuple", "messages"} and isinstance(data, list) and len(data) == 2:
            msg, meta = data
            if not isinstance(msg, dict) or not isinstance(meta, dict):
                return
            if msg.get("type") == "tool":
                self.add_message(msg, scope, receipt)
            elif msg.get("id") and msg.get("tool_call_chunks"):
                # 子图可能只向父流公开消息增量；以真实 chunk id/index 拼接，不生成调用 ID。
                key = (scope, msg["id"])
                entry = self.chunks.setdefault(key, {"calls": {}, "receipt": receipt, "finished": False})
                for chunk in msg["tool_call_chunks"]:
                    call = entry["calls"].setdefault(chunk.get("index", 0), {"id": "", "name": "", "args": ""})
                    for field in ["id", "name", "args"]:
                        if isinstance(chunk.get(field), str):
                            call[field] += chunk[field]
            chunk_key = (scope, msg.get("id"))
            if chunk_key in self.chunks and ((msg.get("response_metadata") or {}).get("finish_reason") or msg.get("chunk_position") == "last"):
                self.chunks[chunk_key]["finished"] = True
        elif base == "custom" and isinstance(data, dict) and isinstance(data.get("step"), dict):
            self.accept_event({"event_type": "subagent.step", "content": {"task_id": data.get("task_id"), **data["step"]}}, receipt)

    def budget_counts(self) -> tuple[int, int, bool]:
        """从已观察原生请求增量计数，避免等待落库。Args: 无。Returns: MCP/SQL/学习。"""
        calls: dict[tuple[str, str], str] = {}
        for entry in self.messages.values():
            for call in entry["message"].get("tool_calls", []):
                if call.get("id") and str(call.get("name", "")).startswith("vfssql_"):
                    calls[(entry["scope"], call["id"])] = normalize_name(call["name"])
        for (scope, _), entry in self.chunks.items():
            if not entry.get("finished"):
                continue
            for call in entry["calls"].values():
                if call["id"] and call["name"].startswith("vfssql_"):
                    calls[(scope, call["id"])] = normalize_name(call["name"])
        root = Counter(name for (scope, _), name in calls.items() if not scope)
        child = Counter(name for (scope, _), name in calls.items() if scope)
        summaries = Counter(normalize_name(c.get("name", "")) for step in self.substeps.values() if step.get("kind") == "ai" for c in step.get("tool_calls", []) if str(c.get("name", "")).startswith("vfssql_"))
        counts = root + (child | summaries)
        return sum(counts.values()), counts["sql_execute"], bool(counts["sql_learn"])

    def accept_event(self, event: dict, receipt: dict) -> None:
        """补充持久化原始事件。Args: event/receipt。Returns: 无。"""
        kind, content = event.get("event_type"), event.get("content")
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except ValueError:
                pass
        if kind in {"llm.ai.response", "llm.tool.result"} and isinstance(content, dict):
            caller = (event.get("metadata") or {}).get("caller", "lead_agent")
            scope = "" if caller == "lead_agent" else "journal:" + caller
            self.add_message(content, scope, receipt, priority=3)
            if not scope and isinstance(event.get("seq"), int):
                self.root_journal_messages[event["seq"]] = content
        elif kind == "subagent.step" and isinstance(content, dict):
            step = content.get("step", content)
            task = str(step.get("task_id") or (event.get("metadata") or {}).get("task_id") or "")
            self.substeps[(task, step.get("message_index", event.get("seq", -1)))] = step

    def finalize_chunks(self) -> None:
        """补全仅由原生增量提供的调用。Args: 无。Returns: 无。"""
        for (scope, msg_id), entry in self.chunks.items():
            if (scope, "ai:" + msg_id) in self.messages:
                continue
            native_result = any(e["scope"] == scope and e["message"].get("type") == "tool" and e["message"].get("tool_call_id") in {c["id"] for c in entry["calls"].values()} for e in self.messages.values())
            if not entry.get("finished") and not native_result:
                # 被取消的生成增量尚未形成真实工具请求，不伪造 MCP 调用。
                continue
            calls = []
            for call in entry["calls"].values():
                try:
                    args = json.loads(call["args"])
                except ValueError:
                    self.issues.append("incomplete_native_tool_argument_chunks")
                    continue
                if call["id"] and call["name"] and isinstance(args, dict):
                    calls.append({**call, "args": args})
                else:
                    self.issues.append("native_tool_id_or_arguments_missing")
            if calls:
                self.add_message({"type": "ai", "id": msg_id, "content": "", "tool_calls": calls}, scope, entry["receipt"])

    def calls(self) -> tuple[list[dict], list[dict], list[str]]:
        """重建真实调用及工具边界问题。Args: 无。Returns: MCP/全部调用/问题。"""
        self.finalize_chunks()
        calls, results = {}, {}
        issues = list(self.issues)
        for entry in self.messages.values():
            msg, scope = entry["message"], entry["scope"]
            if msg.get("type") == "tool":
                results[(scope, str(msg.get("tool_call_id", "")))] = entry
            else:
                for call in msg.get("tool_calls", []):
                    call_id, name, args = call.get("id"), call.get("name"), call.get("args")
                    if not call_id or not name or not isinstance(args, dict):
                        issues.append("native_tool_call_invalid")
                        continue
                    calls[(scope, str(call_id))] = (call, entry)
        all_calls = []
        for key, (call, start) in calls.items():
            end = results.pop(key, None)
            native = end["message"] if end else {}
            payload = result_payload(native)
            result = {"content": native.get("content", ""), "isError": native.get("status") == "error"}
            if payload:
                result["structuredContent"] = payload
            if not end:
                result = {"isError": True, "error": "no_tool_result_captured"}
                issues.append("native_tool_result_missing")
            all_calls.append(
                {
                    "tool_call_id": key[1],
                    "scope": key[0],
                    "native_tool": call["name"],
                    "tool": normalize_name(call["name"]),
                    "arguments": call["args"],
                    "result": result,
                    "is_error": native.get("status") == "error" or end is None,
                    "request_order": start["order"],
                    "result_order": end["order"] if end else None,
                    "started_at_utc": start["received_at_utc"],
                    "finished_at_utc": end["received_at_utc"] if end else None,
                    "observed_duration_seconds": max(0, end["elapsed_seconds"] - start["elapsed_seconds"]) if end and end["elapsed_seconds"] is not None and start["elapsed_seconds"] is not None else None,
                }
            )
        if any(normalize_name(entry["message"].get("name", "")) in VFSSQL_TOOLS for entry in results.values()):
            issues.append("native_tool_request_missing")
        # 摘要只用于发现漏采，不能补造子代理请求、返回及 ID。
        summarized = Counter(normalize_name(step.get("tool_name", "")) for step in self.substeps.values() if step.get("kind") == "tool" and normalize_name(step.get("tool_name", "")) in VFSSQL_TOOLS)
        native_children = Counter(c["tool"] for c in all_calls if c["scope"] and c["tool"] in VFSSQL_TOOLS)
        if any(native_children[name] < count for name, count in summarized.items()):
            issues.append("subagent_native_tool_messages_missing")
        all_calls.sort(key=lambda c: (c["result_order"] is None, c["result_order"] or c["request_order"]))
        mcp = [c for c in all_calls if c["native_tool"].startswith("vfssql_") or c["tool"] in VFSSQL_TOOLS]
        return mcp, all_calls, sorted(set(issues))

    def final(self) -> tuple[dict, list[str]]:
        """解析主代理最终 JSON。Args: 无。Returns: 响应/协议问题。"""
        entries = [e for e in self.messages.values() if not e["scope"] and e["message"].get("type") == "ai" and not e["message"].get("tool_calls") and content_text(e["message"].get("content")).strip()]
        default = {"status": "failed", "sql": "", "parameters": {}, "notes": "未捕获合法最终 JSON"}
        if not entries:
            return default, ["final_response_missing"]
        text = content_text(entries[-1]["message"].get("content")).strip()
        decoder, candidates = json.JSONDecoder(), {}
        for match in re.finditer(r"\{", text):
            try:
                value, _ = decoder.raw_decode(text, match.start())
                if not isinstance(value, dict) or set(value) != {"status", "sql", "parameters_json", "notes"}:
                    continue
                if value["status"] not in {"completed", "failed", "needs_clarification"} or not all(isinstance(value[k], str) for k in value):
                    continue
                parameters = json.loads(value["parameters_json"])
                if not isinstance(parameters, dict):
                    continue
                final = {"status": value["status"], "sql": value["sql"], "parameters": parameters, "notes": value["notes"]}
                candidates[json.dumps(final, sort_keys=True, ensure_ascii=False, allow_nan=False)] = final
            except (ValueError, TypeError, KeyError):
                continue
        if len(candidates) == 1:
            return next(iter(candidates.values())), []
        return default, ["final_response_ambiguous_json" if candidates else "final_response_invalid_json"]

    def native_final(self, calls: list[dict], run_status: str) -> tuple[dict, list[str]]:
        """保留原生答复并从实际执行请求取 SQL。Args: calls/run_status。Returns: 预测及缺口。"""
        roots = sorted((e for e in self.messages.values() if not e["scope"]), key=lambda e: e["order"])
        last = roots[-1]["message"] if roots else {}
        if self.root_journal_messages:
            last = self.root_journal_messages[max(self.root_journal_messages)]
            if self.root_snapshot_messages and last.get("id") and any(m.get("id") == last["id"] for m in self.root_snapshot_messages):
                # 完整状态中的消息顺序可证明末条流消息比尚未落库的旧事件更新。
                last = self.root_snapshot_messages[-1]
        answer = content_text(last.get("content")) if last.get("type") == "ai" and not last.get("tool_calls") else ""
        executions = sorted((c for c in calls if c["tool"] == "sql_execute"), key=lambda c: c["request_order"])
        request = executions[-1]["arguments"] if executions else {}
        return {
            "status": "completed" if answer.strip() and run_status == "success" else "failed",
            "answer_text": answer,
            "sql": request.get("sql", "") if isinstance(request.get("sql", ""), str) else "",
            "parameters": request.get("parameters", {}) if isinstance(request.get("parameters", {}), dict) else {},
            "sql_source": "last_sql_execute_request" if executions else "no_sql_execute_request",
            "notes": "自然语言答复按原文归档；SQL/参数来自最后一次实际执行请求。",
        }, [] if answer.strip() else ["final_response_missing"]


def recorded_chunks(trace: Path, stem: str) -> list[tuple[bytes, dict]]:
    """校验并重放原始字节收据。Args: trace/stem。Returns: 字节和收据列表。"""
    raw_path = trace / ("stream.sse" if stem == "stream" else "http-bodies.bin")
    receipts_path = trace / ("receipts.jsonl" if stem == "stream" else "http-receipts.jsonl")
    if not raw_path.exists() or not receipts_path.exists():
        return []
    raw, offset, previous, chunks = raw_path.read_bytes(), 0, -1.0, []
    for line in receipts_path.read_text(encoding="utf-8").splitlines():
        receipt = json.loads(line)
        length = receipt["byte_length"]
        chunk = raw[offset : offset + length]
        if receipt["byte_offset"] != offset or len(chunk) != length or digest(chunk) != receipt["sha256"] or receipt["elapsed_seconds"] < previous:
            raise ValueError("原始字节收据不一致")
        previous = receipt["elapsed_seconds"]
        chunks.append((chunk, receipt))
        offset += length
    if offset != len(raw):
        raise ValueError("原始字节存在未登记尾部")
    return chunks


def replay_trace(trace: Path) -> TraceBuilder:
    """离线重放 SSE，再以持久化事件补充。Args: trace。Returns: 解析器。"""
    builder, decoder = TraceBuilder(), SseDecoder()
    received = [(raw, receipt, True) for raw, receipt in recorded_chunks(trace, "stream")]
    received += [(raw, receipt, False) for raw, receipt in recorded_chunks(trace, "http")]
    seen, message_cursor, exporting = set(), None, False
    for chunk, receipt, stream in sorted(received, key=lambda item: item[1]["elapsed_seconds"]):
        if stream:
            try:
                for name, data in decoder.accept(chunk):
                    builder.accept_frame(name, data, receipt)
            except (ValueError, TypeError, UnicodeError):
                builder.issues.append("sse_decode_error")
            continue
        path = receipt.get("path", "")
        if receipt.get("status_code") != 200 or not path.endswith(("/events", "/messages")):
            continue
        payload = json.loads(chunk)
        if path.endswith("/events"):
            rows = payload
        else:
            rows = sorted(payload.get("data", []), key=lambda row: row["seq"])
            before = receipt.get("params", {}).get("before_seq")
            if before is None:
                exporting, message_cursor = True, None
            elif before != message_cursor:
                exporting = False
            if payload.get("has_more"):
                message_cursor = min((row["seq"] for row in rows), default=None)
            elif exporting:
                builder.durable_messages_complete = True
        for event in rows:
            if event["seq"] not in seen:
                builder.accept_event(event, receipt)
                seen.add(event["seq"])
    manifest_path = trace / "tool_results_manifest.json"
    manifest = read_json(manifest_path) if manifest_path.exists() else []
    for entry in builder.messages.values():
        message = entry["message"]
        if message.get("type") != "tool":
            continue
        text = content_text(message.get("content"))
        referenced = [item for item in manifest if item["virtual_path"] in text]
        for item in referenced:
            if not item["available"]:
                if not result_payload(message):
                    builder.issues.append("externalized_tool_result_unavailable")
                continue
            path = (trace / item["file"]).resolve()
            if not path.is_relative_to(trace.resolve()) or digest(path.read_bytes()) != item["sha256"]:
                raise ValueError("工具原文文件哈希不一致")
            try:
                payload = json.loads(path.read_bytes())
            except ValueError:
                continue
            if isinstance(payload, dict):
                # 原生消息正文保留不变，恢复来源单独标识；并不声称模型已经读取落盘全文。
                message["captured_original_payload"] = payload
                message["captured_original_file"] = item["file"]
    return builder


def make_prediction(run_dir: Path, case_id: str) -> dict:
    """由保存的原始数据生成预测。Args: run_dir/case_id。Returns: 冻结记录。"""
    trace = run_dir / "traces" / case_id
    config = read_json(run_dir / "capture_config.json")
    native = config.get("evaluation_mode") == NATIVE_MODE
    admission, terminal = read_json(trace / "admission.json"), read_json(trace / "terminal.json")
    builder = replay_trace(trace)
    mcp, all_calls, capture_issues = builder.calls()
    final, protocol_issues = builder.native_final(mcp, terminal["run_status"]) if native else builder.final()
    warnings = []
    if not builder.terminal_sse:
        capture_issues.append("sse_terminal_frame_missing")
    capture_issues.extend(terminal.get("capture_issues", []))
    if native and (builder.terminal_sse or builder.durable_messages_complete and terminal.get("terminal_confirmed")):
        recoverable = {i for i in capture_issues if i.startswith(("sse_", "live_sse_"))}
        warnings.extend(sorted(recoverable))
        capture_issues = [i for i in capture_issues if i not in recoverable]
    if native and not terminal.get("terminal_confirmed"):
        capture_issues.append("run_terminal_not_confirmed")
    permitted_result_paths: set[str] = set()
    for entry in builder.messages.values():
        if entry["message"].get("type") == "tool":
            permitted_result_paths.update(tool_result_paths(content_text(entry["message"].get("content"))))
    for call in all_calls:
        tool = call["tool"]
        if native:
            if tool == "sql_learn":
                protocol_issues.append("unauthorized_learning_tool:" + call["native_tool"])
            continue
        if tool in VFSSQL_TOOLS or tool in ORCHESTRATION_TOOLS:
            continue
        path = call["arguments"].get("path", call["arguments"].get("file_path"))
        if tool in {"read_file", "read_file_tool"} and isinstance(path, str) and path in permitted_result_paths:
            continue
        protocol_issues.append("unexpected_or_learning_tool:" + call["native_tool"])
    files = {p.relative_to(run_dir).as_posix(): digest(p.read_bytes()) for p in sorted(trace.rglob("*")) if p.is_file()}
    return {
        "case_id": case_id,
        **final,
        "thread_id": admission["thread_id"],
        "run_id": admission["run_id"],
        "elapsed_seconds": terminal["elapsed_seconds"],
        **({k: terminal[k] for k in ["observed_elapsed_seconds", "timing_source", "server_created_at", "server_updated_at"] if k in terminal} if native else {}),
        "run_status": terminal["run_status"],
        "stop_reason": terminal.get("stop_reason"),
        "protocol_issues": sorted(set(protocol_issues)),
        "tool_calls": mcp,
        "all_tool_calls": all_calls,
        "capture": {
            "source": "deerflow_gateway",
            "complete": not capture_issues,
            "issues": sorted(set(capture_issues)),
            **({"warnings": warnings, "durable_messages_complete": builder.durable_messages_complete} if native else {}),
            "files": files,
            "configuration_sha256": digest((run_dir / "capture_config.json").read_bytes()),
            "timing": "host monotonic receive intervals; not server invocation latency",
        },
    }


def audit_prediction(run_dir: Path, record: dict) -> bool:
    """重放并比较冻结预测，不接受事后手写调用。Args: run_dir/record。Returns: 一致性。"""
    capture = record.get("capture", {})
    if capture.get("source") != "deerflow_gateway" or capture.get("configuration_sha256") != digest((run_dir / "capture_config.json").read_bytes()):
        return False
    expected_prefix = "traces/" + record["case_id"] + "/"
    for relative, expected in capture.get("files", {}).items():
        path = (run_dir / relative).resolve()
        if not relative.startswith(expected_prefix) or not path.is_relative_to((run_dir / "traces").resolve()) or not path.is_file() or digest(path.read_bytes()) != expected:
            return False
    return make_prediction(run_dir, record["case_id"]) == record
