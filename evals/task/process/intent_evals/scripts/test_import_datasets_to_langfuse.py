"""意图标签业务表达与 Langfuse 内容核验的回归测试。"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from import_datasets_to_langfuse import (
    DEFAULT_DATASETS_DIR,
    DatasetFile,
    discover_dataset_files,
    load_dataset_file,
    validate_item,
    verify_dataset,
)


class BusinessLabelTests(unittest.TestCase):
    """验证业务标签保留用户含义并拒绝数据库实现细节。"""

    def setUp(self) -> None:
        """准备合法业务标签与样本。

        Args:
            无。
        Return:
            无返回值。
        """
        self.dataset = DatasetFile(
            path=Path("intent_FC_result_correctness_v01.jsonl"),
            stem="intent_FC_result_correctness_v01",
            dimension_dir="FC_functional_correctness",
            langfuse_name="intent_evals/FC_functional_correctness/intent_FC_result_correctness_v01",
        )
        label = {
            "query_theme": "ICSI治疗周期统计",
            "query_object": ["治疗周期"],
            "query_method": "统计分析",
            "analysis_type": "单值统计",
            "metrics": [
                {
                    "name": "周期数量",
                    "expression": "统计符合条件的治疗周期数量，每个周期只计一次",
                }
            ],
            "dimensions": [],
            "filters": [{"field": "授精方式", "operator": "=", "value": "ICSI"}],
            "time_condition": {
                "field": "周期日期",
                "range": "2024年",
                "granularity": "年",
                "calculation_basis": "周期日期",
            },
            "sort": [],
            "limit": None,
            "presentation": "表格",
        }
        self.item = {
            "id": "intent_fc_result_correctness_v01_0001",
            "input": {
                "query": "统计2024年ICSI治疗周期数量",
                "conversation_history": [],
                "trace": [],
                "user_replies": [],
            },
            "expected_output": {
                "value": [
                    {
                        "kind": "tool_call",
                        "target": "publish_intent_label",
                        "mode": "must",
                        "arguments": copy.deepcopy(label),
                    }
                ]
            },
            "metadata": {
                "test_types": ["intent"],
                "task": "T03 单指标统计",
                "difficulty": "medium",
                "eval_dimensions": ["FC/result_correctness"],
                "scenario_tags": [],
                "eval_rules": {
                    "order": "strict",
                    "slot_match": {"mode": "semantic_equivalence"},
                },
                "task_raw_value": [
                    {
                        "target": "publish_intent_label",
                        "tool_schema_version": "intent_label@2026-11-10",
                        "value": copy.deepcopy(label),
                    }
                ],
            },
        }

    def issues(self) -> list[str]:
        """返回当前样本的校验信息。

        Args:
            无。
        Return:
            校验信息列表。
        """
        return [
            issue.message
            for issue in validate_item(
                self.item, dataset_file=self.dataset, line=1, allow_easy=False
            )
        ]

    def test_business_terms_and_user_identifiers_are_allowed(self) -> None:
        """保留业务缩写、用户编号、日期以及 JSON 控制字段。

        Args:
            无。
        Return:
            无返回值。
        """
        label = self.item["expected_output"]["value"][0]["arguments"]
        label["filters"].append(
            {"field": "病历号", "operator": "=", "value": "2023-0456"}
        )
        label["filters"].append({"field": "年龄", "operator": ">=", "value": 35})
        self.item["metadata"]["task_raw_value"][0]["value"] = copy.deepcopy(label)
        self.assertEqual([], self.issues())

    def test_physical_names_are_rejected_in_all_label_slots(self) -> None:
        """各业务槽位中的英文物理标识必须被拒绝。

        Args:
            无。
        Return:
            无返回值。
        """
        base = copy.deepcopy(self.item)
        for key, value in (
            ("query_object", ["treatment_cycle"]),
            ("query_object", ["embryopgt"]),
            ("query_object", ["EmbryoOperationRecord"]),
            ("metrics", [{"name": "patient_count", "expression": "统计患者人数"}]),
            ("dimensions", [{"field": "cycle_type", "aggregation": "group_by"}]),
            (
                "filters",
                [{"field": "胚胎状态", "operator": "=", "value": "TRANSFERRED"}],
            ),
            ("query_method", "statistical_analysis"),
        ):
            with self.subTest(key=key, value=value):
                self.item = copy.deepcopy(base)
                self.item["expected_output"]["value"][0]["arguments"][key] = value
                self.assertTrue(
                    any("业务" in issue or "数据库" in issue for issue in self.issues())
                )

    def test_sql_is_rejected_even_with_chinese_identifiers(self) -> None:
        """中文列名的 SQL 也不属于业务意图标签。

        Args:
            无。
        Return:
            无返回值。
        """
        label = self.item["expected_output"]["value"][0]["arguments"]
        for expression in (
            "COUNT(DISTINCT patient_id)",
            "COUNT(DISTINCT 患者编号)",
            "SUM(CASE WHEN 妊娠类型='临床妊娠' THEN 1 ELSE 0 END)",
        ):
            with self.subTest(expression=expression):
                label["metrics"][0]["expression"] = expression
                self.assertTrue(any("SQL" in issue for issue in self.issues()))

    def test_database_codes_are_rejected_but_numeric_thresholds_are_allowed(
        self,
    ) -> None:
        """状态编码必须恢复业务含义，年龄等数值阈值仍合法。

        Args:
            无。
        Return:
            无返回值。
        """
        label = self.item["expected_output"]["value"][0]["arguments"]
        for field, code in (("授精方式", 2), ("胚胎状态", 4), ("周期类型", "1")):
            with self.subTest(field=field):
                label["filters"] = [{"field": field, "operator": "=", "value": code}]
                self.assertTrue(any("编码" in issue for issue in self.issues()))

    def test_raw_annotation_is_checked_and_must_match_expected(self) -> None:
        """原始业务标注与运行期期望都必须合法且相同。

        Args:
            无。
        Return:
            无返回值。
        """
        raw = self.item["metadata"]["task_raw_value"][0]["value"]
        raw["query_object"] = ["patient"]
        messages = self.issues()
        self.assertTrue(
            any("task_raw_value" in issue and "业务" in issue for issue in messages)
        )
        self.assertTrue(any("一致" in issue for issue in messages))

    def test_historical_labels_are_checked_without_rewriting_tool_errors(self) -> None:
        """检查历史中序列化标签，同时允许冻结的工具异常反馈。

        Args:
            无。
        Return:
            无返回值。
        """
        self.item["input"]["trace"] = [
            {
                "role": "tool",
                "message": '<tool-response>{"code":"INVALID_PARAMETER","message":"参数错误"}</tool-response>',
            }
        ]
        self.assertEqual([], self.issues())
        historical = copy.deepcopy(
            self.item["expected_output"]["value"][0]["arguments"]
        )
        historical["query_object"] = ["treatment_cycle"]
        self.item["input"]["conversation_history"] = [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "name": "publish_intent_label",
                        "arguments": json.dumps(historical, ensure_ascii=False),
                    }
                ],
            }
        ]
        self.assertTrue(
            any(
                "conversation_history" in issue and "业务" in issue
                for issue in self.issues()
            )
        )

    def test_negative_expectation_keeps_null_annotation(self) -> None:
        """负样本不填充标签，同时保持显式拒绝期望。

        Args:
            无。
        Return:
            无返回值。
        """
        action = self.item["expected_output"]["value"][0]
        action.update(mode="must_not", arguments=None)
        self.item["metadata"]["task_raw_value"][0]["value"] = None
        self.assertEqual([], self.issues())

    def test_same_count_but_stale_remote_content_is_rejected(self) -> None:
        """远端数量相同但保留旧标签时，回读核验必须失败。

        Args:
            无。
        Return:
            无返回值。
        """
        self.dataset.items = [self.item]
        stale = copy.deepcopy(self.item["expected_output"])
        stale["value"][0]["arguments"]["query_object"] = ["treatment_cycle"]
        remote = SimpleNamespace(
            id=self.item["id"],
            input=self.item["input"],
            expected_output=stale,
            metadata=self.item["metadata"],
        )
        client = SimpleNamespace(
            get_dataset=lambda name: SimpleNamespace(items=[remote])
        )
        with self.assertRaisesRegex(ValueError, "内容|一致"):
            verify_dataset(client, self.dataset)

    def test_real_datasets_and_master_remain_consistent(self) -> None:
        """全量数据通过业务语义校验，汇总与专项逐条一致。

        Args:
            无。
        Return:
            无返回值。
        """
        datasets = discover_dataset_files(DEFAULT_DATASETS_DIR)
        sources = {}
        master = []
        for dataset in datasets:
            issues = load_dataset_file(dataset, allow_easy=False)
            self.assertEqual([], [issue.render() for issue in issues])
            if dataset.is_master:
                master = dataset.items
            else:
                sources.update({item["id"]: item for item in dataset.items})
        self.assertEqual(94, len(sources))
        self.assertEqual(94, len(master))
        for item in master:
            source = sources[item["metadata"]["source_dataset_item_id"]]
            self.assertEqual(source["input"], item["input"])
            self.assertEqual(source["expected_output"], item["expected_output"])
            metadata = dict(item["metadata"])
            metadata.pop("source_dataset_item_id")
            self.assertEqual(source["metadata"], metadata)
            self.assertEqual(json.loads(json.dumps(item, ensure_ascii=False)), item)


if __name__ == "__main__":
    unittest.main()
