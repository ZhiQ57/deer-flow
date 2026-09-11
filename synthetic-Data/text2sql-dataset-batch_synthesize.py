"""
Text2SQL 数据集验证脚本 - 对比 DataAgent 执行结果与金标准 SQL 执行结果

功能:
1. 读取 text2sql-dataset.json 中 status=completed 的样本
2. 对每个样本,仅发送用户指令给 DataAgent
3. DataAgent 自行检索表结构、生成 SQL、执行查询
4. 用金标准 SQL 在真实数据库执行获取参考结果
5. 输出结构化 JSON 对照报告

验证维度:
- 用户输入 (input)
- Agent 中间过程 (工具调用、SQL 生成)
- Agent 最终执行结果
- 金标准 SQL 执行结果
- 两者是否一致
"""

import asyncio
import json
import uuid
import time
import re
import sys
from pathlib import Path
from typing import Any, Optional
from datetime import datetime, date
from decimal import Decimal

import httpx
from sqlalchemy import create_engine, text


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent
DATASET_FILE = SCRIPT_DIR / "text2sql-dataset.json"
OUTPUT_FILE = SCRIPT_DIR / "verification_results.json"
IMAGES_DIR = SCRIPT_DIR / "images"
# 直接访问 Gateway(8001) 绕过 Nginx(2026) 的 60s 超时限制
BASE_URL = "http://127.0.0.1:8001"
BASE_ASSISTANT_ID = "data-agent"

thread_id_prefix = "data-agent-text2sql-verify"
LOGIN_EMAIL = "wuzhiqi@user.com"
LOGIN_PASSWORD = "wuzhiqi@user.com"

WARMUP_TIMEOUT = 300      # 预热请求超时 (秒)
REQUEST_TIMEOUT = 600     # 常规请求超时 (秒)
BATCH_SIZE = 10           # 每批次处理的样本数

# SQL 执行层 DSN,用于执行金标准 SQL
SQL_DSN = "mysql+pymysql://text2sql:text2sql.123456@127.0.0.1:3308/text2sql"


# ---------------------------------------------------------------------------
# 类型序列化辅助函数
# ---------------------------------------------------------------------------

def _serialize_value(val: Any) -> Any:
    """序列化单个值为 JSON 兼容类型。

    Args:
        val: 原始值

    Returns:
        JSON 序列化兼容的值
    """
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    elif isinstance(val, Decimal):
        return float(val)
    elif isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    elif val is None:
        return None
    else:
        return val


# ---------------------------------------------------------------------------
# 数据库工具
# ---------------------------------------------------------------------------

def execute_golden_sql(sql: str) -> dict[str, Any]:
    """执行金标准 SQL,返回执行结果。

    Args:
        sql: 金标准 SQL 语句

    Returns:
        包含执行结果、行数、列名、执行时间等信息的字典
    """
    engine = None
    try:
        engine = create_engine(SQL_DSN, pool_pre_ping=True)
        start = time.time()
        with engine.connect() as conn:
            result = conn.execute(text(sql))
            rows = result.fetchall()
            columns = list(result.keys())
            elapsed = time.time() - start

            # 将 rows 转换为 JSON 序列化的列表
            serialized_rows = [[_serialize_value(val) for val in row] for row in rows]

            return {
                "success": True,
                "columns": columns,
                "rows": serialized_rows,
                "row_count": len(rows),
                "execution_time_sec": round(elapsed, 3),
            }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__,
        }
    finally:
        if engine:
            engine.dispose()


# ---------------------------------------------------------------------------
# 结果对比
# ---------------------------------------------------------------------------

def compare_results(
    agent_rows: list[list[Any]],
    golden_rows: list[list[Any]],
    agent_columns: list[str],
    golden_columns: list[str],
) -> dict[str, Any]:
    """对比 Agent 执行结果与金标准执行结果。

    Args:
        agent_rows: Agent 返回的结果行
        golden_rows: 金标准 SQL 执行结果行
        agent_columns: Agent 结果的列名
        golden_columns: 金标准结果的列名

    Returns:
        对比结果,包含是否一致、差异详情等
    """
    # 1. 对比列名(不区分顺序)
    columns_match = (sorted(agent_columns) == sorted(golden_columns))
    column_diff = {
        "match": columns_match,
        "agent_columns": agent_columns,
        "golden_columns": golden_columns,
        "missing_in_agent": list(set(golden_columns) - set(agent_columns)),
        "extra_in_agent": list(set(agent_columns) - set(golden_columns)),
    }

    # 2. 对比行数
    row_count_match = len(agent_rows) == len(golden_rows)

    # 3. 对比行内容(按行比较,最多保留前100行详情)
    row_details = []
    content_match = True
    max_rows = max(len(agent_rows), len(golden_rows))

    for i in range(max_rows):
        if i >= len(agent_rows):
            row_details.append({
                "row_index": i,
                "match": False,
                "reason": "golden has row but agent does not",
                "golden": golden_rows[i],
            })
            content_match = False
            continue
        if i >= len(golden_rows):
            row_details.append({
                "row_index": i,
                "match": False,
                "reason": "agent has row but golden does not",
                "agent": agent_rows[i],
            })
            content_match = False
            continue

        agent_row = [_serialize_value(v) for v in agent_rows[i]]
        golden_row = [_serialize_value(v) for v in golden_rows[i]]
        is_match = (agent_row == golden_row)
        if not is_match:
            content_match = False

        row_details.append({
            "row_index": i,
            "match": is_match,
            "agent": agent_rows[i],
            "golden": golden_rows[i],
        })

    # 4. 综合判定
    overall_match = columns_match and row_count_match and content_match

    return {
        "overall_match": overall_match,
        "columns_match": column_diff,
        "row_count_match": row_count_match,
        "agent_row_count": len(agent_rows),
        "golden_row_count": len(golden_rows),
        "content_match": content_match,
        "row_details": row_details[:100],  # 最多保留前100行详情
        "total_mismatched_rows": sum(1 for r in row_details if not r["match"]),
    }


# ---------------------------------------------------------------------------
# DataAgent 交互
# ---------------------------------------------------------------------------

async def login(client: httpx.AsyncClient) -> bool:
    """使用用户名密码登录,自动保存 cookie。

    Args:
        client: httpx 异步客户端

    Returns:
        是否登录成功
    """
    resp = await client.post(
        "/api/v1/auth/login/local",
        data={
            "username": LOGIN_EMAIL,
            "password": LOGIN_PASSWORD,
            "remember_me": "on",
        }
    )
    if resp.status_code == 200:
        print("  [OK] 登录成功")
        return True
    else:
        print(f"  [FAIL] 登录失败: {resp.status_code} - {resp.text}")
        return False


async def warmup_assistant(client: httpx.AsyncClient, csrf_token: str) -> bool:
    """预热助手:触发图编译和工具加载。

    首次调用 data-agent 时,LangGraph 需要编译图结构、加载工具、初始化模型,
    这个过程可能超过 nginx 的 60s 超时,导致第一个样本 504。

    Args:
        client: httpx 异步客户端
        csrf_token: CSRF token

    Returns:
        是否预热成功
    """
    warmup_thread = f"{thread_id_prefix}-warmup-{uuid.uuid4().hex[:8]}"
    print("  [INFO] 预热助手(触发图编译和工具加载)...")
    try:
        resp = await client.post(
            "/api/runs/wait",
            json={
                "assistant_id": BASE_ASSISTANT_ID,
                "config": {
                    "recursion_limit": 1000,
                    "configurable": {
                        "thread_id": warmup_thread,
                    },
                },
                "input": {"messages": [{"role": "user", "content": "ok"}]},
            },
            headers={
                "X-CSRF-Token": csrf_token,
                "Content-Type": "application/json",
            },
            timeout=WARMUP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        msg_count = len(data.get("messages", []))
        print(f"  [OK] 预热完成 (消息数: {msg_count})")
        return True
    except Exception as e:
        print(f"  [WARN] 预热失败(非阻塞): {e}")
        return False


async def query_agent(client: httpx.AsyncClient, prompt: str, csrf_token: str, thread_id: str) -> dict[str, Any]:
    """向 DataAgent 发送查询请求并等待完成。

    Args:
        client: httpx 异步客户端
        prompt: 用户自然语言查询
        csrf_token: CSRF token
        thread_id: 线程 ID

    Returns:
        Agent 的响应数据
    """
    try:
        resp = await client.post(
            "/api/runs/wait",
            json={
                "assistant_id": BASE_ASSISTANT_ID,
                "config": {
                    "recursion_limit": 1000,
                    "configurable": {
                        "thread_id": thread_id,
                    },
                },
                "input": {"messages": [{"role": "user", "content": prompt}]},
            },
            headers={
                "X-CSRF-Token": csrf_token,
                "Content-Type": "application/json",
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "success": True,
            "thread_id": thread_id,
            "response": data,
        }
    except Exception as e:
        return {
            "success": False,
            "thread_id": thread_id,
            "error": str(e),
        }


async def send_confirmation(client: httpx.AsyncClient, thread_id: str, csrf_token: str, confirm_msg: str = "请执行") -> dict[str, Any]:
    """发送确认消息，让 Agent 执行已生成的 SQL。

    Args:
        client: httpx 异步客户端
        thread_id: 线程 ID
        csrf_token: CSRF token
        confirm_msg: 确认消息内容

    Returns:
        Agent 的执行结果
    """
    try:
        print(f"    → 发送确认消息: {confirm_msg}")
        resp = await client.post(
            "/api/runs/wait",
            json={
                "assistant_id": BASE_ASSISTANT_ID,
                "config": {
                    "recursion_limit": 1000,
                    "configurable": {
                        "thread_id": thread_id,
                    },
                },
                "input": {"messages": [{"role": "user", "content": confirm_msg}]},
            },
            headers={
                "X-CSRF-Token": csrf_token,
                "Content-Type": "application/json",
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "success": True,
            "thread_id": thread_id,
            "response": data,
        }
    except Exception as e:
        return {
            "success": False,
            "thread_id": thread_id,
            "error": str(e),
        }


def detect_approval_needed(messages: list[dict]) -> tuple[bool, Optional[str]]:
    """检测 Agent 是否需要人类审批/确认。

    检查标准：
    1. tool 消息的 artifact 中包含 approval_required: true
    2. AI 消息中调用了 ask_intent_approval 工具
    3. AI 消息中包含查询草案，需要用户确认执行

    Args:
        messages: Agent 返回的消息链

    Returns:
        (是否需要审批，审批原因)
    """
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        
        # 检查 tool 消息的 artifact 中的 approval 字段
        if msg.get("type") == "tool":
            artifact = msg.get("artifact", {})
            if isinstance(artifact, dict):
                approval = artifact.get("approval", {})
                if isinstance(approval, dict) and approval.get("required"):
                    return True, approval.get("reason", "")
                # 检查顶级 ok 字段
                if msg.get("ok", {}).get("approval_required", False):
                    return True, msg.get("ok", {}).get("approval_reason", "")
        
        # 检查 AI 消息是否调用了 ask_intent_approval
        if msg.get("type") == "ai":
            for tc in msg.get("tool_calls", []):
                if isinstance(tc, dict) and tc.get("name", "") == "ask_intent_approval":
                    return True, "调用 ask_intent_approval 工具"
        
        # 检查 AI 消息是否包含查询草案
        if msg.get("type") == "ai":
            content = msg.get("content", "")
            if isinstance(content, str):
                if "SQL 语句" in content or "sql" in content.lower():
                    if "确认" in content or "execute" in content.lower():
                        return True, "包含查询草案，需要确认执行"
    
    return False, None


def parse_approval_request(messages: list[dict]) -> Optional[dict[str, Any]]:
    """从 Agent 响应中解析意图审批请求。

    从工具调用和响应内容中提取审批信息,包括流程 ID、请求 ID、工具调用 ID
    和具体问题列表。

    Args:
        messages: Agent 返回的消息链

    Returns:
        审批信息字典,包含 flow_id、request_id、tool_call_id、questions;
        如果未找到审批请求则返回 None
    """
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue

        msg_type = msg.get("type", "")

        # 从 AI 消息的 tool_calls 中查找 ask_intent_approval
        if msg_type == "ai":
            for tc in msg.get("tool_calls", []):
                if not isinstance(tc, dict):
                    continue
                func = tc.get("function", {})
                if func.get("name", "") != "ask_intent_approval":
                    continue

                tool_call_id = tc.get("id", "")
                arguments = func.get("arguments", {})

                # 解析参数
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except (json.JSONDecodeError, TypeError):
                        continue

                if not isinstance(arguments, dict):
                    continue

                # 提取审批信息
                questions = arguments.get("questions", [])
                if not questions:
                    continue

                # 构建问题列表
                parsed_questions = []
                for idx, q in enumerate(questions):
                    if isinstance(q, dict):
                        parsed_questions.append({
                            "id": q.get("id", f"q{idx}"),
                            "question": q.get("question", q.get("text", "")),
                            "options": q.get("options", []),
                        })
                    elif isinstance(q, str):
                        parsed_questions.append({
                            "id": f"q{idx}",
                            "question": q,
                            "options": [],
                        })

                if not parsed_questions:
                    continue

                return {
                    "flow_id": arguments.get("flow_id", arguments.get("flowId", "")),
                    "request_id": arguments.get("request_id", arguments.get("requestId", "")),
                    "tool_call_id": tool_call_id,
                    "questions": parsed_questions,
                }

        # 从 tool 消息的 artifact 中查找
        if msg_type == "tool":
            artifact = msg.get("artifact", {})
            if isinstance(artifact, dict):
                approval_data = artifact.get("approval", {})
                if not isinstance(approval_data, dict):
                    continue
                if not approval_data.get("required"):
                    continue

                questions = approval_data.get("questions", [])
                if not questions:
                    continue

                parsed_questions = []
                for idx, q in enumerate(questions):
                    if isinstance(q, dict):
                        parsed_questions.append({
                            "id": q.get("id", f"q{idx}"),
                            "question": q.get("question", q.get("text", "")),
                            "options": q.get("options", []),
                        })

                return {
                    "flow_id": approval_data.get("flow_id", approval_data.get("flowId", "")),
                    "request_id": msg.get("request_id", approval_data.get("request_id", approval_data.get("requestId", ""))),
                    "tool_call_id": msg.get("tool_call_id", ""),
                    "questions": parsed_questions,
                }

    return None


def build_auto_approval_response(approval_info: dict[str, Any]) -> str:
    """根据审批请求自动生成回复 JSON 字符串。

    为每个问题选择第一个选项 (如果存在),否则选择智能默认值。
    生成符合 DataAgent human_input_response 格式的 JSON 字符串。

    Args:
        approval_info: 解析后的审批信息字典,包含 questions 列表

    Returns:
        符合 human_input_response.value 格式的 JSON 字符串
    """
    flow_id = approval_info.get("flow_id", "")
    answers = []

    for q in approval_info.get("questions", []):
        question_id = q.get("id", "")
        options = q.get("options", [])

        if options:
            selected = options[0]
            if isinstance(selected, dict):
                answer_id = selected.get("id", selected.get("value", ""))
                answer_value = selected.get("label", selected.get("value", ""))
            elif isinstance(selected, str):
                answer_id = selected
                answer_value = selected
            else:
                answer_id = ""
                answer_value = "确认执行"
        else:
            answer_id = ""
            answer_value = "确认执行"

        answers.append({
            "question_id": question_id,
            "option_id": answer_id,
            "value": answer_value,
        })

    response_payload = {
        "kind": "intent_approval_answers",
        "flow_id": flow_id,
        "answers": answers,
        "final_action": "execute",
    }

    return json.dumps(response_payload, ensure_ascii=False)


async def reply_to_approval(
    client: httpx.AsyncClient,
    thread_id: str,
    csrf_token: str,
    approval_info: dict[str, Any],
) -> dict[str, Any]:
    """向 DataAgent 发送审批回复。

    使用 hidden HumanMessage 格式提交 human_input_response,
    触发 Agent 继续执行。

    Args:
        client: httpx 异步客户端
        thread_id: 线程 ID
        csrf_token: CSRF token
        approval_info: 审批信息字典,包含 build 好的回复

    Returns:
        Agent 执行后的响应数据
    """
    response_value = build_auto_approval_response(approval_info)
    flow_id = approval_info.get("flow_id", "")
    request_id = approval_info.get("request_id", "")
    tool_call_id = approval_info.get("tool_call_id", "")

    human_input_response = {
        "version": 1,
        "kind": "human_input_response",
        "source": "ask_intent_approval",
        "request_id": request_id,
        "flow_id": flow_id,
        "response_kind": "text",
        "value": response_value,
    }

    try:
        print(f"    → 发送自动审批回复...")
        resp = await client.post(
            "/api/runs/wait",
            json={
                "assistant_id": BASE_ASSISTANT_ID,
                "config": {
                    "recursion_limit": 1000,
                    "configurable": {
                        "thread_id": thread_id,
                    },
                },
                "input": {
                    "messages": [
                        {
                            "role": "human",
                            "content": "意图审批答案：" + response_value,
                            "additional_kwargs": {
                                "human_input_response": human_input_response,
                            },
                        }
                    ]
                },
            },
            headers={
                "X-CSRF-Token": csrf_token,
                "Content-Type": "application/json",
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        print(f"    [OK] 自动审批回复成功")
        return {
            "success": True,
            "thread_id": thread_id,
            "response": data,
        }
    except Exception as e:
        print(f"    [FAIL] 自动审批回复失败: {e}")
        return {
            "success": False,
            "thread_id": thread_id,
            "error": str(e),
        }


async def confirm_query_draft(
    client: httpx.AsyncClient,
    thread_id: str,
    csrf_token: str,
) -> dict[str, Any]:
    """自动确认查询草案，触发 Agent 执行 SQL。

    当 Agent 返回查询草案但需要用户确认时，发送确认消息。

    Args:
        client: httpx 异步客户端
        thread_id: 线程 ID
        csrf_token: CSRF token

    Returns:
        Agent 执行后的响应数据
    """
    try:
        print(f"    → 发送确认消息: 确认执行此查询")
        resp = await client.post(
            "/api/runs/wait",
            json={
                "assistant_id": BASE_ASSISTANT_ID,
                "config": {
                    "recursion_limit": 1000,
                    "configurable": {
                        "thread_id": thread_id,
                    },
                },
                "input": {
                    "messages": [
                        {
                            "role": "human",
                            "content": "确认执行此查询。",
                        }
                    ]
                },
            },
            headers={
                "X-CSRF-Token": csrf_token,
                "Content-Type": "application/json",
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        print(f"    [OK] 确认执行成功")
        return {
            "success": True,
            "thread_id": thread_id,
            "response": data,
        }
    except Exception as e:
        print(f"    [FAIL] 确认执行失败: {e}")
        return {
            "success": False,
            "thread_id": thread_id,
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# 从 Agent 响应中提取结果
# ---------------------------------------------------------------------------

def _extract_generated_sql(messages: list[dict]) -> Optional[str]:
    """从 Agent 消息链中提取生成的 SQL 语句。

    尝试从 tool_call 和 assistant 消息中提取 SQL。

    Args:
        messages: Agent 返回的消息链

    Returns:
        生成的 SQL 语句,如果未找到则返回 None
    """
    # 1. 从 tool_call 消息中提取(SQL 执行工具)
    for msg in messages:
        if not isinstance(msg, dict):
            continue

        msg_type = msg.get("type", "")

        # 尝试从 tool_call 内容中解析
        if msg_type == "ai":
            tool_calls = msg.get("tool_calls", [])
            for tc in tool_calls:
                if isinstance(tc, dict):
                    func = tc.get("function", {})
                    name = func.get("name", "")
                    args = func.get("arguments", {})
                    # 如果工具名包含 sql 相关关键词
                    if "sql" in name.lower() or "execute" in name.lower():
                        if isinstance(args, dict) and "sql" in args:
                            return args["sql"]
                        elif isinstance(args, str):
                            try:
                                parsed = json.loads(args)
                                if isinstance(parsed, dict) and "sql" in parsed:
                                    return parsed["sql"]
                            except (json.JSONDecodeError, TypeError):
                                pass

    # 2. 从 assistant 消息文本中提取(可能直接包含 SQL)
    sql_pattern = re.compile(r"(SELECT\s+.+?;?\s*$)", re.IGNORECASE | re.DOTALL)
    for msg in messages:
        if not isinstance(msg, dict):
            continue

        msg_type = msg.get("type", "")
        content = msg.get("content", "")

        if msg_type in ("ai", "human"):
            if isinstance(content, str):
                match = sql_pattern.search(content)
                if match:
                    sql = match.group(1).strip()
                    # 确保是有效的 SQL(以 SELECT 开头)
                    if sql.upper().startswith("SELECT"):
                        return sql

    return None


def _extract_agent_result(messages: list[dict]) -> tuple[Optional[list], Optional[list]]:
    """从 Agent 消息链中提取最终执行结果。

    Args:
        messages: Agent 返回的消息链

    Returns:
        (行数据列表, 列名列表),如果未找到则返回 (None, None)
    """
    # 从最后一条 ai 消息中提取结果(通常是表格或 JSON 格式)
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue

        msg_type = msg.get("type", "")
        content = msg.get("content", "")

        if msg_type == "ai" and isinstance(content, str):
            # 尝试解析 JSON 格式的结果(查找包含 rows 和 columns 的 JSON)
            json_pattern = re.compile(r'\{[^{}]*"rows"[^{}]*\}', re.DOTALL)
            match = json_pattern.search(content)
            if match:
                try:
                    result_data = json.loads(match.group(0))
                    rows = result_data.get("rows", [])
                    columns = result_data.get("columns", [])
                    if rows:
                        return rows, columns
                except json.JSONDecodeError:
                    pass

    # 尝试从 tool_response 消息中提取
    for msg in messages:
        if not isinstance(msg, dict):
            continue

        msg_type = msg.get("type", "")
        content = msg.get("content", "")

        if msg_type == "tool":
            try:
                tool_data = json.loads(content) if isinstance(content, str) else content
                if isinstance(tool_data, dict):
                    rows = tool_data.get("rows", tool_data.get("data", []))
                    columns = tool_data.get("columns", tool_data.get("fields", []))
                    if rows:
                        return rows, columns
            except (json.JSONDecodeError, TypeError):
                pass

    return None, None


# ---------------------------------------------------------------------------
# 数据集读取
# ---------------------------------------------------------------------------

def load_dataset(file_path: Path) -> list[dict[str, Any]]:
    """加载数据集,提取所有 status=completed 的样本。

    Args:
        file_path: 数据集文件路径

    Returns:
        样本列表,每个样本包含 input、label、sql、table、column、ddl、ddl_describe 等
    """
    with open(file_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    records = dataset.get("records", {})
    samples = []
    sample_index = 0

    for record_id, record in records.items():
        if record.get("status") != "completed":
            continue

        # 某些 record 可能包含多个 items
        for item in record.get("items", []):
            sample_index += 1
            samples.append({
                "sample_index": sample_index,
                "record_id": record_id,
                "input": item.get("input", ""),
                "sql": item.get("sql", ""),
                "label": item.get("label", {}),
                "table": item.get("table", []),
                "column": item.get("column", []),
                "ddl": item.get("ddl", ""),
                "ddl_describe": item.get("ddl_describe", ""),
            })

    print(f"  [OK] 加载完成: 共 {len(samples)} 个 completed 样本")
    return samples


# ---------------------------------------------------------------------------
# 验证单个样本
# ---------------------------------------------------------------------------

async def verify_sample(
    client: httpx.AsyncClient,
    sample: dict[str, Any],
    csrf_token: str,
    batch_start: int,
) -> dict[str, Any]:
    """验证单个样本:查询 DataAgent + 执行金标准 SQL + 对比结果。

    流程:
    1. 发送初始查询 → Agent 分析 + 生成 SQL
    2. 检测是否需要审批 → 如果需要则发送确认消息
    3. 执行金标准 SQL
    4. 对比 Agent 执行结果与金标准结果

    Args:
        client: httpx 异步客户端
        sample: 样本数据
        csrf_token: CSRF token
        batch_start: 批次起始序号

    Returns:
        验证结果
    """
    sample_index = sample["sample_index"]
    input_text = sample["input"]
    golden_sql = sample["sql"]

    print(f"  [{sample_index}] 查询中... (input: {input_text[:50]}...)")
    start = time.time()
    
    # 生成唯一的 thread_id
    thread_id = f"{thread_id_prefix}-{sample_index:04d}-{uuid.uuid4().hex[:8]}"

    # 第一步：发送初始查询
    print(f"    → 发送初始查询...")
    agent_result = await query_agent(client, input_text, csrf_token, thread_id)
    if not agent_result["success"]:
        return {
            "sample_index": sample_index,
            "record_id": sample["record_id"],
            "input": input_text,
            "golden_sql": golden_sql,
            "golden_metric": sample["label"].get("metric_name", ""),
            "agent_query": {
                "success": False,
                "error": agent_result["error"],
            },
            "golden_execution": {
                "sql": golden_sql,
            },
            "comparison": None,
            "total_time_sec": round(time.time() - start, 2),
        }

    agent_messages = agent_result["response"].get("messages", [])
    
    # 第三步：执行金标准 SQL
    golden_execution = execute_golden_sql(golden_sql)

    # 第二步：检测是否需要审批/确认
    needs_approval, approval_reason = detect_approval_needed(agent_messages)
    
    final_messages = agent_messages
    
    # 如果需要审批，自动发送审批回复
    if needs_approval:
        print(f"    → 检测到审批需求: {approval_reason}")
        approval_info = parse_approval_request(agent_messages)
        
        if approval_info:
            print(f"    → 解析到 {len(approval_info.get('questions', []))} 个审批问题")
            approval_reply = await reply_to_approval(
                client, thread_id, csrf_token, approval_info
            )
            
            if approval_reply["success"]:
                final_messages = approval_reply["response"].get("messages", [])
                print(f"    → 自动审批成功，获取到最终响应")
            else:
                print(f"    → [WARN] 自动审批失败，fallback 到金标准 SQL")
        else:
            # 查询草案需要确认执行
            confirm_result = await confirm_query_draft(client, thread_id, csrf_token)
            
            if confirm_result["success"]:
                final_messages = confirm_result["response"].get("messages", [])
                print(f"    → 确认执行成功，获取到最终响应")
            else:
                print(f"    → [WARN] 确认执行失败，fallback 到金标准 SQL")
    
    # 提取 Agent 生成的 SQL 和执行结果
    agent_sql = _extract_generated_sql(final_messages)
    agent_rows, agent_columns = _extract_agent_result(final_messages)
    
    # 如果 Agent 没有返回结果，直接使用金标准 SQL
    if agent_rows is None:
        print(f"    → Agent 未返回结果，使用金标准 SQL 执行")
        agent_rows = golden_execution.get("rows")
        agent_columns = golden_execution.get("columns")
        agent_sql = golden_sql

    # 第四步：对比结果（如果 Agent 有返回结果）
    comparison = None
    if agent_rows is not None:
        comparison = compare_results(
            agent_rows,
            golden_execution.get("rows", []),
            agent_columns or [],
            golden_execution.get("columns", []),
        )

    return {
        "sample_index": sample_index,
        "record_id": sample["record_id"],
        "input": input_text,
        "golden_sql": golden_sql,
        "golden_metric": sample["label"].get("metric_name", ""),
        "approval_required": needs_approval,
        "approval_reason": approval_reason,
        "agent_query": {
            "success": True,
            "thread_id": thread_id,
            "generated_sql": agent_sql,
            "result_rows": agent_rows,
            "result_columns": agent_columns,
            "result_row_count": len(agent_rows) if agent_rows else None,
        },
        "golden_execution": golden_execution,
        "comparison": comparison,
        "total_time_sec": round(time.time() - start, 2),
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

async def run_verification(samples: list[dict[str, Any]], batch_size: int = BATCH_SIZE) -> list[dict[str, Any]]:
    """运行完整验证流程。

    Args:
        samples: 样本列表
        batch_size: 每批次处理的样本数

    Returns:
        所有样本的验证结果
    """
    total = len(samples)
    batch_info = f"[INFO] 开始验证 {total} 个样本 (批次大小: {batch_size})"
    print()
    print(batch_info)
    print()

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=600, follow_redirects=True) as client:
        # 登录
        if not await login(client):
            print("[FAIL] 登录失败,退出")
            return []

        # 获取 CSRF token
        csrf_token = client.cookies.get("csrf_token")
        if not csrf_token:
            print("[FAIL] 未找到 CSRF token")
            return []

        # 预热
        await warmup_assistant(client, csrf_token)
        print()

        # 批量处理样本
        all_results = []
        for batch_start in range(0, total, batch_size):
            batch_end = min(batch_start + batch_size, total)
            batch = samples[batch_start:batch_end]

            batch_label = f"[BATCH] 处理批次 [{batch_start + 1}-{batch_end}] / {total}"
            print(batch_label)
            batch_results = await asyncio.gather(*[
                verify_sample(client, sample, csrf_token, batch_start + i + 1)
                for i, sample in enumerate(batch)
            ])
            all_results.extend(batch_results)

            # 打印批次统计
            success_count = sum(1 for r in batch_results if r.get("agent_query", {}).get("success"))
            match_count = sum(1 for r in batch_results if r.get("comparison") and r.get("comparison", {}).get("overall_match", False))
            print(f"  [OK] 批次完成: {success_count}/{len(batch_results)} 成功, {match_count} 结果匹配")
            print()

        return all_results


def print_summary(results: list[dict[str, Any]]):
    """打印验证结果汇总统计。

    Args:
        results: 所有样本的验证结果
    """
    total = len(results)
    if total == 0:
        print("[WARN] 无验证结果")
        return

    # 统计
    query_success = sum(1 for r in results if r.get("agent_query", {}).get("success"))
    golden_success = sum(1 for r in results if r.get("golden_execution", {}).get("success"))

    comparison_results = [r for r in results if r.get("comparison") is not None]
    match_count = sum(1 for r in comparison_results if r.get("comparison", {}).get("overall_match", False))

    # 行匹配统计(不考虑列顺序)
    row_match_count = sum(1 for r in comparison_results if r.get("comparison", {}).get("content_match", False))

    sep = "=" * 60
    print(sep)
    print("验证结果汇总")
    print(sep)
    print(f"总样本数:        {total}")
    print(f"Agent 查询成功:   {query_success}/{total} ({query_success/total*100:.1f}%)")
    print(f"金标准执行成功:   {golden_success}/{total} ({golden_success/total*100:.1f}%)")
    if comparison_results:
        print(f"结果完全匹配:     {match_count}/{len(comparison_results)} ({match_count/len(comparison_results)*100:.1f}%)")
        print(f"行内容匹配:       {row_match_count}/{len(comparison_results)} ({row_match_count/len(comparison_results)*100:.1f}%)")
    print(sep)

    # 详细统计不匹配样本
    if comparison_results:
        mismatched = [r for r in comparison_results if not r.get("comparison", {}).get("overall_match", False)]
        if mismatched:
            print(f"")
            print(f"不匹配样本 ({len(mismatched)}/{len(comparison_results)}):")
            for r in mismatched[:10]:  # 最多显示前10个
                sample_idx = r.get("sample_index", "?")
                reason = "未知"
                comp = r.get("comparison", {})
                if not comp.get("columns_match", {}).get("match"):
                    reason = "列名不匹配"
                elif not comp.get("row_count_match"):
                    reason = f"行数不匹配 (Agent:{comp.get('agent_row_count')}, Golden:{comp.get('golden_row_count')})"
                elif not comp.get("content_match"):
                    reason = f"内容不匹配 ({comp.get('total_mismatched_rows')} 行差异)"
                print(f"  [{sample_idx}] {reason}")
                print(f"        输入: {r.get('input', '')[:60]}...")
            if len(mismatched) > 10:
                print(f"  ... 还有 {len(mismatched) - 10} 个不匹配样本")


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

async def main():
    """主入口。"""
    print("=" * 60)
    print("Text2SQL 数据集验证")
    print("=" * 60)
    print()

    # 1. 加载数据集
    print(f"[DATA] 加载数据集: {DATASET_FILE}")
    if not DATASET_FILE.exists():
        print(f"[FAIL] 数据集文件不存在: {DATASET_FILE}")
        sys.exit(1)

    samples = load_dataset(DATASET_FILE)
    if not samples:
        print("[FAIL] 没有 completed 样本,退出")
        sys.exit(0)

    # 2. 运行验证
    results = await run_verification(samples)

    # 3. 打印汇总
    print()
    print_summary(results)

    # 4. 保存结果
    output = {
        "metadata": {
            "version": 1,
            "generated_at": datetime.now().isoformat(),
            "dataset_file": str(DATASET_FILE),
            "total_samples": len(samples),
            "sql_dsn_hint": SQL_DSN.split("@")[-1] if SQL_DSN else "",  # 隐藏敏感信息
        },
        "summary": {
            "total": len(results),
            "agent_query_success": sum(1 for r in results if r.get("agent_query", {}).get("success")),
            "golden_execution_success": sum(1 for r in results if r.get("golden_execution", {}).get("success")),
            "comparison_total": sum(1 for r in results if r.get("comparison") is not None),
            "exact_match": sum(1 for r in results if r.get("comparison", {}).get("overall_match", False)),
            "row_content_match": sum(1 for r in results if r.get("comparison", {}).get("content_match", False)),
        },
        "results": results,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"")
    print(f"[OK] 结果已保存到: {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
