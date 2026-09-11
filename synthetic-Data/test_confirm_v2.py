"""
确认流程测试 v2 — 测试发送确认消息后的完整流程
"""

import asyncio
import json
import uuid
import time
import re
from pathlib import Path
from typing import Any, Optional

import httpx

BASE_URL = "http://127.0.0.1:8001"
BASE_ASSISTANT_ID = "data-agent"
thread_id_prefix = "test-confirm-v2"
LOGIN_EMAIL = "wuzhiqi@user.com"
LOGIN_PASSWORD = "wuzhiqi@user.com"


async def login(client: httpx.AsyncClient) -> bool:
    resp = await client.post(
        "/api/v1/auth/login/local",
        data={"username": LOGIN_EMAIL, "password": LOGIN_PASSWORD, "remember_me": "on"},
    )
    if resp.status_code == 200:
        print("[OK] 登录成功")
        return True
    else:
        print(f"[FAIL] 登录失败: {resp.status_code}")
        return False


async def query_agent(client: httpx.AsyncClient, prompt: str, csrf_token: str, thread_id: str) -> dict:
    try:
        resp = await client.post(
            "/api/runs/wait",
            json={
                "assistant_id": BASE_ASSISTANT_ID,
                "config": {"configurable": {"thread_id": thread_id}},
                "input": {"messages": [{"role": "user", "content": prompt}]},
            },
            headers={"X-CSRF-Token": csrf_token, "Content-Type": "application/json"},
            timeout=600,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[ERROR] 查询失败: {e}")
        return {}


async def send_confirmation(client: httpx.AsyncClient, thread_id: str, csrf_token: str, 
                            confirm_msg: str = "确认执行") -> dict:
    """发送确认消息。"""
    try:
        print(f"  发送确认消息: {confirm_msg}")
        resp = await client.post(
            "/api/runs/wait",
            json={
                "assistant_id": BASE_ASSISTANT_ID,
                "config": {"configurable": {"thread_id": thread_id}},
                "input": {"messages": [{"role": "user", "content": confirm_msg}]},
            },
            headers={"X-CSRF-Token": csrf_token, "Content-Type": "application/json"},
            timeout=600,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[ERROR] 确认请求失败: {e}")
        return {}


def detect_approval_needed(messages: list[dict]) -> tuple[bool, Optional[str]]:
    """检测是否需要审批。"""
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
    return False, None


def analyze_messages(messages: list[dict]):
    """分析消息链。"""
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        msg_type = msg.get("type", "")
        name = msg.get("name", "") or ""
        
        if msg_type == "human":
            content = msg.get("content", "")
            content_str = content if isinstance(content, str) else "..."
            print(f"  [{i:2d}] HUMAN  {content_str[:60]}...")
        elif msg_type == "ai":
            tool_calls = msg.get("tool_calls", [])
            tc_names = [tc.get("name", "") if isinstance(tc, dict) else "?" for tc in tool_calls]
            if tc_names:
                print(f"  [{i:2d}] AI     → tools: {', '.join(tc_names)}")
        elif msg_type == "tool":
            if name:
                print(f"  [{i:2d}] TOOL   {name}")
        elif msg_type == "system":
            pass  # 跳过 system 消息


async def test_sample_full(client: httpx.AsyncClient, sample: dict, csrf_token: str) -> dict:
    """测试完整流程：查询 → 检测审批 → 发送确认 → 获取结果。"""
    sample_index = sample["sample_index"]
    input_text = sample["input"]
    golden_sql = sample["sql"]
    
    print(f"\n{'='*70}")
    print(f"样本 {sample_index}")
    print(f"输入: {input_text[:50]}...")
    print(f"{'='*70}")
    
    start = time.time()
    thread_id = f"{thread_id_prefix}-{sample_index:04d}-{uuid.uuid4().hex[:8]}"
    
    # 第一步：发送查询
    print("\n[步骤1] 发送初始查询...")
    first_response = await query_agent(client, input_text, csrf_token, thread_id)
    
    if not first_response:
        return {"success": False, "error": "first query failed"}
    
    first_messages = first_response.get("messages", [])
    print(f"  收到 {len(first_messages)} 条消息")
    analyze_messages(first_messages)
    
    # 检测是否需要审批
    needs_approval, approval_reason = detect_approval_needed(first_messages)
    
    second_messages = []
    second_response = None
    
    if needs_approval:
        print(f"\n  ⚠ 检测到需要审批: {approval_reason}")
        print(f"\n[步骤2] 发送确认消息...")
        
        # 发送确认
        second_response = await send_confirmation(client, thread_id, csrf_token, "请执行")
        
        if second_response:
            second_messages = second_response.get("messages", [])
            print(f"  确认后又收到 {len(second_messages)} 条消息")
            analyze_messages(second_messages)
            
            # 提取结果
            agent_rows, agent_columns = extract_result(second_messages)
            final_messages = second_messages
        else:
            agent_rows, agent_columns = None, None
            final_messages = first_messages
    else:
        print(f"\n  ✓ 无需审批")
        agent_rows, agent_columns = extract_result(first_messages)
        final_messages = first_messages
    
    elapsed = time.time() - start
    print(f"\n结果:")
    print(f"  耗时: {elapsed:.1f}s")
    print(f"  需要审批: {needs_approval}")
    print(f"  结果行数: {len(agent_rows) if agent_rows else 0}")
    print(f"  结果列名: {agent_columns[:5] if agent_columns else 'None'}")
    
    return {
        "success": True,
        "sample_index": sample_index,
        "input": input_text,
        "golden_sql": golden_sql,
        "needs_approval": needs_approval,
        "approval_reason": approval_reason,
        "first_message_count": len(first_messages),
        "second_message_count": len(second_messages) if second_response else 0,
        "agent_rows": agent_rows,
        "agent_columns": agent_columns,
        "elapsed_sec": round(elapsed, 2),
        "final_messages": final_messages,
    }


def extract_result(messages: list[dict]) -> tuple[Optional[list], Optional[list]]:
    """从消息链中提取执行结果。"""
    # 从最后一条 AI 消息提取
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("type") == "ai":
            content = msg.get("content", "")
            if isinstance(content, str):
                # 尝试 JSON
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
                # 尝试 Markdown 表格
                lines = content.strip().split('\n')
                table_rows = []
                table_cols = None
                in_table = False
                for line in lines:
                    if '|' in line and ('| 表' in line or '列' in line or line.strip().startswith('|')):
                        if not in_table:
                            in_table = True
                            parts = [p.strip() for p in line.split('|') if p.strip()]
                            if parts:
                                table_cols = parts
                        elif not line.strip().startswith('|---'):
                            parts = [p.strip() for p in line.split('|') if p.strip()]
                            if parts:
                                table_rows.append(parts)
                if table_rows and table_cols:
                    return table_rows, table_cols
    
    return None, None


async def warmup(client: httpx.AsyncClient, csrf_token: str):
    thread_id = f"{thread_id_prefix}-warmup-{uuid.uuid4().hex[:8]}"
    print("[预热] ...")
    try:
        resp = await client.post(
            "/api/runs/wait",
            json={"assistant_id": BASE_ASSISTANT_ID,
                  "config": {"configurable": {"thread_id": thread_id}},
                  "input": {"messages": [{"role": "user", "content": "ok"}]},
            },
            headers={"X-CSRF-Token": csrf_token, "Content-Type": "application/json"},
            timeout=300,
        )
        resp.raise_for_status()
        print("[预热] 完成")
    except Exception as e:
        print(f"[预热] 失败: {e}")


async def main():
    print("="*70)
    print("确认流程测试 v2")
    print("="*70)
    
    # 加载数据集
    dataset_path = Path("text2sql-dataset.json")
    with open(dataset_path, "r", encoding="utf-8") as f:
        dataset = json.load(f)
    
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
            if len(samples) >= 5:  # 测试5个样本
                break
        if len(samples) >= 5:
            break
    
    print(f"加载 {len(samples)} 个样本\n")
    
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=600, follow_redirects=True) as client:
        if not await login(client):
            return
        
        csrf_token = client.cookies.get("csrf_token")
        if not csrf_token:
            print("[FAIL] 未找到 CSRF token")
            return
        
        await warmup(client, csrf_token)
        
        results = []
        for sample in samples:
            result = await test_sample_full(client, sample, csrf_token)
            results.append(result)
        
        # 汇总
        print("\n" + "="*70)
        print("测试汇总")
        print("="*70)
        approval_count = sum(1 for r in results if r.get("needs_approval"))
        no_approval_count = sum(1 for r in results if not r.get("needs_approval"))
        print(f"需要审批: {approval_count}")
        print(f"无需审批: {no_approval_count}")
        
        for r in results:
            status = "需要审批" if r.get("needs_approval") else "无需审批"
            print(f"  样本 {r['sample_index']}: {status}, 耗时 {r.get('elapsed_sec', 0)}s")
        
        # 保存
        with open("test_results_v2.json", "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n结果已保存到 test_results_v2.json")


if __name__ == "__main__":
    asyncio.run(main())
