#!/usr/bin/env python3
"""
结构化病历 JSON 基础校验脚本。

用途：
- 检查问诊采集生成的 JSON 是否包含核心字段；
- 提醒哪些字段缺失；
- 不做医学诊断、不判断治疗方案是否正确。

用法：
python validate_medical_record.py record.json
"""

import json
import sys
from pathlib import Path

# 核心字段：这些字段用于保证病历草稿具有最基本的临床可读性。
REQUIRED_FIELDS = [
    "record_type",
    "patient",
    "chief_complaint",
    "history_of_present_illness",
    "red_flags",
    "clinician_review_focus",
    "limitations",
]

# 建议字段：缺失时不一定失败，但会降低病历完整性。
RECOMMENDED_FIELDS = [
    "associated_symptoms",
    "pertinent_negatives",
    "past_medical_history",
    "medications",
    "allergies",
    "objective_data",
    "patient_facing_summary",
]


def load_json(path: Path):
    """读取 JSON 文件。"""
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def validate(data: dict):
    """返回缺失的必需字段和建议字段。"""
    missing_required = [field for field in REQUIRED_FIELDS if field not in data]
    missing_recommended = [field for field in RECOMMENDED_FIELDS if field not in data]
    return missing_required, missing_recommended


def main():
    if len(sys.argv) != 2:
        print("用法：python validate_medical_record.py record.json")
        sys.exit(2)

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"错误：文件不存在：{path}")
        sys.exit(2)

    try:
        data = load_json(path)
    except json.JSONDecodeError as exc:
        print(f"错误：JSON 格式无效：{exc}")
        sys.exit(1)

    if not isinstance(data, dict):
        print("错误：顶层 JSON 必须是对象。")
        sys.exit(1)

    missing_required, missing_recommended = validate(data)

    if missing_required:
        print("校验未通过：缺少必需字段。")
        for field in missing_required:
            print(f"- {field}")
        sys.exit(1)

    print("校验通过：核心字段完整。")
    if missing_recommended:
        print("建议补充以下字段：")
        for field in missing_recommended:
            print(f"- {field}")
    else:
        print("建议字段也已完整。")


if __name__ == "__main__":
    main()
