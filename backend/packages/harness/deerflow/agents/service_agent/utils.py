from hashlib import sha256
import json


def _sha256_id(prefix: str, value: object) -> str:
    """生成带类型前缀的稳定摘要标识。"""
    digest = sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
    return f"{prefix}:sha256:{digest}"
