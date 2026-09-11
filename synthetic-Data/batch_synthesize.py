"""
批量数据合成脚本 — 输出 Hermes 格式，用于 Agent 模型训练

- assistant_id: data-agent（固定，指定目标智能体）
- thread_id: 每次合成动态生成 data-agent-{样本号}-{uuid}
- 使用 /runs/wait 等待运行完成
- 使用用户名密码登录认证
- 输出格式兼容 OpenHermes / distilabel 数据集

转换逻辑：
- thinking 标签提取
- tool_calls 拆分
- tool_response 提取
- 多模态图片处理
"""

import json
import re
import uuid
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


# ---------------------------------------------------------------------------
# 批量合成主函数
# ---------------------------------------------------------------------------

import asyncio
import httpx
import time
import json
from pathlib import Path


OUTPUT_FILE = Path(__file__).parent / "synthetic_results.json"
IMAGES_DIR = Path(__file__).parent / "images"
BASE_URL = "http://127.0.0.1:2026"  # Nginx HTTP 入口
BASE_ASSISTANT_ID = "data-agent"  # 目标智能体名称

thread_id_prefix = "data-agent-text2sql"   # Langfuse显示的Sessions标题.
LOGIN_EMAIL = "wuzhiqi@user.com"
LOGIN_PASSWORD = "wuzhiqi@user.com"
WARMUP_TIMEOUT = 300  # 预热请求超时时间（秒）
REQUEST_TIMEOUT = 600  # 常规请求超时时间（秒）


async def login(client: httpx.AsyncClient) -> bool:
    """使用用户名密码登录，自动保存 cookie"""
    resp = await client.post(
        "/api/v1/auth/login/local",
        data={
            "username": LOGIN_EMAIL,
            "password": LOGIN_PASSWORD,
            "remember_me": "on",
        }
    )
    if resp.status_code == 200:
        print("✓ 登录成功")
        return True
    else:
        print(f"✗ 登录失败: {resp.status_code} - {resp.text}")
        return False


async def warmup_assistant(client: httpx.AsyncClient, csrf_token: str, assistant_id: str) -> bool:
    """预热助手：发送一个极简请求，触发 graph 编译和工具加载。

    首次调用 data-agent 时，LangGraph 需要编译图结构、加载工具、初始化模型，
    这个过程可能超过 nginx 的 60s 超时，导致第一个样本 504。
    预热请求提前完成这些初始化，后续请求会快很多。
    """
    warmup_thread = f"warmup-{uuid.uuid4().hex[:8]}"
    print(f"  → 预热助手（触发图编译和工具加载）...")
    try:
        resp = await client.post(
            "/api/runs/wait",
            json={
                "assistant_id": assistant_id,
                "config": {
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
        print(f"  → 预热完成 (耗时 {time.time():.1f}s，消息数: {msg_count})")
        return True
    except Exception as e:
        print(f"  → 预热失败（非阻塞）: {e}")
        return False


async def run_synthesis_wait(client: httpx.AsyncClient, prompt: str, csrf_token: str, thread_id: str) -> dict:
    """使用 /runs/wait 等待运行完成（使用指定 thread_id 创建独立对话）"""
    print(f"  → 开始请求 (thread_id: {thread_id})...")
    resp = await client.post(
        "/api/runs/wait",
        json={
            "assistant_id": BASE_ASSISTANT_ID,
            "config": {
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
    print(f"  → 收到响应 ({len(json.dumps(data))} 字节，消息数: {len(data.get('messages', []))})")
    return data


async def batch_synthesize(prompts: list[str], base_assistant_id: str = None) -> list[dict]:
    """批量合成数据，输出 Hermes 格式用于 Agent 模型训练。

    thread_id 每次合成动态生成：data-agent-{样本号}-{uuid}
    便于在 Langfuse 中独立追踪每个样本的执行过程。
    """
    if base_assistant_id is None:
        base_assistant_id = BASE_ASSISTANT_ID
    
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=600, follow_redirects=True) as client:
        # 登录
        if not await login(client):
            return []
        
        # 获取 CSRF token
        csrf_token = client.cookies.get("csrf_token")
        if not csrf_token:
            print("✗ 未找到 CSRF token，登录可能未完全生效")
            return []
        
        # 创建 images 目录
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        
        # 预热助手（触发图编译和工具加载，避免第一个样本 504）
        await warmup_assistant(client, csrf_token, BASE_ASSISTANT_ID)
        print()  # 空行分隔预热和正式合成
        
        results = []
        for i, prompt in enumerate(prompts, 1):
            # 生成唯一 thread_id，格式：data-agent-{样本号}-{uuid}
            thread_id = f"{thread_id_prefix}-{i:04d}-{uuid.uuid4().hex[:8]}"
            print(f"🔄 [{i}/{len(prompts)}] 合成中... (thread_id: {thread_id})")
            try:
                result = await run_synthesis_wait(client, prompt, csrf_token, thread_id)
                
                # 转换为 Hermes 格式
                hermes_record = _convert_to_hermes(result)
                
                results.append({
                    "status": "success",
                    "thread_id": thread_id,
                    **hermes_record,
                })
            except Exception as e:
                results.append({
                    "status": "error",
                    "thread_id": thread_id,
                    "error": str(e),
                })
        
        # 保存结果
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        
        print(f"✓ 完成! 结果保存到: {OUTPUT_FILE}")
        return results


if __name__ == "__main__":
    prompts = [
        "生成 10 条关于人工智能的摘要",
        "生成 5 个电商产品评论",
        "生成 3 段技术文档内容",
    ]
    
    asyncio.run(batch_synthesize(prompts))
