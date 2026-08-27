#!/usr/bin/env python3
"""
基础个人敏感信息脱敏脚本。

用途：
- 对患者病历草稿中的手机号、邮箱、身份证号等做基础脱敏；
- 方便生成用于演示、测试、质控或模型评估的低敏版本。

注意：
- 这是规则匹配脚本，不保证覆盖所有隐私信息；
- 正式医疗系统仍需使用合规的数据脱敏和访问控制方案。

用法：
python redact_phi.py input.txt output.txt
"""

import re
import sys
from pathlib import Path

# 常见中国大陆手机号
PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")

# 常见邮箱
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# 简化身份证号匹配：15 位或 18 位，最后一位可为 X/x
ID_RE = re.compile(r"(?<![0-9A-Za-z])(\d{15}|\d{17}[0-9Xx])(?![0-9A-Za-z])")

# 可能的姓名标签：仅做基础替换，不覆盖所有姓名场景
NAME_LABEL_RE = re.compile(r"(姓名|患者姓名|名字)[:：]\s*[^\s，,；;。\n]+")


def redact_text(text: str) -> str:
    """返回脱敏后的文本。"""
    text = PHONE_RE.sub("[手机号已脱敏]", text)
    text = EMAIL_RE.sub("[邮箱已脱敏]", text)
    text = ID_RE.sub("[身份证号已脱敏]", text)
    text = NAME_LABEL_RE.sub(lambda m: m.group(1) + "：[姓名已脱敏]", text)
    return text


def main():
    if len(sys.argv) != 3:
        print("用法：python redact_phi.py input.txt output.txt")
        sys.exit(2)

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    if not input_path.exists():
        print(f"错误：文件不存在：{input_path}")
        sys.exit(2)

    text = input_path.read_text(encoding="utf-8")
    output_path.write_text(redact_text(text), encoding="utf-8")
    print(f"已生成脱敏文件：{output_path}")


if __name__ == "__main__":
    main()
