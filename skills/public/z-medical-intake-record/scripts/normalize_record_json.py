#!/usr/bin/env python3
"""
自由文本病历草稿 JSON 外壳生成脚本。

用途：
- 当模型只生成了 Markdown 或自由文本病历时，将其包裹为统一 JSON 外壳；
- 方便下游系统先存储，再由人工或后续模型进一步结构化。

注意：
- 该脚本不抽取医学字段；
- 不做诊断、不做医学判断。

用法：
python normalize_record_json.py note.md record.json
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def normalize(note_text: str) -> dict:
    """生成统一 JSON 外壳。"""
    return {
        "record_type": "patient_intake_record",
        "language": "zh-CN",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_format": "free_text",
        "note_text": note_text,
        "limitations": [
            "该记录由自由文本病历草稿包裹生成，尚未完成字段级结构化。",
            "该记录不能替代医生诊断、处方或治疗建议。"
        ]
    }


def main():
    if len(sys.argv) != 3:
        print("用法：python normalize_record_json.py note.md record.json")
        sys.exit(2)

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])

    if not input_path.exists():
        print(f"错误：文件不存在：{input_path}")
        sys.exit(2)

    note_text = input_path.read_text(encoding="utf-8")
    data = normalize(note_text)
    output_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已生成 JSON 文件：{output_path}")


if __name__ == "__main__":
    main()
