#!/usr/bin/env python3
"""把检前概率和似然比转换为检后概率。

中文说明：
- 本脚本是通用贝叶斯概率计算器，不是临床推荐引擎。
- 检前概率和似然比必须来自可靠来源或明确的临床假设。
- 计算结果只能辅助解释检查如何改变概率，不能单独决定诊断或治疗。
"""
import argparse


def normalize_probability(value: float) -> float:
    """把百分数或小数形式的检前概率统一转换为 0-1 概率。"""
    if value > 1:
        value = value / 100.0
    if not 0 < value < 1:
        raise ValueError("检前概率必须在 0 到 1 之间且不含端点，或以 0 到 100 的百分数输入且不含端点")
    return value


def posttest_probability(pretest_probability: float, likelihood_ratio: float) -> float:
    """使用 odds 形式完成检前概率到检后概率的转换。"""
    if likelihood_ratio < 0:
        raise ValueError("似然比不能为负数")
    pre = normalize_probability(pretest_probability)
    pre_odds = pre / (1 - pre)
    post_odds = pre_odds * likelihood_ratio
    return post_odds / (1 + post_odds)


def main() -> None:
    """命令行入口：读取检前概率和似然比并打印检后概率。"""
    parser = argparse.ArgumentParser(description="根据检前概率和似然比计算检后概率")
    parser.add_argument("--pretest", type=float, required=True, help="检前概率，可输入 0-1 小数或百分数")
    parser.add_argument("--lr", type=float, required=True, help="似然比")
    args = parser.parse_args()
    post = posttest_probability(args.pretest, args.lr)
    print(f"posttest_probability={post:.4f}  # 检后概率")
    print(f"posttest_percent={post * 100:.2f}%  # 检后概率百分比")


if __name__ == "__main__":
    main()
