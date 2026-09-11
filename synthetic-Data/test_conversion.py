"""
验证转换逻辑是否正确工作（与 batch_synthesize.py 逻辑一致）。
"""

import json
import re
from typing import Any


_THINKING_RE = re.compile(r"<think[^>]*>(.*?)</think>", re.DOTALL)


def _extract_thinking(content: str):
    """将 content 拆分为 (thinking, answer)。"""
    if not content:
        return None, None
    m = _THINKING_RE.search(content)
    if m:
        thinking = m.group(1).strip()
        before = content[: m.start()].rstrip()
        after = content[m.end():].lstrip()
        answer = (before + after).strip() if before or after else ""
        return thinking if thinking else None, answer if answer else None
    return None, content


def _tool_calls_to_messages(ai_msg: dict) -> list[dict]:
    """将 AIMessage 的 tool_calls 拆分为 tool_call 消息。"""
    tool_calls = ai_msg.get("tool_calls") or ai_msg.get("tool_call") or []
    if not tool_calls:
        return []

    normalized = []
    for tc in tool_calls:
        if isinstance(tc, str):
            try:
                tc = json.loads(tc)
            except json.JSONDecodeError:
                continue
        if isinstance(tc, dict):
            func = tc.get("function") or tc.get("tool") or {}
            if not isinstance(func, dict):
                func = {}
            normalized.append({
                "id": tc.get("id", ""),
                "name": func.get("name", tc.get("name", "")),
                "arguments": func.get("arguments", tc.get("arguments", "{}")),
            })

    messages = []
    for tc in normalized:
        args = tc["arguments"]
        if isinstance(args, dict):
            args = json.dumps(args, ensure_ascii=False)
        messages.append({
            "role": "tool_call",
            "content": json.dumps({
                "name": tc["name"],
                "arguments": json.loads(args) if isinstance(args, str) else args,
            }, ensure_ascii=False),
        })
    return messages


def _extract_tools_from_messages(messages: list[dict]) -> list[dict]:
    """从完整消息链中推断工具定义。"""
    seen_tools: dict[str, dict] = {}
    for msg in messages:
        if msg.get("role") == "tool_call":
            try:
                tc = json.loads(msg["content"])
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(tc, dict):
                continue
            name = tc.get("name", "")
            if not name or name in seen_tools:
                continue
            args = tc.get("arguments", {})
            properties: dict[str, Any] = {}
            required: list[str] = []
            if isinstance(args, dict):
                for k, v in args.items():
                    if v is None:
                        continue
                    if isinstance(v, int):
                        properties[k] = {"type": "integer", "description": k}
                    elif isinstance(v, float):
                        properties[k] = {"type": "number", "description": k}
                    elif isinstance(v, bool):
                        properties[k] = {"type": "boolean", "description": k}
                    else:
                        properties[k] = {"type": "string", "description": k}
                    required.append(k)
            seen_tools[name] = {
                "type": "function",
                "function": {
                    "name": name,
                    "description": f"调用 {name} 工具",
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            }
    return list(seen_tools.values())


def _format_thinking(thinking: str, answer: str = None) -> str:
    """将 thinking 格式化为完整文本。"""
    result = "\n<thinking>\n" + thinking + "\n</think>\n"
    if answer:
        result += answer
    return result


def _convert_to_hermes(deerflow_result: dict) -> dict:
    """将 /runs/wait 返回的 DeerFlow 结果转换为 Hermes 数据集格式。"""
    df_messages = deerflow_result.get("messages", [])
    if not df_messages:
        return {"tools": [], "messages": [], "images": []}

    hermes_messages: list[dict] = []
    all_images: list[str] = []

    for msg in df_messages:
        if not isinstance(msg, dict):
            continue

        msg_type = msg.get("type", "")
        msg_content = msg.get("content", "")

        if msg_type == "human":
            hermes_messages.append({
                "role": "user",
                "content": msg_content if isinstance(msg_content, str) else json.dumps(msg_content, ensure_ascii=False),
            })

        elif msg_type == "ai":
            thinking, answer = _extract_thinking(msg_content if isinstance(msg_content, str) else "")
            tool_call_msgs = _tool_calls_to_messages(msg)

            if tool_call_msgs:
                # 有工具调用：thinking + answer 应在 tool_call 之前
                if thinking:
                    hermes_messages.append({
                        "role": "assistant",
                        "content": _format_thinking(thinking, None),
                    })
                if answer:
                    hermes_messages.append({
                        "role": "assistant",
                        "content": answer,
                    })
                # 追加 tool_call 消息
                hermes_messages.extend(tool_call_msgs)
            else:
                # 无工具调用：正常 assistant 消息
                if thinking:
                    hermes_messages.append({
                        "role": "assistant",
                        "content": _format_thinking(thinking, answer),
                    })
                elif answer:
                    hermes_messages.append({
                        "role": "assistant",
                        "content": answer,
                    })

        elif msg_type == "tool":
            tool_content = msg_content if isinstance(msg_content, str) else json.dumps(msg_content, ensure_ascii=False)
            hermes_messages.append({
                "role": "tool_response",
                "content": tool_content,
            })

        elif msg_type == "system":
            sys_content = msg_content if isinstance(msg_content, str) else json.dumps(msg_content, ensure_ascii=False)
            hermes_messages.append({
                "role": "system",
                "content": sys_content,
            })

    tools = _extract_tools_from_messages(hermes_messages)

    return {
        "tools": tools,
        "messages": hermes_messages,
        "images": all_images if all_images else None,
    }


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------

def test_extract_thinking():
    """测试 thinking 标签提取"""
    content = "让我思考一下。\n<thinking>\n首先分析用户需求，然后生成数据...\n</think>\n好的，以下是结果..."
    thinking, answer = _extract_thinking(content)
    assert thinking is not None, "thinking 不应为 None"
    assert "首先分析" in thinking
    assert answer is not None
    assert "以下是结果" in answer
    print("✓ test_extract_thinking 通过")


def test_tool_calls_to_messages():
    """测试 tool_calls 拆分"""
    ai_msg = {
        "type": "ai",
        "content": "我需要调用工具...",
        "tool_calls": [
            {
                "id": "call_123",
                "function": {
                    "name": "bash",
                    "arguments": {"command": "ls -la", "timeout": 60}
                }
            }
        ]
    }
    tool_msgs = _tool_calls_to_messages(ai_msg)
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["role"] == "tool_call"
    tc = json.loads(tool_msgs[0]["content"])
    assert tc["name"] == "bash"
    assert tc["arguments"]["command"] == "ls -la"
    print("✓ test_tool_calls_to_messages 通过")


def test_convert_to_hermes():
    """测试完整转换流程"""
    deerflow_result = {
        "messages": [
            {
                "type": "human",
                "content": "帮我执行 ls 命令"
            },
            {
                "type": "ai",
                "content": "让我思考一下。\n<thinking>\n用户需要执行 ls 命令，我应该调用 bash 工具。\n</think>",
                "tool_calls": [
                    {
                        "id": "call_123",
                        "function": {
                            "name": "bash",
                            "arguments": {"command": "ls -la", "timeout": 60}
                        }
                    }
                ]
            },
            {
                "type": "tool",
                "name": "bash",
                "content": "total 12\ndrwxr-xr-x  5 user  staff  160 Jan 10 10:00 .\n-rw-r--r--  1 user  staff  230 Jan 10 10:00 README.md"
            },
            {
                "type": "ai",
                "content": "执行成功，当前目录包含以下文件：..."
            }
        ]
    }
    
    hermes = _convert_to_hermes(deerflow_result)
    
    # 检查 messages 结构
    assert len(hermes["messages"]) >= 4, f"应有至少 4 条消息，实际: {len(hermes['messages'])}"
    
    # 检查 role 序列
    roles = [m["role"] for m in hermes["messages"]]
    assert "user" in roles
    assert "tool_call" in roles
    assert "tool_response" in roles
    assert "assistant" in roles
    
    # 检查 tools 提取
    assert len(hermes["tools"]) > 0
    assert hermes["tools"][0]["function"]["name"] == "bash"
    
    # 验证消息顺序：user -> assistant(thinking) -> assistant(answer) -> tool_call -> tool_response -> assistant
    expected_roles = ["user", "assistant", "assistant", "tool_call", "tool_response", "assistant"]
    assert roles == expected_roles, f"消息角色顺序不正确: {roles}, 期望: {expected_roles}"
    
    # 验证 thinking 内容正确
    thinking_msg = [m for m in hermes["messages"] if m["role"] == "assistant" and "<thinking" in m["content"]][0]
    assert "用户需要执行" in thinking_msg["content"], f"thinking 内容不正确: {thinking_msg['content']}"
    
    print("✓ test_convert_to_hermes 通过")
    print(f"  - 生成 {len(hermes['messages'])} 条消息")
    print(f"  - 提取 {len(hermes['tools'])} 个工具定义")
    print(f"  - 消息角色序列: {roles}")
    
    # 打印实际输出用于验证
    print("\n转换后的 Hermes 格式示例:")
    print(json.dumps(hermes, ensure_ascii=False, indent=2))


def test_multimodal():
    """测试多模态图片处理（不实际保存，只检查逻辑）"""
    deerflow_result = {
        "messages": [
            {
                "type": "human",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,iVBORw0KGgo="
                        }
                    },
                    {
                        "type": "text",
                        "text": "这是什么图片？"
                    }
                ]
            }
        ]
    }
    
    hermes = _convert_to_hermes(deerflow_result)
    assert len(hermes["messages"]) == 1
    assert hermes["messages"][0]["role"] == "user"
    print("✓ test_multimodal 通过")


if __name__ == "__main__":
    print("=" * 50)
    print("运行转换逻辑测试...")
    print("=" * 50)
    
    test_extract_thinking()
    test_tool_calls_to_messages()
    test_convert_to_hermes()
    test_multimodal()
    
    print("=" * 50)
    print("所有测试通过! ✓")
    print("=" * 50)
