# 意图识别评测数据集 v01

本目录是《03-过程评测-意图识别评测》的数据集版本 `v01`。数据以 Langfuse
`DatasetItem` 为最小单位，本地 JSONL 仅作为可追溯的版本化源文件。

## 1. 文件清单

| 本地文件 | Langfuse 数据集名 | 条数 |
| --- | --- | ---: |
| `intent_master_v01.jsonl` | `intent_evals/intent_master_v01` | 94 |
| `FC_functional_correctness/intent_FC_result_correctness_v01.jsonl` | `intent_evals/FC_functional_correctness/intent_FC_result_correctness_v01` | 47 |
| `PQ_process_quality/intent_PQ_context_utilization_v01.jsonl` | `intent_evals/PQ_process_quality/intent_PQ_context_utilization_v01` | 30 |
| `RS_robustness_safety/intent_RS_consistency_v01.jsonl` | `intent_evals/RS_robustness_safety/intent_RS_consistency_v01` | 12 |
| `EA_experience_alignment/intent_EA_label_clarity_v01.jsonl` | `intent_evals/EA_experience_alignment/intent_EA_label_clarity_v01` | 5 |

`intent_master_v01.jsonl` 汇总四个维度的 94 条样本，使用独立的
`intent_master_v01_XXXX` ID，并通过 `metadata.source_dataset_item_id` 指回专项数据集
中的原始条目。`EC_efficiency_cost/` 不存放样本，只存放运行级效率与成本配置
`intent_evaluation_config_v01.yaml`。

## 2. 来源与去重

原始数据来自同一任务目录下四个平铺版本：`v00`、`v01`、`v01_real`、`v02`。
`v02` 是标注最完整的精修版本；`v01`、`v01_real` 与 `v02` 存在大量同模板样本；
`v00` 保留了部分多表关联的早期复杂样本。

去重按“语义意图 + 期望槽位”判断，而不是只看 ID 或措辞：

- 同一模板只更换年份、日期、周期措辞或标注语言，视为重复，保留 `v02`；
- `v02` 缺失时依次保留 `v01` → `v01_real` → `v00`；
- `v01_real` 存在复用 `source_case_id` 指向不同内容的情况，判断时以 query 与
  `expected_output.value[].arguments` 为准；
- 同一 `source_case_id` 保留标注更规范、`task_raw_value` 更完整的一条。

保留来源分布：

| 维度 | v00 | v01 | v01_real | v02 |
| --- | ---: | ---: | ---: | ---: |
| FC | 9 | 0 | 6 | 32 |
| PQ | 0 | 5 | 6 | 19 |
| RS | 0 | 0 | 0 | 12 |
| EA | 0 | 0 | 0 | 5 |

## 3. 简单样本处理

按用户要求 `v01` 不保留 `easy` 样本：

- 单实体、单条件、直接计数/明细的早期基线样本（如 v00 的周期数量直查）整体剔除；
- T07 结构化输出、T08 异常恢复、多轮上下文、比率/条件聚合样本即使原标 `easy`
  也保留，并按实际复杂度重新定级；
- 安全负样本按风险类别保留代表，统一按 `medium` 计。

最终难度分布：

| 维度 | medium | hard | expert | 合计 |
| --- | ---: | ---: | ---: | ---: |
| FC | 32 | 9 | 6 | 47 |
| PQ | 16 | 11 | 3 | 30 |
| RS | 8 | 4 | 0 | 12 |
| EA | 5 | 0 | 0 | 5 |
| 合计 | 61 | 24 | 9 | 94 |

任务类型分布（合计）：T01 4、T02 8、T03 22、T04 21、T05 18、T06 10、T07 4、
T08 2、T09 5。

## 4. 字段约定

- 顶层字段只允许 `id`、`input`、`expected_output`、`metadata`，以及可选的
  `source_trace_id`、`source_observation_id`、`status`；业务字段不得平铺。
- `input` 固定包含 `query`、`conversation_history`、`trace`、`user_replies`。
- `expected_output.value` 只保留一个 `target=publish_intent_label` 的动作；
  `mode=must_not` 时 `arguments` 为 `null`。
- `arguments` 使用面向用户的业务标签，与对应 `metadata.task_raw_value[].value`
  内容一致。业务对象、指标、条件与取值使用原始中文业务表达；指标 `expression`
  描述统计口径，例如“统计患者人数，每位患者只计一次”，不得填写物理表名、列名、
  内部编码或 SQL。JSON 键名、排序/分组等工具控制字段遵守接口结构；IVF、ICSI、
  PGT、3PN 等业务术语与用户提供的编号、日期正常保留。Schema/值映射和 SQL
  属于意图后阶段，数据库结果在最终业务回答呈现。硬性规则见
  [evals/AGENTS.md §4.3.1](../../../../../AGENTS.md#431-意图标签必须使用用户业务语言硬性规则)。
- 本次业务表达修订记为 `metadata.annotation_revision=business-language-r01`。
  已纠正的例子包括“授精方式=2”→“授精方式=ICSI”、“胚胎状态=4”→
  “胚胎状态=已移植”；9 条早期 SQL 来源样本改为用户提出的业务对象及条件。
  原始文件和本次修订前快照保留在本地 `.tmp/`，来源编号保持可追溯。
- `metadata` 必填 `test_types`、`task`、`difficulty`、`eval_dimensions`、
  `scenario_tags`、`eval_rules`、`task_raw_value`；负样本额外带
  `negative_reason`。

## 5. 导入 Langfuse

```bash
backend/.venv/Scripts/python.exe -X utf8 -B \
  evals/task/process/intent_evals/scripts/import_datasets_to_langfuse.py --dry-run

backend/.venv/Scripts/python.exe -X utf8 -B \
  evals/task/process/intent_evals/scripts/import_datasets_to_langfuse.py
```

脚本从 `evals/.env` 读取 `LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`、
`LANGFUSE_BASE_URL`，逐条校验后以 `create_dataset_item` 幂等 upsert，并在导入后
重新读取数据集核对条数。`--dataset`、`--skip-master`、`--master-only` 可用于
局部重导；`--allow-easy` 仅用于临时排查，不应写入正式版本。

## 6. 追溯说明

旧版平铺文件已移动到 `.tmp/legacy_datasets/`（`.tmp/` 不入库），仅用于本次重构的
人工比对。正式版本以本目录的 `v01` 文件为准。
