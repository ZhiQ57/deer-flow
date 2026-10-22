"""复用原评估器的纯离线表字段及 SQL 诊断函数；仅适配采集来源名。"""

from __future__ import annotations

import json
import math
import re

import sqlglot
from evaluation_evidence import TABLE_PATH, TOOLS, discovered_tables, normalize_tool, response_payload
from sqlglot import exp
from sqlglot.errors import ParseError, SqlglotError
from sqlglot.optimizer.scope import Scope, traverse_scope


def canonical_table(name: str, name_map: dict) -> str:
    """将公开表标识归一到评分侧名称，跨 Schema 同名另行判为待复核。

    Args:
        name: MCP 或目标标签中的表名。
        name_map: 操作员提供的私有公开名到目标名映射。

    Returns:
        用于匹配的无引号小写表名。
    """
    clean = ".".join(part.strip('`" ').casefold() for part in name.split("."))
    bare = clean.rsplit(".", 1)[-1]
    mapped = name_map.get(clean, name_map.get(bare))
    if mapped is None:
        candidates = {value for key, value in name_map.items() if key.rsplit(".", 1)[-1] == bare}
        mapped = candidates.pop() if len(candidates) == 1 else clean
    return mapped.rsplit(".", 1)[-1].strip('`" ').casefold()


def collect_schema(calls: list, name_map: dict) -> tuple:
    """从真实 grep/文件读取及历史响应收集结构证据。

    Args:
        calls: 按发生顺序保存的 MCP 工具调用。
        name_map: 仅评分侧可见的表名映射。

    Returns:
        首次召回序列、累计表字段、同名冲突和成功 workspace 响应。
    """
    first, tables, origins, workspace = None, {}, {}, None
    for call in calls:
        result = response_payload(call["result"])
        tool = normalize_tool(call["tool"])
        if (
            call.get("is_error")
            or call.get("isError")
            or call["result"].get("isError")
            or call["result"].get("is_error")
            or result.get("isError")
            or result.get("ok") is False
            or not (result.get("status") in ("ok", "success") or result.get("status") is None and result.get("ok") is True)
        ):
            continue
        candidates = [canonical_table(name, name_map) for name in discovered_tables(call)]
        if first is None and candidates:
            first = list(dict.fromkeys(candidates))
        if tool == "tree_folder":
            arguments = call["arguments"]
            summary = arguments.get("show_summary", True)
            expected = {"status", "workspace_content"} | ({"file_summaries"} if summary else set())
            if (
                set(arguments) <= {"path", "show_summary"}
                and isinstance(arguments.get("path", ""), str)
                and len(arguments.get("path", "")) <= 4096
                and isinstance(summary, bool)
                and result.get("status") == "success"
                and set(result) == expected
                and all(isinstance(value, str) and value.strip() for value in result.values())
            ):
                workspace = result
        records = []
        if tool == "search_sql_schema":
            records = result.get("tables") if isinstance(result.get("tables"), list) else []
        elif (tool == "inspect_sql_schema" and call["arguments"].get("mode") == "ddls") or tool in {"vfs_bash_execute", "vfs_cat_read", "vfs_sed"}:
            if tool == "vfs_sed" and call["arguments"].get("operation") != "print_range":
                continue
            content = result.get("content", result.get("result_content", ""))
            path = result.get("path") or result.get("file_path") or call["arguments"].get("file_path") or call["arguments"].get("path", "")
            if not isinstance(path, str) or not content or not isinstance(content, str):
                continue
            if tool == "vfs_cat_read" and call["arguments"].get("show_line") is True:
                content = re.sub(r"^ *\d+\t", "", content, flags=re.MULTILINE)
            metadata_records = []
            matches = TABLE_PATH.findall(path)
            if matches:
                schema, table_name = matches[-1]
                metadata_records = [{"schema": schema, "table": table_name}]
            elif isinstance(result.get("data"), list) and len(result["data"]) == 1:
                metadata_records = result["data"]
            for metadata in metadata_records:
                if not isinstance(metadata, dict) or not isinstance(metadata.get("table"), str):
                    continue
                columns = []
                # desc.md 的字段段位于正文末尾，后续分页可以不再包含段落标题。
                start_line = result.get("start_line") or call["arguments"].get("start_line") or 1
                in_columns = isinstance(start_line, int) and not isinstance(start_line, bool) and start_line > 1
                if path.endswith("/desc.md"):
                    for line in content.splitlines():
                        if line.strip() == "## 字段":
                            in_columns = True
                        elif in_columns and line.startswith("## "):
                            in_columns = False
                        elif in_columns and line.startswith("| "):
                            cell = line.split("|")[1].strip().strip("`")
                            if cell and cell != "字段" and set(cell) - set("-: "):
                                columns.append({"name": cell})
                elif path.endswith("/ddl.sql"):
                    try:
                        ddl = sqlglot.parse_one(content, read="mysql")
                        if isinstance(ddl, exp.Create):
                            ddl_table = ddl.this.this if isinstance(ddl.this, exp.Schema) else ddl.this
                            if isinstance(ddl_table, exp.Table) and canonical_table(f"{ddl_table.db}.{ddl_table.name}".strip("."), name_map) == canonical_table(f"{metadata.get('schema', '')}.{metadata['table']}".strip("."), name_map):
                                columns = [{"name": column.name} for column in ddl.find_all(exp.ColumnDef)]
                    except ParseError:
                        # 分页片段无法完整解析时不猜字段；覆盖不足进入待复核。
                        pass
                records.append({**metadata, "columns": columns})
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("table"), str):
                continue
            raw = f"{record.get('schema', '')}.{record['table']}".strip(".").casefold()
            table = canonical_table(raw, name_map)
            columns = record.get("columns") if isinstance(record.get("columns"), list) else []
            tables.setdefault(table, set()).update(column["name"].casefold() for column in columns if isinstance(column, dict) and isinstance(column.get("name"), str))
            origins.setdefault(table, set()).add(raw)
    collisions = {table for table, sources in origins.items() if len(sources) > 1}
    return first or [], tables, collisions, workspace


def retrieval_metrics(sample: dict, calls: list, name_map: dict) -> dict:
    """独立计算候选发现和读取结构覆盖，避免生成或记录格式失败连带归零。

    Args:
        sample: 仅评分侧可见的参考目标集合。
        calls: 已验证外层类型的调用序列。
        name_map: 操作员提供的名称映射。

    Returns:
        不包含目标名称的检索指标。
    """
    targets = {canonical_table(name, {}) for name in sample["target_tables"]}
    columns = {(canonical_table(item["table"], {}), item["column"].casefold()) for item in sample["target_columns"]}
    first, retrieved, _, _ = collect_schema(calls, name_map)
    candidates = set(retrieved) | {canonical_table(name, name_map) for call in calls for name in discovered_tables(call)}
    observed = {(table, column) for table, values in retrieved.items() for column in values}
    table_recall = len(targets & retrieved.keys()) / len(targets)
    coverage = len(columns & observed) / len(columns)
    return {
        "table_recall": table_recall,
        "column_coverage": coverage,
        "structure_complete": table_recall == 1 and coverage == 1,
        "candidate_table_recall": len(targets & candidates) / len(targets),
        "candidate_table_precision": len(targets & candidates) / len(candidates) if candidates else 0,
        "candidate_complete": targets <= candidates,
        "first_discovery_recall_at_k": {str(k): len(targets & set(first[:k])) / len(targets) for k in (1, 3, 5, 10)},
        "retrieved_table_precision": len(targets & retrieved.keys()) / len(retrieved) if retrieved else 0,
    }


def score_prediction(sample: dict, record: dict, name_map: dict, budgets: dict) -> dict:
    """分别核验结构、SQL 与执行证据，结构替代方案保留人工复核空间。

    Args:
        sample: 私有数据集样本，仅使用目标表字段标签，不比较 Gold SQL。
        record: 当前题的冻结预测及按序保存的 MCP 响应。
        name_map: 评分侧表名映射。
        budgets: 冻结工具次数、运行时间及 SQL 执行次数预算。

    Returns:
        不含目标标签正文、SQL 正文或业务值的逐题指标与判定。

    Raises:
        ValueError: 预测记录或目标标签格式非法。
    """
    calls = record.get("tool_calls")
    if not isinstance(calls, list) or any(not isinstance(call, dict) or not isinstance(call.get("tool"), str) or not isinstance(call.get("arguments"), dict) or not isinstance(call.get("result"), dict) for call in calls):
        raise ValueError("工具证据格式非法")
    if record.get("status") not in {"completed", "failed", "needs_clarification"} or not isinstance(record.get("sql"), str) or not isinstance(record.get("parameters"), dict):
        raise ValueError("预测终态、SQL 或参数格式非法")
    targets = {canonical_table(table, {}) for table in sample["target_tables"]}
    columns = {(canonical_table(item["table"], {}), item["column"].casefold()) for item in sample["target_columns"]}
    if not targets or not columns:
        raise ValueError("目标表字段标签不能为空")
    _, retrieved, collisions, workspace = collect_schema(calls, name_map)
    retrieval = retrieval_metrics(sample, calls, name_map)
    structure_ok = retrieval["structure_complete"]
    reasons, review_reasons = [], []
    if not structure_ok:
        review_reasons.append("reference_structure_not_fully_covered")
    if collisions:
        review_reasons.append("ambiguous_schema_table_name")
    if workspace is None and budgets.get("require_workspace", True):
        reasons.append("missing_workspace_evidence")
    elapsed = record.get("elapsed_seconds")
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed < 0:
        review_reasons.append("elapsed_time_not_recorded")
        elapsed = None
    elif elapsed > budgets["timeout_seconds"]:
        reasons.append("case_timeout")
    if budgets.get("enforce_tool_limits", True) and len(calls) > budgets["max_tool_calls"]:
        reasons.append("tool_call_budget_exceeded")
    if any(normalize_tool(call["tool"]) not in TOOLS for call in calls):
        reasons.append("unexpected_or_learning_tool")
    capture = record.get("capture")
    if capture is not None and (capture.get("source") != "deerflow_gateway" or not capture.get("complete") or capture.get("issues")):
        reasons.append("incomplete_or_invalid_event_capture")
    executions = [call for call in calls if normalize_tool(call["tool"]) == "sql_execute"]
    if budgets.get("enforce_tool_limits", True) and len(executions) > budgets["max_sql_executions"]:
        reasons.append("sql_execution_budget_exceeded")
    execution_ok, first_execution_ok, first_final_execution_ok = False, False, None
    for index, call in enumerate(executions):
        response = response_payload(call["result"])
        success = (
            not call.get("is_error")
            and not call.get("isError")
            and not call["result"].get("isError")
            and not call["result"].get("is_error")
            and not response.get("isError")
            and response.get("ok") is True
            and response.get("executed") is True
            and response.get("status") in ("ok", "empty")
            and not response.get("error")
        )
        if index == 0:
            first_execution_ok = success
        matches_final = call["arguments"].get("sql") == record["sql"] and json.dumps(call["arguments"].get("parameters", {}), sort_keys=True, allow_nan=False) == json.dumps(record["parameters"], sort_keys=True, allow_nan=False)
        if first_final_execution_ok is None and matches_final:
            first_final_execution_ok = success
        if index == len(executions) - 1:
            execution_ok = success and matches_final
    if not execution_ok:
        reasons.append("final_sql_execution_not_verified")
    used_tables, used_columns, counted_tables, unresolved = set(), set(), set(), False
    sql_structure_ok = False
    try:
        statements = sqlglot.parse(record["sql"], read="mysql")
        if len(statements) != 1 or not isinstance(statements[0], exp.Query) or any(statements[0].find_all(exp.DML, exp.DDL)):
            reasons.append("not_one_readonly_query")
        else:
            tree = statements[0]
            scopes = list(traverse_scope(tree))
            active_scopes, pending_scopes, reachable = [], scopes[-1:] if scopes else [], set()
            while pending_scopes:
                scope = pending_scopes.pop()
                if id(scope) in reachable:
                    continue
                reachable.add(id(scope))
                active_scopes.append(scope)
                pending_scopes.extend(scope.subquery_scopes)
                pending_scopes.extend(getattr(scope, "set_operation_scopes", getattr(scope, "union_scopes", [])))
                pending_scopes.extend(source for _, source in scope.selected_sources.values() if isinstance(source, Scope))
            # 未被最终查询引用的 CTE 不得用来凑齐目标表字段。
            for scope in active_scopes:
                sources = {alias: source for alias, (_, source) in scope.selected_sources.items()}
                if any(isinstance(count.this, exp.Star) for count in scope.find_all(exp.Count)):
                    pending, seen = [scope], set()
                    while pending:
                        counted_scope = pending.pop()
                        if id(counted_scope) in seen:
                            continue
                        seen.add(id(counted_scope))
                        pending.extend(getattr(counted_scope, "set_operation_scopes", getattr(counted_scope, "union_scopes", [])))
                        for _, source in counted_scope.selected_sources.values():
                            if isinstance(source, exp.Table):
                                counted_tables.add(canonical_table(f"{source.db}.{source.name}".strip("."), name_map))
                            elif isinstance(source, Scope):
                                pending.append(source)
                for source in sources.values():
                    if isinstance(source, exp.Table):
                        used_tables.add(canonical_table(f"{source.db}.{source.name}".strip("."), name_map))
                for projection in scope.expression.selects:
                    if isinstance(projection, exp.Star) or isinstance(projection, exp.Column) and projection.is_star:
                        for alias, source in sources.items():
                            if isinstance(source, exp.Table) and (not isinstance(projection, exp.Column) or projection.table == alias):
                                table = canonical_table(f"{source.db}.{source.name}".strip("."), name_map)
                                used_columns.update((table, name) for name in retrieved.get(table, set()))
                for column in scope.columns:
                    source = sources.get(column.table) if column.table else None
                    if not column.table:
                        possible = [item for item in sources.values() if isinstance(item, exp.Table)]
                        if len(sources) == 1 and len(possible) == 1:
                            source = possible[0]
                        elif len(sources) > 1:
                            # 分页结构未必包含所有同名列，不猜多源未限定列的归属。
                            unresolved = True
                    if isinstance(source, exp.Table) and column.name != "*":
                        used_columns.add((canonical_table(f"{source.db}.{source.name}".strip("."), name_map), column.name.casefold()))
            if not used_tables or not (list(tree.find_all(exp.Column)) or list(tree.find_all(exp.Count))):
                reasons.append("query_without_business_structure")
            if not used_tables <= retrieved.keys():
                review_reasons.append("sql_table_without_retrieval_evidence")
            if not targets <= used_tables:
                review_reasons.append("sql_uses_alternative_table_route")
            missing_columns = columns - used_columns
            # COUNT(*) 不需要显式引用唯一计数键 id；其他必要业务列仍需命中。
            count_key_equivalent = bool(missing_columns) and all(column == "id" and table in counted_tables for table, column in missing_columns)
            sql_structure_ok = targets <= used_tables and (not missing_columns or count_key_equivalent)
            if not sql_structure_ok and missing_columns:
                review_reasons.append("sql_required_columns_not_covered")
            if unresolved:
                review_reasons.append("sql_column_binding_requires_review")
    except SqlglotError:
        review_reasons.append("sql_parser_requires_review")
    if record["status"] != "completed":
        reasons.append("prediction_not_completed")
    verdict = "failed" if reasons else "needs_review" if review_reasons else "passed"
    return {
        "case_id": record["case_id"],
        "verdict": verdict,
        "reasons": reasons + review_reasons,
        **retrieval,
        "execution_ok": execution_ok,
        "first_execution_ok": first_execution_ok,
        "first_final_sql_execution_ok": first_final_execution_ok is True,
        "sql_table_recall": len(targets & used_tables) / len(targets),
        "sql_column_coverage": len(columns & used_columns) / len(columns),
        "sql_structure_complete": sql_structure_ok,
        "capture_complete": bool(capture and capture.get("complete") and not capture.get("issues")),
        "protocol_compliant": not (set(reasons) - {"final_sql_execution_not_verified", "prediction_not_completed"}) and elapsed is not None,
        "diagnostic_flags": [
            name
            for name, condition in (
                ("candidate_coverage_incomplete", not retrieval["candidate_complete"]),
                ("schema_evidence_incomplete", not structure_ok),
                ("sql_structure_incomplete", not sql_structure_ok),
                ("execution_unverified", not execution_ok),
                ("capture_incomplete", bool(capture) and not capture.get("complete")),
            )
            if condition
        ],
        "tool_calls": len(calls),
        "sql_executions": len(executions),
        "elapsed_seconds": elapsed,
        "technical_failure": bool(reasons),
    }
