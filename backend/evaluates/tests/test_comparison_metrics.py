"""离线核对合成指标的分母、宏平均、候选顺序和分位数，不调用模型。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from comparison_metrics import duration_summary, f1, summarize_rows
from schema_metrics import retrieval_metrics


def synthetic_row(case_id: str, precision: float, recall: float, success: bool) -> dict:
    """构造不含真实题库的合成指标。Args: 题号/P/R/成功。Returns: 指标行。"""
    return {
        "case_id": case_id,
        "first_discovery_recall_at_k": {str(k): recall for k in [1, 3, 5, 10]},
        "candidate_table_recall": recall,
        "candidate_table_precision": precision,
        "candidate_complete": recall == 1,
        "table_recall": recall,
        "retrieved_table_precision": precision,
        "column_coverage": recall,
        "structure_complete": recall == 1,
        "first_execution_ok": success,
        "execution_ok": success,
        "primary_success": success,
        "strict_sql_success": False,
        "elapsed_seconds": 2 if success else 10,
        "tool_calls": 3 if success else 5,
        "timing_source": "synthetic",
        "retrieval_rounds_total": 1,
        "first_complete_search_round": 1 if recall == 1 else None,
        "first_complete_mcp_call": 2 if recall == 1 else None,
        "prompt_group": "synthetic",
        "source_run": "synthetic",
    }


def test_macro_f1_is_mean_of_per_case_f1() -> None:
    """核对逐题F1后均值，不用汇总P/R计算。Args: 无。Returns: 无。"""
    rows = [synthetic_row("synthetic-1", 1, 0.2, False), synthetic_row("synthetic-2", 0.4, 1, True)]
    result = summarize_rows(rows)
    expected = (f1(1, 0.2) + f1(0.4, 1)) / 2
    assert result["discovery"]["candidate_table_macro_f1"] == pytest.approx(expected)
    assert result["structure"]["table_macro_f1"] == pytest.approx(expected)
    assert expected != pytest.approx(f1(0.7, 0.6))


def test_missing_sql_stays_in_sample_denominator_and_success_efficiency() -> None:
    """没有SQL的有效样本计无错率未成功，成功效率只取主成功题。Args: 无。Returns: 无。"""
    result = summarize_rows([synthetic_row("synthetic-1", 1, 1, True), synthetic_row("synthetic-2", 0, 0, False)])
    assert result["task"]["first_sql_error_free_rate"] == 0.5
    assert result["task"]["final_sql_error_free_rate"] == 0.5
    assert result["efficiency"]["mean_mcp_calls"] == 4
    assert result["efficiency"]["saved_elapsed"]["mean_seconds"] == 6
    assert result["efficiency"]["successful_saved_elapsed"]["mean_seconds"] == 2
    assert result["efficiency"]["successful_mean_mcp_calls"] == 3
    assert result["rounds"]["never_complete"] == 1


def test_p95_nearest_rank_and_empty_success_set() -> None:
    """核对最近秩和零成功时不伪造0秒。Args: 无。Returns: 无。"""
    result = duration_summary(list(range(1, 21)))
    assert result["p50_seconds"] == 10.5
    assert result["p95_seconds"] == 19
    failed = summarize_rows([synthetic_row("synthetic-1", 0, 0, False)])
    assert failed["efficiency"]["successful_saved_elapsed"]["mean_seconds"] is None
    assert failed["efficiency"]["successful_mean_mcp_calls"] is None


def test_first_nonempty_candidate_dedup_order_not_call_count() -> None:
    """首个非空候选返回内部去重排序，不能把空调用计为k。Args: 无。Returns: 无。"""
    sample = {"target_tables": ["alpha"], "target_columns": [{"table": "alpha", "column": "id"}]}
    calls = [
        {"tool": "vfs_find_file", "arguments": {}, "result": {"status": "success", "result_content": ""}},
        {"tool": "vfs_find_file", "arguments": {}, "result": {"status": "success", "result_content": "/synthetic/db/table/beta/desc.md\n/synthetic/db/table/beta/desc.md\n/synthetic/db/table/alpha/desc.md"}},
    ]
    result = retrieval_metrics(sample, calls, {})
    assert result["first_discovery_recall_at_k"] == {"1": 0, "3": 1, "5": 1, "10": 1}
    assert result["candidate_table_precision"] == 0.5


def test_duplicate_cases_rejected() -> None:
    """重复题不得进入分母。Args: 无。Returns: 无。"""
    row = synthetic_row("synthetic-1", 1, 1, True)
    with pytest.raises(ValueError, match="重复"):
        summarize_rows([row, row])
