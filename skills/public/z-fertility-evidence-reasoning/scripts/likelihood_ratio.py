#!/usr/bin/env python3
"""根据敏感度和特异度计算诊断似然比。

中文说明：
- 本脚本是通用计算器，不是临床推荐引擎。
- 只有当敏感度和特异度来自可靠来源时，才应使用。
- 输出结果需要由医生结合具体患者、检查适用性和指南解释。
"""
import argparse


def bounded_probability(value: float, name: str) -> float:
    """把 0-100 的百分数或 0-1 的小数统一转换为 0-1 概率。"""
    if value > 1:
        value = value / 100.0
    if not 0 <= value <= 1:
        raise ValueError(f"{name} 必须在 0 到 1 之间，或以 0 到 100 的百分数输入")
    return value


def calculate(sensitivity: float, specificity: float) -> dict:
    """计算阳性似然比 LR+ 和阴性似然比 LR-。"""
    sens = bounded_probability(sensitivity, "sensitivity")
    spec = bounded_probability(specificity, "specificity")
    lr_positive = None if spec == 1 else sens / (1 - spec)
    lr_negative = None if spec == 0 else (1 - sens) / spec
    return {
        "sensitivity": sens,
        "specificity": spec,
        "lr_positive": lr_positive,
        "lr_negative": lr_negative,
    }


def main() -> None:
    """命令行入口：读取敏感度和特异度并打印计算结果。"""
    parser = argparse.ArgumentParser(description="根据敏感度和特异度计算 LR+ 与 LR-")
    parser.add_argument("--sensitivity", type=float, required=True, help="敏感度，可输入 0-1 小数或百分数")
    parser.add_argument("--specificity", type=float, required=True, help="特异度，可输入 0-1 小数或百分数")
    args = parser.parse_args()
    result = calculate(args.sensitivity, args.specificity)
    print(f"sensitivity={result['sensitivity']:.4f}  # 敏感度")
    print(f"specificity={result['specificity']:.4f}  # 特异度")
    print("lr_positive=" + ("undefined  # 当特异度为 1 时无法计算" if result["lr_positive"] is None else f"{result['lr_positive']:.4f}  # 阳性似然比"))
    print("lr_negative=" + ("undefined  # 当特异度为 0 时无法计算" if result["lr_negative"] is None else f"{result['lr_negative']:.4f}  # 阴性似然比"))


if __name__ == "__main__":
    main()
