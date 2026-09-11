"""
快速测试 - 验证 DataAgent 确认流程 (仅测试前 3 个样本)
"""

import asyncio
import json
import sys
import importlib.util
from pathlib import Path
from datetime import datetime

# 动态导入主模块
spec = importlib.util.spec_from_file_location(
    "main_module",
    Path(__file__).parent / "text2sql-dataset-batch_synthesize.py"
)
main_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(main_module)

load_dataset = main_module.load_dataset
run_verification = main_module.run_verification
print_summary = main_module.print_summary

# 配置
OUTPUT_FILE = Path(__file__).parent / "test_verification_results.json"
TEST_LIMIT = 1  # 只测试 1 个样本

BATCH_SIZE = 1  # 逐条处理，便于调试


async def main():
    """主入口。"""
    print("=" * 70)
    print("DataAgent 确认流程测试 (前 3 个样本)")
    print("=" * 70)
    print()

    # 加载数据集
    dataset_path = Path(__file__).parent / "text2sql-dataset.json"
    print(f"加载数据集: {dataset_path}")
    
    if not dataset_path.exists():
        print(f"数据集文件不存在: {dataset_path}")
        return

    all_samples = load_dataset(dataset_path)
    
    # 只取前 TEST_LIMIT 个样本
    samples = all_samples[:TEST_LIMIT]
    print(f"测试样本数: {len(samples)}\n")
    
    if not samples:
        print("没有可测试的样本")
        return

    # 运行验证
    results = await run_verification(samples, batch_size=1)

    # 打印汇总
    print()
    print_summary(results)

    # 保存结果
    output = {
        "metadata": {
            "version": 1,
            "generated_at": datetime.now().isoformat(),
            "test_mode": True,
            "test_limit": TEST_LIMIT,
            "total_samples_in_dataset": len(all_samples),
        },
        "summary": {
            "total": len(results),
            "agent_query_success": sum(1 for r in results if r.get("agent_query", {}).get("success")),
            "needs_approval": sum(1 for r in results if r.get("needs_approval")),
            "golden_execution_success": sum(1 for r in results if r.get("golden_execution", {}).get("success")),
            "comparison_total": sum(1 for r in results if r.get("comparison") is not None),
            "exact_match": sum(1 for r in results if r.get("comparison") and r.get("comparison", {}).get("overall_match", False)),
            "row_content_match": sum(1 for r in results if r.get("comparison") and r.get("comparison", {}).get("content_match", False)),
        },
        "results": results,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n结果已保存到: {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
