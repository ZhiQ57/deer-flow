"""解析已保存的 MCP 原始响应，不读取评估答案或修改原始事件。"""

from __future__ import annotations

import json
import re

TOOLS = {
    "tree_folder",
    "search_sql_schema",
    "inspect_sql_schema",
    "vfs_bash_execute",
    "vfs_grep",
    "vfs_grep_file",
    "vfs_cat_read",
    "vfs_find_file",
    "vfs_sed",
    "sql_execute",
}
TABLE_PATH = re.compile(r"/(?:[^\s/|`\[\]()<>'\"]+/)*([^\s/|`\[\]()<>'\"]+)/table/([^\s/|`\[\]()<>'\"]+)(?=/|[\s|`\[\]()<>'\"]|$)")


def normalize_tool(name: str) -> str:
    """去掉宿主 MCP 前缀，保留明确的历史目录工具别名。

    Args:
        name: 基础工具名或 mcp__server__tool 原名。

    Returns:
        规范名称，未知工具保持原名以便拒绝。
    """
    if name.startswith("mcp__") and "__" in name[5:]:
        name = name.rsplit("__", 1)[-1]
    if name.startswith("vfssql_"):
        name = name[7:]
    return "tree_folder" if name == "vfs_tree_folder" else name


def response_payload(result: dict) -> dict:
    """从 MCP 或 CLI 包装提取业务字典，不丢弃外层原始记录。

    Args:
        result: MCP 原始结果字典。

    Returns:
        结构化业务结果；无法无歧义解码时返回空字典。
    """
    for key in ("structuredContent", "structured_content"):
        if isinstance(result.get(key), dict):
            return result[key]
    content = result.get("content")
    if isinstance(content, list):
        texts = [item.get("text") for item in content if isinstance(item, dict) and item.get("type") == "text"]
        if len(texts) == 1 and isinstance(texts[0], str):
            try:
                decoded = json.loads(texts[0])
                if isinstance(decoded, dict):
                    return decoded
            except ValueError:
                pass
        return {}
    return result


def discovered_tables(call: dict) -> list[str]:
    """提取一次成功检索实际暴露的候选，保持返回顺序。

    Args:
        call: 完整的工具请求与结果。

    Returns:
        带 Schema 的候选表序列；目录总览不充当相关检索。
    """
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
        return []
    if tool == "search_sql_schema":
        records = result.get("tables")
        return list(dict.fromkeys(f"{item.get('schema', '')}.{item['table']}".strip(".") for item in records if isinstance(item, dict) and isinstance(item.get("table"), str))) if isinstance(records, list) else []
    texts = []
    if tool in {"vfs_grep_file", "vfs_grep"}:
        matches = result.get("matches")
        for match in matches if isinstance(matches, list) else []:
            if isinstance(match, dict):
                texts.extend([match.get("path", ""), match.get("text", "")])
    elif tool == "vfs_find_file":
        texts.append(result.get("result_content", ""))
    elif tool in {"vfs_bash_execute", "vfs_cat_read", "vfs_sed", "inspect_sql_schema"}:
        if tool == "vfs_sed" and call["arguments"].get("operation") != "print_range":
            return []
        # 只有响应实际返回正文，才可将请求路径算作已暴露的表。
        body = result.get("content", result.get("result_content", ""))
        if isinstance(body, str) and body:
            texts.append(result.get("path") or result.get("file_path") or call["arguments"].get("file_path") or call["arguments"].get("path", ""))
            # index/find 文件正文中的公开表路径属于候选发现。
            texts.append(body)
    return list(dict.fromkeys(f"{match[0]}.{match[1]}" for value in texts if isinstance(value, str) for match in TABLE_PATH.findall(value)))
