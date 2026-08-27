#!/usr/bin/env python3
"""根据发表日期粗略判断指南或证据是否可能过时。

中文说明：
- 本脚本不会联网，也不会验证指南是否已有新版。
- 它只根据用户提供的日期和阈值做时效性提醒。
- 医疗指南是否仍然适用，必须人工查验最新版本和本地规范。
"""
import argparse
from datetime import date, datetime


def parse_date(text: str) -> date:
    """支持 YYYY、YYYY-MM 或 YYYY-MM-DD 三种日期格式。"""
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.date()
        except ValueError:
            pass
    raise ValueError("日期格式必须是 YYYY、YYYY-MM 或 YYYY-MM-DD")


def age_years(published: date, as_of: date) -> float:
    """计算来源从发表日至参考日期的大致年数。"""
    return (as_of - published).days / 365.25


def main() -> None:
    """命令行入口：输出来源年龄和是否需要更新核查。"""
    parser = argparse.ArgumentParser(description="根据日期检查指南或证据来源是否可能过时")
    parser.add_argument("--published", required=True, help="发表日期：YYYY、YYYY-MM 或 YYYY-MM-DD")
    parser.add_argument("--as-of", default=date.today().isoformat(), help="参考日期，默认今天，格式 YYYY-MM-DD")
    parser.add_argument("--max-years", type=float, default=5.0, help="时效性阈值，单位：年")
    args = parser.parse_args()
    published = parse_date(args.published)
    as_of = parse_date(args.as_of)
    age = age_years(published, as_of)
    status = "fresh_or_current_enough  # 日期层面暂未超过阈值" if age <= args.max_years else "stale_check_current_guideline  # 已超过阈值，需核查最新版"
    print(f"published={published.isoformat()}  # 发表日期")
    print(f"as_of={as_of.isoformat()}  # 参考日期")
    print(f"age_years={age:.2f}  # 距今约多少年")
    print(f"threshold_years={args.max_years:.2f}  # 设定阈值")
    print(f"status={status}")


if __name__ == "__main__":
    main()
