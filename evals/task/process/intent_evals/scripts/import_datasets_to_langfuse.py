#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""意图识别评测数据集导入脚本。

作用：
    读取 ``datasets/v01`` 下的版本化 JSONL 源文件，逐条校验后写入 Langfuse
    Dataset / DatasetItem。脚本可重复执行，同一个 ``id`` 会覆盖更新（upsert），
    不会产生重复样本。

Args:
    --dry-run: 只做本地校验并打印计划，不访问 Langfuse。
    --dataset: 只导入指定数据集，可重复传入；支持本地文件名（不含扩展名）
        或完整 Langfuse 数据集名。
    --skip-master / --master-only: 控制是否导入汇总集。
    --allow-easy: 允许 difficulty=easy 的样本通过校验（默认禁止）。
    --env-file: Langfuse 凭据文件，默认 ``evals/.env``。
    --verbose: 打印每条样本的导入结果。

Return:
    进程退出码：0 成功，1 本地校验失败，2 环境或 Langfuse 接口失败。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------

# 当前脚本位于 evals/task/process/intent_evals/scripts/。
INTENT_EVALS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_DATASETS_DIR = INTENT_EVALS_DIR / "datasets" / "v01"
DEFAULT_ENV_FILE = REPO_ROOT / "evals" / ".env"

# Langfuse DatasetItem 允许的顶层字段；业务字段必须嵌套在 input/expected_output/metadata。
ALLOWED_TOP_LEVEL_KEYS = {
    "id",
    "input",
    "expected_output",
    "metadata",
    "source_trace_id",
    "source_observation_id",
    "status",
}

# 项目要求的必填顶层字段。
REQUIRED_TOP_LEVEL_KEYS = {"id", "input", "expected_output", "metadata"}

# 意图评测允许的动作模式。
ALLOWED_MODES = {"must", "may", "must_not"}

# 难度枚举。
ALLOWED_DIFFICULTIES = {"easy", "medium", "hard", "expert"}

# DatasetItem ID 规范：{dataset_slug}_{四位序号}，dataset_slug 使用小写。
ITEM_ID_PATTERN = re.compile(r"^[a-z0-9_]+_v\d{2}_\d{4}$")

# 任务类型规范，例如 T04 多维统计分析。
TASK_PATTERN = re.compile(r"^T\d{2}\s+\S.*$")

# 中文数据集中的展示槽位必须使用业务语言；接口键名与排序等控制枚举不受影响。
CHINESE_TEXT_PATTERN = re.compile(r"[一-鿿]")
PHYSICAL_IDENTIFIER_PATTERN = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b")
SQL_PATTERN = re.compile(
    r"\b(?:COUNT|SUM|AVG|MAX|MIN|COALESCE|ROUND)\s*\(|\bSELECT\b.+\bFROM\b|\bCASE\s+WHEN\b",
    re.IGNORECASE | re.DOTALL,
)
CATEGORICAL_FIELDS = {
    "授精方式",
    "胚胎状态",
    "周期类型",
    "妊娠类型",
    "妊娠结局",
    "生存状态",
    "性别",
    "患者性别",
    "治疗类型",
    "周期结局",
    "胚胎类型",
}
BUSINESS_ABBREVIATIONS = {
    "IVF",
    "ICSI",
    "IVF/ICSI",
    "IVF+ICSI",
    "PGT",
    "PGT-A",
    "PGT-M",
    "3PN",
    "2PN",
    "1PN",
    "0PN",
}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class DatasetFile:
    """一个本地 JSONL 数据集文件及其 Langfuse 映射。

    Attributes:
        path: 本地 JSONL 文件路径。
        stem: 文件名去掉扩展名，例如 ``intent_FC_result_correctness_v01``。
        dimension_dir: 一级维度目录名；汇总集为空字符串。
        langfuse_name: Langfuse 数据集名，例如
            ``intent_evals/FC_functional_correctness/intent_FC_result_correctness_v01``。
        items: 已解析并校验的 DatasetItem 列表。
        sha256: 源文件 SHA-256，用于实验记录与版本追溯。
    """

    path: Path
    stem: str
    dimension_dir: str
    langfuse_name: str
    items: list[dict[str, Any]] = field(default_factory=list)
    sha256: str = ""

    @property
    def is_master(self) -> bool:
        """是否为汇总集（位于 v01 根目录）。"""
        return self.dimension_dir == ""

    @property
    def item_id_prefix(self) -> str:
        """DatasetItem ID 前缀，例如 ``intent_fc_result_correctness_v01_``。"""
        return f"{self.stem.lower()}_"


@dataclass
class ValidationIssue:
    """一条校验问题。

    Attributes:
        path: 文件路径。
        line: 行号，1-based；文件级问题为 0。
        message: 问题描述。
    """

    path: Path
    line: int
    message: str

    def render(self) -> str:
        """渲染为便于阅读的单行文本。"""
        location = f"{self.path.name}:{self.line}" if self.line else self.path.name
        return f"{location}: {self.message}"


# ---------------------------------------------------------------------------
# 环境变量
# ---------------------------------------------------------------------------


def load_env_file(path: Path) -> None:
    """加载 KEY=VALUE 形式的凭据文件，不覆盖进程内已有环境变量。

    Args:
        path: 凭据文件路径；不存在时静默跳过。

    Return:
        无返回值。
    """
    if not path.is_file():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        # 跳过空行与注释。
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        # 去掉成对的引号。
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


# ---------------------------------------------------------------------------
# 数据集发现与校验
# ---------------------------------------------------------------------------


def discover_dataset_files(datasets_dir: Path) -> list[DatasetFile]:
    """发现 ``datasets/v01`` 下全部 JSONL 文件并计算 Langfuse 数据集名。

    Args:
        datasets_dir: 版本目录，例如 ``datasets/v01``。

    Return:
        按路径排序的数据集文件列表。
    """
    if not datasets_dir.is_dir():
        raise FileNotFoundError(f"数据集目录不存在：{datasets_dir}")

    discovered: list[DatasetFile] = []
    for path in sorted(datasets_dir.rglob("*.jsonl")):
        relative = path.relative_to(datasets_dir)
        dimension_dir = "" if len(relative.parts) == 1 else relative.parts[0]
        stem = path.stem
        if dimension_dir:
            langfuse_name = f"intent_evals/{dimension_dir}/{stem}"
        else:
            langfuse_name = f"intent_evals/{stem}"
        discovered.append(
            DatasetFile(
                path=path,
                stem=stem,
                dimension_dir=dimension_dir,
                langfuse_name=langfuse_name,
            )
        )
    return discovered


def _is_non_empty_str(value: Any) -> bool:
    """判断值是否为非空字符串。"""
    return isinstance(value, str) and bool(value.strip())


def validate_business_label(label: Any, *, prefix: str) -> list[str]:
    """校验意图标签使用面向用户的业务表达。

    Args:
        label: 发布工具的标签参数或原始业务标注。
        prefix: 报错时标识标签所在的路径。
    Return:
        数据库标识、SQL、编码或展示文本的问题列表。
    """
    if not isinstance(label, dict):
        return [f"{prefix} 必须是业务标签对象"]

    problems: list[str] = []

    def inspect_text(value: Any, path: str) -> None:
        """递归检查展示内容，保留接口控制枚举。

        Args:
            value: 待检查的值。
            path: 当前值路径。
        Return:
            无返回值，问题写入 problems。
        """
        if isinstance(value, dict):
            for key, child in value.items():
                if key not in {"aggregation", "operator", "order", "implicit"}:
                    inspect_text(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                inspect_text(child, f"{path}[{index}]")
        elif isinstance(value, str):
            if SQL_PATTERN.search(value):
                problems.append(f"{path} 不得包含 SQL，指标应描述业务统计口径")
            if PHYSICAL_IDENTIFIER_PATTERN.search(value):
                problems.append(f"{path} 不得包含数据库标识，应使用业务名称")

    inspect_text(label, prefix)
    display_slots = [
        (key, label.get(key))
        for key in ("query_theme", "query_method", "analysis_type", "presentation")
    ]
    objects = label.get("query_object")
    if not isinstance(objects, list) or not objects:
        problems.append(f"{prefix}.query_object 必须是非空业务对象数组")
    else:
        display_slots.extend(
            (f"query_object[{index}]", value) for index, value in enumerate(objects)
        )
    for group, key in (
        ("metrics", "name"),
        ("metrics", "expression"),
        ("dimensions", "field"),
        ("filters", "field"),
        ("sort", "field"),
    ):
        parts = label.get(group)
        if not isinstance(parts, list):
            problems.append(f"{prefix}.{group} 必须是数组")
            continue
        for index, part in enumerate(parts):
            display_slots.append(
                (
                    f"{group}[{index}].{key}",
                    part.get(key) if isinstance(part, dict) else None,
                )
            )
    time_condition = label.get("time_condition")
    if isinstance(time_condition, dict):
        display_slots.extend(
            (f"time_condition.{key}", time_condition.get(key))
            for key in ("field", "calculation_basis", "granularity")
        )
    for path, value in display_slots:
        if not isinstance(value, str) or not CHINESE_TEXT_PATTERN.search(value):
            problems.append(
                f"{prefix}.{path} 必须使用中文业务表达，不能使用数据库名称或英文实现枚举"
            )

    # 年龄、数量、孕周等保留数值；分类条件必须写业务含义，不能直接发布内部编码。
    for index, condition in enumerate(label.get("filters") or []):
        if (
            not isinstance(condition, dict)
            or condition.get("field") not in CATEGORICAL_FIELDS
        ):
            continue
        values = condition.get("value")
        if not isinstance(values, list):
            values = [values]
        for value in values:
            if not isinstance(value, str) or value.isdecimal():
                problems.append(
                    f"{prefix}.filters[{index}].value 不得使用数据库编码，请填写业务含义"
                )
            elif (
                not CHINESE_TEXT_PATTERN.search(value)
                and value not in BUSINESS_ABBREVIATIONS
            ):
                problems.append(
                    f"{prefix}.filters[{index}].value 必须使用业务名称或通用业务缩写"
                )
    return problems


def validate_embedded_labels(value: Any, *, prefix: str) -> list[str]:
    """校验历史及轨迹中嵌入的结构化意图标签。

    Args:
        value: 可见历史、轨迹或其嵌套内容。
        prefix: 当前内容在样本中的路径。
    Return:
        标签内容的问题列表；普通文本及工具错误反馈不参与标签校验。
    """
    if isinstance(value, dict):
        if "query_object" in value:
            return validate_business_label(value, prefix=prefix)
        return [
            message
            for key, child in value.items()
            for message in validate_embedded_labels(child, prefix=f"{prefix}.{key}")
        ]
    if isinstance(value, list):
        return [
            message
            for index, child in enumerate(value)
            for message in validate_embedded_labels(child, prefix=f"{prefix}[{index}]")
        ]
    if isinstance(value, str):
        text = (
            value.strip()
            .removeprefix("<tool-response>")
            .removesuffix("</tool-response>")
        )
        if text.startswith(("{", "[")):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return []
            return validate_embedded_labels(parsed, prefix=prefix)
    return []


def validate_item(
    item: Any,
    *,
    dataset_file: DatasetFile,
    line: int,
    allow_easy: bool,
) -> list[ValidationIssue]:
    """校验单条 DatasetItem 是否符合评测方案 3-2 的字段要求。

    Args:
        item: 从 JSONL 行解析出的对象。
        dataset_file: 所属数据集文件，用于校验 ID 前缀。
        line: 行号，1-based。
        allow_easy: 是否允许 easy 难度。

    Return:
        校验问题列表；为空表示通过。
    """
    issues: list[ValidationIssue] = []

    def add(message: str) -> None:
        """记录一条问题。"""
        issues.append(
            ValidationIssue(path=dataset_file.path, line=line, message=message)
        )

    if not isinstance(item, dict):
        add("DatasetItem 必须是 JSON 对象")
        return issues

    # 1. 顶层字段：禁止业务字段平铺。
    extra_keys = set(item) - ALLOWED_TOP_LEVEL_KEYS
    if extra_keys:
        add(f"存在不允许的顶层字段：{sorted(extra_keys)}")
    missing_keys = REQUIRED_TOP_LEVEL_KEYS - set(item)
    if missing_keys:
        add(f"缺少必填顶层字段：{sorted(missing_keys)}")
        return issues

    # 2. ID 规范与数据集前缀。
    item_id = item["id"]
    if not _is_non_empty_str(item_id):
        add("id 必须是非空字符串")
    else:
        if not ITEM_ID_PATTERN.match(item_id):
            add(f"id 不符合 '{{dataset_slug}}_vNN_NNNN' 规范：{item_id}")
        if not item_id.startswith(dataset_file.item_id_prefix):
            add(
                f"id 前缀与数据集不匹配：期望 {dataset_file.item_id_prefix!r}，"
                f"实际 {item_id!r}"
            )

    # 3. input 结构。
    input_obj = item["input"]
    if not isinstance(input_obj, dict):
        add("input 必须是对象")
    else:
        for key in ("query", "conversation_history", "trace", "user_replies"):
            if key not in input_obj:
                add(f"input 缺少字段：{key}")
        if "query" in input_obj and not _is_non_empty_str(input_obj["query"]):
            add("input.query 必须是非空字符串")
        for key in ("conversation_history", "trace", "user_replies"):
            if key in input_obj and not isinstance(input_obj[key], list):
                add(f"input.{key} 必须是数组")
            for message in validate_embedded_labels(
                input_obj.get(key), prefix=f"input.{key}"
            ):
                add(message)

    # 4. expected_output：必须是标准动作数组。
    expected = item["expected_output"]
    if not isinstance(expected, dict):
        add("expected_output 必须是对象")
    else:
        actions = expected.get("value")
        if not isinstance(actions, list) or not actions:
            add("expected_output.value 必须是非空数组")
        else:
            publish_actions = [
                action
                for action in actions
                if isinstance(action, dict)
                and action.get("target") == "publish_intent_label"
            ]
            if not publish_actions:
                add(
                    "expected_output.value 至少需要一个 target=publish_intent_label 的期望"
                )
            for index, action in enumerate(actions):
                prefix = f"expected_output.value[{index}]"
                if not isinstance(action, dict):
                    add(f"{prefix} 必须是对象")
                    continue
                if action.get("kind") != "tool_call":
                    add(f"{prefix}.kind 必须为 tool_call")
                if action.get("target") != "publish_intent_label":
                    add(f"{prefix}.target 必须为 publish_intent_label")
                mode = action.get("mode")
                if mode not in ALLOWED_MODES:
                    add(f"{prefix}.mode 非法：{mode!r}")
                if mode == "must_not":
                    if action.get("arguments") is not None:
                        add(f"{prefix}.mode=must_not 时 arguments 必须为 null")
                elif mode in {"must", "may"}:
                    if not isinstance(action.get("arguments"), dict):
                        add(f"{prefix}.mode={mode} 时 arguments 必须是对象")
                    else:
                        for message in validate_business_label(
                            action["arguments"], prefix=f"{prefix}.arguments"
                        ):
                            add(message)

    # 5. metadata：分类、难度、维度、规则、原始标注。
    metadata = item["metadata"]
    if not isinstance(metadata, dict):
        add("metadata 必须是对象")
        return issues

    required_metadata = {
        "test_types",
        "task",
        "difficulty",
        "eval_dimensions",
        "scenario_tags",
        "eval_rules",
        "task_raw_value",
    }
    missing_metadata = required_metadata - set(metadata)
    if missing_metadata:
        add(f"metadata 缺少字段：{sorted(missing_metadata)}")

    test_types = metadata.get("test_types")
    if not isinstance(test_types, list) or "intent" not in test_types:
        add("metadata.test_types 必须包含 'intent'")

    task = metadata.get("task")
    if not _is_non_empty_str(task) or not TASK_PATTERN.match(task):
        add(f"metadata.task 不符合 'T04 多维统计分析' 形式：{task!r}")

    difficulty = metadata.get("difficulty")
    if difficulty not in ALLOWED_DIFFICULTIES:
        add(f"metadata.difficulty 非法：{difficulty!r}")
    elif difficulty == "easy" and not allow_easy:
        add("V1 数据集禁止 easy 简单样本；如确需保留请传 --allow-easy")

    dimensions = metadata.get("eval_dimensions")
    if not isinstance(dimensions, list) or not dimensions:
        add("metadata.eval_dimensions 必须是非空数组")

    tags = metadata.get("scenario_tags")
    if not isinstance(tags, list):
        add("metadata.scenario_tags 必须是数组")

    eval_rules = metadata.get("eval_rules")
    if not isinstance(eval_rules, dict):
        add("metadata.eval_rules 必须是对象")
    else:
        if not isinstance(eval_rules.get("order"), str):
            add("metadata.eval_rules.order 必须是字符串")
        slot_match = eval_rules.get("slot_match")
        if not isinstance(slot_match, dict) or not isinstance(
            slot_match.get("mode"), str
        ):
            add("metadata.eval_rules.slot_match.mode 必须是字符串")

    raw_values = metadata.get("task_raw_value")
    if not isinstance(raw_values, list) or not raw_values:
        add("metadata.task_raw_value 必须是非空数组")
    else:
        for index, raw in enumerate(raw_values):
            prefix = f"metadata.task_raw_value[{index}]"
            if not isinstance(raw, dict):
                add(f"{prefix} 必须是对象")
                continue
            if raw.get("target") != "publish_intent_label":
                add(f"{prefix}.target 必须为 publish_intent_label")
            if "tool_schema_version" not in raw:
                add(f"{prefix} 缺少 tool_schema_version")
            if "value" not in raw:
                add(f"{prefix} 缺少 value")
            elif raw["value"] is not None:
                for message in validate_business_label(
                    raw["value"], prefix=f"{prefix}.value"
                ):
                    add(message)
        if isinstance(expected, dict) and isinstance(expected.get("value"), list):
            for action in expected["value"]:
                if not isinstance(action, dict):
                    continue
                matches = [
                    raw
                    for raw in raw_values
                    if isinstance(raw, dict)
                    and raw.get("target") == action.get("target")
                    and raw.get("turn") == action.get("turn")
                ]
                if len(matches) != 1 or action.get("arguments") != matches[0].get(
                    "value"
                ):
                    add(
                        "expected_output.arguments 必须与对应轮次 task_raw_value.value 的业务标签一致"
                    )

    return issues


def load_dataset_file(
    dataset_file: DatasetFile,
    *,
    allow_easy: bool,
) -> list[ValidationIssue]:
    """读取并校验一个 JSONL 文件。

    Args:
        dataset_file: 待加载的数据集文件。
        allow_easy: 是否允许 easy 难度。

    Return:
        校验问题列表；为空表示通过，且 ``dataset_file.items`` 已被填充。
    """
    issues: list[ValidationIssue] = []
    raw_bytes = dataset_file.path.read_bytes()
    dataset_file.sha256 = hashlib.sha256(raw_bytes).hexdigest()

    items: list[dict[str, Any]] = []
    text = raw_bytes.decode("utf-8")
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            issues.append(
                ValidationIssue(
                    path=dataset_file.path,
                    line=line_number,
                    message=f"JSON 解析失败：{exc}",
                )
            )
            continue
        issues.extend(
            validate_item(
                item,
                dataset_file=dataset_file,
                line=line_number,
                allow_easy=allow_easy,
            )
        )
        items.append(item)

    if not items and not issues:
        issues.append(
            ValidationIssue(
                path=dataset_file.path,
                line=0,
                message="文件为空，至少需要一条 DatasetItem",
            )
        )

    dataset_file.items = items
    return issues


def validate_global_item_ids(
    dataset_files: Iterable[DatasetFile],
) -> list[ValidationIssue]:
    """校验 DatasetItem ID 在项目内全局唯一。

    Args:
        dataset_files: 已加载的数据集文件列表。

    Return:
        校验问题列表；同一 ID 出现在不同数据集时报告冲突。
    """
    issues: list[ValidationIssue] = []
    seen: dict[str, DatasetFile] = {}
    for dataset_file in dataset_files:
        for item in dataset_file.items:
            item_id = item.get("id")
            if not isinstance(item_id, str):
                continue
            previous = seen.get(item_id)
            if previous is not None and previous.path != dataset_file.path:
                issues.append(
                    ValidationIssue(
                        path=dataset_file.path,
                        line=0,
                        message=(
                            f"DatasetItem id 跨数据集重复：{item_id!r}，"
                            f"已存在于 {previous.path.name}"
                        ),
                    )
                )
            else:
                seen[item_id] = dataset_file
    return issues


# ---------------------------------------------------------------------------
# Langfuse 导入
# ---------------------------------------------------------------------------


def ensure_dataset(client: Any, dataset_file: DatasetFile) -> None:
    """确保 Langfuse 数据集存在；已存在时复用。

    Args:
        client: Langfuse SDK 客户端。
        dataset_file: 数据集文件及其映射信息。

    Return:
        无返回值。
    """
    metadata = {
        "source_file": dataset_file.path.name,
        "source_sha256": dataset_file.sha256,
        "item_count": len(dataset_file.items),
        "schema_version": "v01",
        "test_types": ["intent"],
    }
    description = (
        f"意图识别评测数据集 {dataset_file.stem}；"
        "由 evals/task/process/intent_evals/scripts/import_datasets_to_langfuse.py 导入。"
    )
    try:
        client.create_dataset(
            name=dataset_file.langfuse_name,
            description=description,
            metadata=metadata,
        )
    except Exception:
        # 数据集名项目内唯一；已存在时复用，不回删已有样本。
        client.get_dataset(dataset_file.langfuse_name)


def import_dataset(
    client: Any,
    dataset_file: DatasetFile,
    *,
    verbose: bool,
) -> int:
    """把一个本地数据集文件写入 Langfuse。

    Args:
        client: Langfuse SDK 客户端。
        dataset_file: 已校验的数据集文件。
        verbose: 是否逐条打印。

    Return:
        成功写入的样本数。
    """
    ensure_dataset(client, dataset_file)

    imported = 0
    for item in dataset_file.items:
        client.create_dataset_item(
            dataset_name=dataset_file.langfuse_name,
            id=item["id"],
            input=item["input"],
            expected_output=item["expected_output"],
            metadata=item["metadata"],
            source_trace_id=item.get("source_trace_id"),
            source_observation_id=item.get("source_observation_id"),
            status=item.get("status"),
        )
        imported += 1
        if verbose:
            print(f"  upsert {item['id']}")
    return imported


def verify_dataset(client: Any, dataset_file: DatasetFile) -> int:
    """回读并逐条核验 Langfuse 样本的 ID 与内容。

    Args:
        client: Langfuse SDK 客户端。
        dataset_file: 数据集文件。

    Return:
        内容一致时返回远端样本数，否则抛出 ValueError。
    """
    dataset = client.get_dataset(dataset_file.langfuse_name)
    remote_items = {item.id: item for item in dataset.items}
    local_ids = {item["id"] for item in dataset_file.items}
    if set(remote_items) != local_ids or len(dataset.items) != len(dataset_file.items):
        raise ValueError(
            f"{dataset_file.langfuse_name} 的远端样本 ID 或数量与本地不一致"
        )
    for local in dataset_file.items:
        remote = remote_items[local["id"]]
        for key in ("input", "expected_output", "metadata"):
            if getattr(remote, key) != local[key]:
                raise ValueError(f"{local['id']} 的远端 {key} 内容与本地不一致")
    return len(dataset.items)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。

    Return:
        argparse 解析器。
    """
    parser = argparse.ArgumentParser(
        description="把 intent_evals/datasets/v01 下的 JSONL 数据集导入 Langfuse。",
    )
    parser.add_argument(
        "--datasets-dir",
        type=Path,
        default=DEFAULT_DATASETS_DIR,
        help=f"数据集版本目录，默认 {DEFAULT_DATASETS_DIR}",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help=f"Langfuse 凭据文件，默认 {DEFAULT_ENV_FILE}",
    )
    parser.add_argument(
        "--dataset",
        action="append",
        default=[],
        help="只导入指定数据集；可传本地文件名或完整 Langfuse 名，可重复。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只做本地校验并打印计划，不访问 Langfuse。",
    )
    parser.add_argument(
        "--skip-master",
        action="store_true",
        help="跳过汇总集 intent_master_v01。",
    )
    parser.add_argument(
        "--master-only",
        action="store_true",
        help="只导入汇总集 intent_master_v01。",
    )
    parser.add_argument(
        "--allow-easy",
        action="store_true",
        help="允许 difficulty=easy 的样本通过校验。",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="逐条打印导入结果。",
    )
    return parser


def select_dataset_files(
    dataset_files: list[DatasetFile],
    *,
    selected: list[str],
    skip_master: bool,
    master_only: bool,
) -> list[DatasetFile]:
    """按命令行过滤待导入数据集。

    Args:
        dataset_files: 全部数据集文件。
        selected: --dataset 传入的过滤项。
        skip_master: 是否跳过汇总集。
        master_only: 是否只保留汇总集。

    Return:
        过滤后的数据集文件列表。
    """
    result = dataset_files
    if master_only:
        result = [item for item in result if item.is_master]
    elif skip_master:
        result = [item for item in result if not item.is_master]

    if selected:
        wanted = set(selected)
        result = [
            item
            for item in result
            if item.stem in wanted
            or item.langfuse_name in wanted
            or item.path.name in wanted
        ]
        found = (
            {item.stem for item in result}
            | {item.langfuse_name for item in result}
            | {item.path.name for item in result}
        )
        missing = [value for value in selected if value not in found]
        if missing:
            raise ValueError(f"--dataset 未匹配到数据集：{missing}")
    return result


def main(argv: list[str] | None = None) -> int:
    """脚本入口。

    Args:
        argv: 命令行参数；默认读取 sys.argv。

    Return:
        进程退出码。
    """
    args = build_parser().parse_args(argv)

    # 1. 发现与本地校验。
    try:
        dataset_files = discover_dataset_files(args.datasets_dir)
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    try:
        dataset_files = select_dataset_files(
            dataset_files,
            selected=args.dataset,
            skip_master=args.skip_master,
            master_only=args.master_only,
        )
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    issues: list[ValidationIssue] = []
    for dataset_file in dataset_files:
        issues.extend(load_dataset_file(dataset_file, allow_easy=args.allow_easy))
    issues.extend(validate_global_item_ids(dataset_files))

    if issues:
        print(f"[ERROR] 数据集校验失败，共 {len(issues)} 个问题：", file=sys.stderr)
        for issue in issues:
            print(f"  - {issue.render()}", file=sys.stderr)
        return 1

    print("[OK] 本地校验通过：")
    for dataset_file in dataset_files:
        print(
            f"  - {dataset_file.path.relative_to(REPO_ROOT)} -> "
            f"{dataset_file.langfuse_name} ({len(dataset_file.items)} 条, "
            f"sha256={dataset_file.sha256[:12]}…)"
        )

    if args.dry_run:
        print("[DRY-RUN] 未写入 Langfuse。")
        return 0

    # 2. 凭据与客户端。
    load_env_file(args.env_file)
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    base_url = os.environ.get("LANGFUSE_BASE_URL")
    missing = [
        name
        for name, value in (
            ("LANGFUSE_PUBLIC_KEY", public_key),
            ("LANGFUSE_SECRET_KEY", secret_key),
            ("LANGFUSE_BASE_URL", base_url),
        )
        if not value
    ]
    if missing:
        print(
            f"[ERROR] 缺少 Langfuse 环境变量：{missing}；请在 {args.env_file} 中配置。",
            file=sys.stderr,
        )
        return 2

    try:
        from langfuse import Langfuse
    except ImportError:
        print(
            "[ERROR] 未安装 langfuse SDK；请使用 backend/.venv 的 Python 运行。",
            file=sys.stderr,
        )
        return 2

    client = Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        host=base_url,
        # 数据集导入只调用 Dataset API，不产生 Trace。
        tracing_enabled=False,
    )

    # 3. 写入并校验。
    total = 0
    failed = False
    try:
        for dataset_file in dataset_files:
            print(f"[IMPORT] {dataset_file.langfuse_name}")
            imported = import_dataset(client, dataset_file, verbose=args.verbose)
            total += imported
        client.flush()

        print("[VERIFY] 重新读取 Langfuse 数据集：")
        for dataset_file in dataset_files:
            actual = verify_dataset(client, dataset_file)
            expected = len(dataset_file.items)
            status = "OK" if actual == expected else "MISMATCH"
            if actual != expected:
                failed = True
            print(
                f"  - {dataset_file.langfuse_name}: 期望 {expected}，实际 {actual} [{status}]"
            )
    except Exception as exc:  # noqa: BLE001 - CLI 需要把 SDK 异常转成退出码
        print(f"[ERROR] Langfuse 导入失败：{exc}", file=sys.stderr)
        return 2

    print(f"[DONE] 共导入 {total} 条 DatasetItem。")
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
