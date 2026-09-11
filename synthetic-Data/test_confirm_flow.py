"""
快速测试脚本 — 测试 DataAgent 的确认流程和消息提取

只测试前3个样本，验证流程正确后移除这个文件。
"""

import asyncio
import json
import uuid
import time
import re
from pathlib import Path
from typing import Any, Optional

import httpx

# 配置
BASE_URL = "http://127.0.0.1:2026"
BASE_ASSISTANT_ID = "data-agent"
thread_id_prefix = "test-confirm-flow"
LOGIN_EMAIL = "wuzhiqi@user.com"
LOGIN_PASSWORD = "wuzhiqi@user.com"


async def login(client: httpx.AsyncClient) -> bool:
    """使用用户名密码登录。"""
    resp = await client.post(
        "/api/v1/auth/login/local",
        data={
            "username": LOGIN_EMAIL,
            "password": LOGIN_PASSWORD,
            "remember_me": "on",
        }
    )
    if resp.status_code == 200:
        print("[OK] 登录成功")
        return True
    else:
        print(f"[FAIL] 登录失败: {resp.status_code}")
        return False


async def query_agent(client: httpx.AsyncClient, prompt: str, csrf_token: str, thread_id: str) -> dict:
    """发送查询请求。"""
    try:
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
            timeout=600,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[ERROR] 查询失败: {e}")
        return {}


async def confirm_and_query(client: httpx.AsyncClient, prompt: str, csrf_token: str, thread_id: str) -> dict:
    """发送确认指令并等待结果。"""
    try:
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
            timeout=600,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[ERROR] 确认请求失败: {e}")
        return {}


def analyze_messages(messages: list[dict], indent: str = ""):
    """分析消息链，打印关键信息。"""
    print(f"{indent}=== 消息链分析 ({len(messages)} 条消息) ===")
    
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        
        msg_type = msg.get("type", "")
        content = msg.get("content", "")
        
        # 只打印关键信息
        if msg_type == "human":
            content_str = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)[:200]
            print(f"{indent}  [{i}] HUMAN: {content_str[:100]}...")
            
        elif msg_type == "ai":
            # 提取 thinking
            thinking = ""
            if isinstance(content, str) and "<think" in content:
                thinking_match = re.search(r'<think[^>]*>(.*?)\n</think>', content, re.DOTALL)
                if thinking_match:
                    thinking = thinking_match.group(1).strip()[:100]
            
            # 提取 tool_calls
            tool_calls = msg.get("tool_calls", [])
            
            preview = content[:100] if isinstance(content, str) else ""
            if thinking:
                print(f"{indent}  [{i}] AI: [thinking] {thinking}...")
            if tool_calls:
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        func = tc.get("function", {})
                        name = func.get("name", "")
                        args = func.get("arguments", {})
                        args_str = json.dumps(args, ensure_ascii=False)[:100] if isinstance(args, (dict, list)) else str(args)[:100]
                        print(f"{indent}      → TOOL_CALL: {name}({args_str})")
            if not thinking and not tool_calls:
                print(f"{indent}  [{i}] AI: {preview}...")
                
        elif msg_type == "tool":
            content_str = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)[:200]
            print(f"{indent}  [{i}] TOOL: {content_str[:150]}...")
            
        elif msg_type == "system":
            print(f"{indent}  [{i}] SYSTEM: {content[:100]}...")
    
    print()


async def test_sample(client: httpx.AsyncClient, sample: dict, csrf_token: str) -> dict:
    """测试单个样本的完整流程。"""
    sample_index = sample["sample_index"]
    input_text = sample["input"]
    golden_sql = sample["sql"]
    
    print(f"\n{'='*70}")
    print(f"样本 {sample_index}")
    print(f"输入: {input_text[:60]}...")
    print(f"{'='*70}")
    
    start = time.time()
    thread_id = f"{thread_id_prefix}-{sample_index:04d}-{uuid.uuid4().hex[:8]}"
    
    # 第一步：发送查询
    print("\n[步骤1] 发送初始查询...")
    first_response = await query_agent(client, input_text, csrf_token, thread_id)
    
    if not first_response:
        return {"success": False, "error": "first query failed"}
    
    first_messages = first_response.get("messages", [])
    print(f"  收到 {len(first_messages)} 条消息\n")
    analyze_messages(first_messages)
    
    # 检查是否需要确认 - 查找是否有需要用户确认的信号
    needs_confirm = False
    confirm_prompt = None
    
    for msg in first_messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("type") == "ai":
            content = msg.get("content", "")
            if isinstance(content, str):
                # 检查是否有确认请求的信号
                confirm_signals = [
                    "确认", "execute", "执行", "是否", "请确认", "confirmation",
                    "should I", "ready to", "准备", "同意"
                ]
                for signal in confirm_signals:
                    if signal in content.lower():
                        needs_confirm = True
                        confirm_prompt = f"请确认是否执行此SQL: {content[:200]}"
                        break
    
    # 如果没有找到明确的确认信号，尝试发送简单的确认
    if needs_confirm:
        print(f"\n[步骤2] 需要确认，发送确认指令...")
        second_response = await confirm_and_query(client, "请执行", csrf_token, thread_id)
        if second_response:
            second_messages = second_response.get("messages", [])
            print(f"  确认后又收到 {len(second_messages)} 条消息\n")
            analyze_messages(second_messages)
            
            # 合并两轮消息
            all_messages = first_messages + second_messages
            final_messages = second_messages
        else:
            all_messages = first_messages
            final_messages = first_messages
    else:
        print(f"\n[步骤2] 未检测到确认请求，使用首次响应")
        all_messages = first_messages
        final_messages = first_messages
    
    # 尝试从最终消息中提取结果
    agent_rows, agent_columns = extract_agent_result(final_messages)
    generated_sql = extract_generated_sql(all_messages)
    
    print(f"\n提取结果:")
    print(f"  生成SQL: {generated_sql[:100] if generated_sql else 'None'}")
    print(f"  结果行数: {len(agent_rows) if agent_rows else 0}")
    print(f"  结果列名: {agent_columns[:5] if agent_columns else 'None'}...")
    
    elapsed = time.time() - start
    print(f"\n总耗时: {elapsed:.1f}s")
    
    return {
        "success": True,
        "sample_index": sample_index,
        "input": input_text,
        "golden_sql": golden_sql,
        "needs_confirm": needs_confirm,
        "first_response_count": len(first_messages),
        "final_response_count": len(final_messages) if final_messages else 0,
        "generated_sql": generated_sql,
        "agent_rows": agent_rows,
        "agent_columns": agent_columns,
        "all_messages": all_messages,  # 保存完整消息链供分析
    }


def extract_generated_sql(messages: list[dict]) -> Optional[str]:
    """从消息链中提取生成的 SQL。"""
    # 1. 从 tool_calls 中提取
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("type") == "ai":
            for tc in msg.get("tool_calls", []):
                if isinstance(tc, dict):
                    func = tc.get("function", {})
                    name = func.get("name", "")
                    args = func.get("arguments", {})
                    if "sql" in name.lower():
                        if isinstance(args, dict) and "sql" in args:
                            return args["sql"]
    
    # 2. 从文本中提取 SELECT 语句
    sql_pattern = re.compile(r"(SELECT\s+.+?;?\s*$)", re.IGNORECASE | re.DOTALL)
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("type") in ("ai", "human"):
            content = msg.get("content", "")
            if isinstance(content, str):
                match = sql_pattern.search(content)
                if match and match.group(1).strip().upper().startswith("SELECT"):
                    return match.group(1).strip()
    
    return None


def extract_agent_result(messages: list[dict]) -> tuple[Optional[list], Optional[list]]:
    """从消息链中提取执行结果。"""
    # 1. 从最后一条 AI 消息提取
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("type") == "ai":
            content = msg.get("content", "")
            if isinstance(content, str):
                # 尝试解析 JSON
                json_pattern = re.compile(r'\{[^{}]*"rows"[^{}]*\}', re.DOTALL)
                match = json_pattern.search(content)
                if match:
                    try:
                        data = json.loads(match.group(0))
                        rows = data.get("rows", [])
                        cols = data.get("columns", [])
                        if rows:
                            return rows, cols
                    except:
                        pass
    
    return None, None


async def warmup(client: httpx.AsyncClient, csrf_token: str):
    """预热助手。"""
    thread_id = f"{thread_id_prefix}-warmup-{uuid.uuid4().hex[:8]}"
    print("[预热] 触发图编译和工具加载...")
    try:
        resp = await client.post(
            "/api/runs/wait",
            json={
                "assistant_id": BASE_ASSISTANT_ID,
                "config": {"configurable": {"thread_id": thread_id}},
                "input": {"messages": [{"role": "user", "content": "ok"}]},
            },
            headers={"X-CSRF-Token": csrf_token, "Content-Type": "application/json"},
            timeout=300,
        )
        resp.raise_for_status()
        print("[预热] 完成")
    except Exception as e:
        print(f"[预热] 失败（非阻塞）: {e}")


async def main():
    """主入口。"""
    print("="*70)
    print("DataAgent 确认流程测试")
    print("="*70)
    
    # 加载数据集
    dataset_path = Path("text2sql-dataset.json")
    with open(dataset_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)
    
    # 获取前3个 completed 样本
    samples = []
    for record_id, record in dataset.get("records", {}).items():
        if record.get("status") != "completed":
            continue
        for item in record.get("items", []):
            samples.append({
                "sample_index": len(samples) + 1,
                "record_id": record_id,
                "input": item.get("input", ""),
                "sql": item.get("sql", ""),
            })
            if len(samples) >= 3:
                break
        if len(samples) >= 3:
            break
    
    print(f"\n共加载 {len(samples)} 个样本\n")
    
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=600, follow_redirects=True) as client:
        # 登录
        if not await login(client):
            return
        
        csrf_token = client.cookies.get("csrf_token")
        if not csrf_token:
            print("[FAIL] 未找到 CSRF token")
            return
        
        # 预热
        await warmup(client, csrf_token)
        
        # 测试样本
        results = []
        for sample in samples:
            result = await test_sample(client, sample, csrf_token)
            results.append(result)
        
        # 汇总
        print("\n" + "="*70)
        print("测试汇总")
        print("="*70)
        for r in results:
            if r.get("success"):
                print(f"样本 {r['sample_index']}: "
                      f"需要确认={r.get('needs_confirm', False)}, "
                      f"首次消息={r.get('first_response_count', 0)}, "
                      f"最终消息={r.get('final_response_count', 0)}")
        
        # 保存完整消息链供分析
        with open("test_messages.json", "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n完整消息已保存到 test_messages.json")


if __name__ == "__main__":
    asyncio.run(main())
