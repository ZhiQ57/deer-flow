# evals — DataAgent 评测工作区

本目录是 DeerFlow DataAgent 的专业评测工作区：**评测方案、版本化数据集、适配脚本、实验报告**。它回答两个问题：端到端业务效果如何，以及效果变化由链路中哪个模块造成。

> 本文件是 `evals/` 的入口指南。具体评测的目标、边界、数据字段、指标公式和验收门槛，以 `docs/` 下对应方案为准；本文件只负责目录约定、公共规范和指路。

## 1. 定位与原则

### 1.1 与 backend/evaluates 的分工

- `backend/evaluates/`：早期 VFSSQL MCP/Skill 采集评测（324 题、Gateway 真实会话、冻结采集器、离线评分）。它是一套已经跑过的运行资产，**不要改写其冻结协议和既有结果**。
- `evals/`：面向 DataAgent 全链路的新评测体系，按“端到端 + 过程评测”组织，自动化实验使用 Langfuse 等成熟框架。
- 两者可以共享业务题目和结论，但不共用运行目录、不互相覆盖结果；`evals/` 的新实验不要写进 `backend/evaluates/runs/`。

### 1.2 三方职责

| 模块 | 负责什么 | 项目角色 |
| --- | --- | --- |
| Agent 应用 | 接收 Input、执行 Agent、返回 Output、生成 Trace | DeerFlow 等 Harness |
| Evaluation | 加载 Dataset、调用 Agent、计算指标、创建 Experiment | `evals/` |
| Langfuse | Dataset、Trace、Observation、Experiment、Score 管理 | Langfuse 平台 |

边界要求：

- `evals/` 不实现 Agent 执行逻辑，也不实现 Trace 采集；它只负责把 Dataset Item 变成 Agent 输入、调用 Agent、把输出交给 evaluator。
- Agent 应用不感知评测数据集和 Gold；它只接收正常业务输入，按生产路径执行并产生 Trace。
- Langfuse 只负责存储与展示，不替项目判断业务正确性；指标口径、Gold 和阈值由 `evals/` 定义。

### 1.3 硬原则

1. **框架优先，脚本只做适配。** 自动化评测使用 Langfuse（Dataset + Experiments + Scores）与 RAGAS（Schema Recall 指标）等成熟框架。本目录的脚本只负责数据集加载、task 驱动、evaluator 注册、结果聚合与报告导出；**不从头实现评测引擎、指标调度或实验存储**。
2. **数据与记录优先。** `evals/` 的主要产出是可复现的数据集、逐样本结果、版本对照和报告，不是一次性的跑分。任何实验都必须留下“数据版本 + 配置版本 + 框架版本 + 结果”的完整记录。
3. **文档即规范。** 每个评测的输入、标注、指标、阈值以 `docs/` 为准。代码与文档不一致时，先同步文档或同时修改，不允许“代码跑成什么样就算什么样”。
4. **冻结与可追溯。** 已发布的数据集版本、预测和报告不改写；变更通过新版本号或新 run 目录表达。Gold、隐藏实例和评分规则只留在评测端。
5. **不碰生产。** 评测适配代码不得修改 DeerFlow 生产逻辑、MCP 生产检索路径或数据库；被测 MCP 的评测副本放 `extensions/`，并记录来源版本。

## 2. 目录结构

```text
evals/
├── AGENTS.md                     # 本文件：目录约定与公共规范
├── evals.yml                     # 全局运行配置：默认值、Harness 注册表、实验与对照规则
├── mcp_extensions.json           # 评测用 MCP Server 配置（凭据用环境变量引用）
├── harnesses/                    # 各 Harness Agent 的适配配置（一 Harness 一文件）
│   ├── README.md                 # 适配器契约、NormalizedResult、能力矩阵、Trace 覆盖模式
│   ├── deerflow.yaml
│   ├── codex.yaml
│   ├── claude-code.yaml
│   ├── opencode.yaml             # 草稿，本机未安装，待核对
│   └── pi-agent.yaml             # 草稿，本机未安装，待核对
├── schemas/                      # 配置校验：harness.schema.json、experiment.schema.json
├── docs/                         # 评测方案（权威）
│   ├── 01-端到端评测.md          # 占位，待补
│   ├── 02-过程评测.md            # 过程评测索引（骨架）
│   └── 03-过程评测-<模块>评测.md # 各模块方案
├── task/
│   ├── end-to-end/               # 端到端评测
│   │   └── SQL/
│   │       ├── datasets/         # 版本化数据集
│   │       ├── scripts/          # 适配脚本（待建）
│   │       ├── reports/          # 实验报告
│   │       └── .tmp/             # 本地临时产物（不入库）
│   └── process/                  # 过程评测
│       ├── intent_evals/         # 意图识别
│       └── schema_recall_evals/  # SQL Schema Recall
├── extensions/                   # 被测 MCP Server 的评测副本/快照（当前为空）
│   ├── vfssql-mcp-server/
│   └── example-mcp-server/
└── ...
```

每个评测任务目录自包含：`datasets/`（数据与标注）、`scripts/`（适配与评分）、`reports/`（结果）、`.tmp/`（临时）。需要冻结工具响应或数据实例时，增加 `fixtures/`。

### 2.1 标准骨架

```text
<name>_evals/
├── datasets/
│   ├── <prefix>_master_v01.jsonl          # 汇总集
│   ├── FC_functional_correctness/         # 功能正确性
│   ├── PQ_process_quality/                # 过程质量
│   ├── EC_efficiency_cost/                # 效率与成本（配置/预算）
│   ├── RS_robustness_safety/              # 鲁棒性与安全
│   └── EA_experience_alignment/           # 体验与对齐
├── fixtures/                              # 可选：冻结工具响应、实例清单
├── scripts/                               # 适配、evaluator、聚合、导出
├── reports/                               # 按 run 分目录
└── .tmp/                                  # 可丢弃中间产物，不入库
```

> 方案文档中的 `evaluation/`（`metrics.py`、`judge_rubrics.yaml`、`evaluation_config.yaml`）在本目录统一落到 `scripts/`，不另建 `evaluation/`。

### 2.2 命名

- 数据集文件：`{prefix}_{一级维度}_{二级评测项}_v{版本号}.jsonl`，例如 `intent_FC_result_correctness_v01.jsonl`。
- Langfuse 数据集名：`<任务>_evals/<维度目录>/<文件名去扩展名>`，例如 `intent_evals/FC_functional_correctness/intent_FC_result_correctness_v01`。
- DatasetItem ID：`<dataset_slug>_<四位序号>`，项目级唯一、不能跨数据集复用，例如 `intent_fc_result_correctness_v01_0001`。
- 实验目录：`reports/<YYYYMMDD>-<评测>-<变体>-<序号>/`，例如 `reports/20261110-sqlgen-qwen38-generate-only-01/`。
- 版本号：数据/配置/规则用 `v01`、`v02`；意图修订用 `r01`、`r02`；重复运行用 `rep01`。不要用“最终版”“新”之类不可追溯的名字。

### 2.3 当前状态

| 评测 | 目录 | 方案 | 数据 | 脚本 | 报告 |
| --- | --- | --- | --- | --- | --- |
| 端到端 SQL | `task/end-to-end/SQL/` | `01`（占位） | 81 题 JSON（已入库） | 待建 | 待建 |
| SQL Schema Recall | `task/process/schema_recall_evals/` | `03-...SQL Schema Recall 评测.md` | 待建 | 待建 | 待建 |
| 意图识别 | `task/process/intent_evals/` | `03-...意图识别评测.md` | 待建 | 待建 | 待建 |
| 歧义澄清 | 待建 `clarify_evals/` | `03-...歧义澄清评测.md` | 规划 | 规划 | 规划 |
| 业务检索（意图前） | 待建 `retrieval_pre_intent_evals/` | `03-...业务检索能力评测-意图前.md` | 规划 | 规划 | 规划 |
| 业务检索（意图后） | 待建 `retrieval_post_intent_evals/` | `03-...业务检索能力评测-意图后.md` | 规划 | 规划 | 规划 |
| SQL 生成 | 待建 `sql_generation_evals/` | `03-...SQL 生成评测.md` | 规划 | 规划 | 规划 |
| 业务检索（综合） | — | `03-...业务检索能力评测.md` | 拆分说明/沿革，以意图前、意图后为准 | — | — |

空目录不会进入 Git；新建任务目录时至少放一个 `README.md` 或 `.gitkeep`，否则合并后目录会消失。

Harness 适配状态单独维护：DeerFlow、Codex、Claude Code 已按本机 CLI `--help` 核对为 `verified`；OpenCode、Pi Agent 未安装，配置为 `status: draft`、`enabled: false`，不参与正式对照。

## 3. 评测体系

### 3.1 端到端与过程评测

- **端到端**：只发原始问题，评最终业务效果（结果是否正确、任务是否完成）。它无法定位错误环节。
- **过程评测**：按 DataAgent 链路拆分模块，固定上游输入，隔离其他模块的错误，回答“问题出在意图、检索、生成还是执行”。
- 过程评测的结论不能替代端到端结论；两者分别报告，不合成一个总分。

```text
用户问题
  → [意图前检索] → 意图识别 → 歧义澄清
  → [意图后检索] → SQL 生成 → SQL 执行 → 结果解释
  → 端到端（只看最终业务结果）
```

### 3.2 模块边界

| 评测 | 评什么 | 不评什么 |
| --- | --- | --- |
| SQL Schema Recall | `fast_search` 单工具的召回、排序、值映射、稳定性；隔离 Agent | Agent 如何使用工具、SQL 生成 |
| 意图识别 | 槽位完整性/准确性、`publish_intent_label` 调用与参数 | 是否该追问、检索、SQL |
| 歧义澄清 | `ask_clarification` 的触发、问题质量、多轮收敛 | 槽位提取、检索、SQL |
| 业务检索（意图前） | 意图发布前的知识检索、来源判断、交接 | Schema 映射、SQL |
| 业务检索（意图后） | 已发布意图后的 Schema/值/关联检索与交接 | 意图识别、SQL 生成 |
| SQL 生成 | 生成决策、语法/方言、Schema/参数、业务约束、执行结果、反馈纠错 | 意图、检索、最终答案解释 |
| 端到端 | 最终业务结果与任务完成 | 内部环节归因 |

关键依赖：意图识别、歧义澄清、业务检索共享同一批业务任务和冻结轨迹；SQL 生成使用固定的已确认意图与检索依据；Schema Recall 与 Agent 解耦，单独调用 MCP。

后续按链路继续补充：工具调用评测、Skill 评测、服务稳定性评测等。新增模块先写 `docs/03-...` 方案，再建目录和数据，不先写脚本。

### 3.3 五大维度

所有过程评测统一按五个维度组织数据集与指标：

- **FC 功能正确性**：结果、槽位、生成/调用决策是否正确。
- **PQ 过程质量**：步骤、纠错、上下文利用、信息完整性。
- **EC 效率与成本**：Token、延迟、工具调用次数、单任务成本。
- **RS 鲁棒性与安全**：一致性、异常恢复、抗注入、幻觉、越权、合规。
- **EA 体验与对齐**：清晰度、可解释性、语气、用户满意度（Rubric 或人工）。

指标口径隔离：不同评测中同名指标（Recall、Precision、F1、Accuracy、延迟）的定义与分母不同，禁止跨评测合并成一个分数。Schema Recall 的 Recall/Precision 以 RAGAS `IDBasedContextRecall` / `IDBasedContextPrecision` 为准，不要为接入 Langfuse 再写第二套同义实现；Langfuse 只负责记录 Score 与实验关联。

### 3.4 Langfuse 离线评测流程

推荐链路：

```text
Langfuse Dataset
  → Experiment Runner（dataset.run_experiment）
  → Task Function：DatasetItem → Agent 输入
  → Agent CLI / Agent 应用：正常执行，生成 Trace
  → Task Function 返回实际 Output
  → Item Evaluators / Run Evaluators
  → Scores + Dataset Run（Langfuse Experiments 页面）
```

官方模型：SDK Experiment Runner 可以运行现有应用，应用仍在自己的执行环境中运行；Task Function 只负责把 Dataset Item 转换成应用输入，并返回应用输出。不要为了评测改造 Agent 的生产入口。

**Trace 关联（关键）**：Experiment Runner 会为每个 Dataset Item 创建一个根 observation，并建立 Dataset Run Item 关联，至少包含：

| 关联项 | 作用 |
| --- | --- |
| Dataset Item ID | 对应数据集中的哪条测试样本 |
| Experiment Run ID | 对应哪一次实验运行 |
| Trace ID | 对应 Agent 实际执行产生的 Trace |
| Input / Expected Output / Output | 该样本的输入、Gold 和实际输出 |

只有这条关联存在，Trace 才会出现在 Experiments 页面并对应到 Dataset 样本。Task Function 内同进程调用 Agent 时，Agent 的 span 会自动继承当前 experiment item 的上下文；**调用独立 Agent CLI 时不会自动继承**，必须显式传递 trace 上下文。

推荐实现（Experiment Runner 作为调度器，Task Function 内调用 Agent CLI）。下例中的 `run_agent_cli` 是项目适配器占位，不是 Langfuse 内置函数：

```python
from langfuse import get_client

langfuse = get_client()


def task(*, item, **kwargs):
    """把 DatasetItem 转成 Agent CLI 输入，并返回实际输出。"""
    # Experiment Runner 已为当前 item 建立根 observation
    trace_id = langfuse.get_current_trace_id()
    parent_observation_id = langfuse.get_current_observation_id()

    # 通过环境变量把实验上下文传给独立进程；不要只写进日志
    output = run_agent_cli(
        input=item.input,
        env={
            "LANGFUSE_TRACE_ID": trace_id,
            "LANGFUSE_PARENT_OBSERVATION_ID": parent_observation_id,
        },
    )
    return output
```

Agent CLI 侧在自己的根 observation 上接入同一 Trace：

```python
import os

from langfuse import get_client

langfuse = get_client()

with langfuse.start_as_current_observation(
    name="agent-run",
    trace_context={
        "trace_id": os.environ["LANGFUSE_TRACE_ID"],
        "parent_span_id": os.environ["LANGFUSE_PARENT_OBSERVATION_ID"],
    },
):
    ...  # Agent 正常执行，LLM/工具调用成为该 Trace 的子 span
```

约束：

- `LANGFUSE_TRACE_ID` / `LANGFUSE_PARENT_OBSERVATION_ID` 是本项目约定的传递变量，不是 SDK 内置环境变量；CLI 必须显式读取并传给 `trace_context`。
- CLI 无法接受 trace 上下文时，优先改为同进程调用，或在报告中明确标注“只有输出、没有 Agent Trace”；不要只在报告里写一个 `trace_id` 就宣称已关联。
- `trace_id` 必须是 32 位小写十六进制；`parent_span_id` 必须是 16 位小写十六进制，否则 SDK 会忽略并产生新 Trace。
- 每次 item 执行都要重新读取当前上下文，不能在进程启动时缓存；并发实验下缓存会串 Trace。
- 实验结束后 `langfuse.flush()`，确认 Trace、Scores 和 Dataset Run Item 都已上报。

### 3.5 在线评测流程

在线评测复用生产 Trace，不重新跑数据集：

```text
DeerFlow 生产请求
  → Langfuse Trace / Observation（LANGFUSE_TRACING 已接入）
  → 在线 evaluator（LLM-as-a-Judge / 自定义规则，按采样与过滤触发）
  → Score 写回 Trace / Observation
  → 看板与告警；问题样本可加入 Dataset 供离线回归
```

- 在线评测只做监控和抽样，不替代离线 Dataset Experiment；两者的分母、采样和结论分开报告。
- 在线 evaluator 的 Prompt、模型和版本要固定并记录；Judge 结果需要人工抽检校准。
- 生产 Trace 含真实业务数据时，注意脱敏和访问权限，不把原始 Trace 直接导出到公开 CI。

### 3.6 多 Harness 对照实验

评测对象不限于 DeerFlow。对照实验需要在同一数据集、同一题面、同一模型（Harness 支持时）下运行 Codex、Claude Code、OpenCode、Pi Agent 等 Harness Agent，并分别报告结果。

配置分三层，职责不重叠：

| 文件 | 负责什么 |
| --- | --- |
| `evals.yml` | 全局默认值、Harness 注册表、实验定义（数据集 + Harness 列表 + evaluator 列表）、对照公平规则 |
| `harnesses/<name>.yaml` | 单个 Harness 的调用方式、事件解析、Trace 覆盖、MCP 渲染、能力声明 |
| `harnesses/README.md` | 适配器契约（`prepare` / `invoke` / `normalize` / `trace` / `cleanup`）与 `NormalizedResult` 字段 |
| `schemas/*.json` | 上述配置的 JSON Schema，提交前校验 |

当前注册的 Harness：

| Harness | 状态 | 非交互调用 | 事件流 | Trace 覆盖 | 备注 |
| --- | --- | --- | --- | --- | --- |
| DeerFlow | `verified` | `python -m deerflow.tui.cli --json` | JSONL | `bridged`（同进程 `DeerFlowClient` 可 `native`） | 基准；MCP 用 `mcp_extensions.json` |
| Codex | `verified` | `codex exec --json` | JSONL | `bridged` | `--ephemeral` 隔离会话 |
| Claude Code | `verified` | `claude -p --output-format stream-json` | stream-json | `bridged` | 关闭自带 OTel，避免第二条无关 Trace |
| OpenCode | `draft` | `opencode run` | 待核对 | 待核对 | 本机未安装，`enabled: false` |
| Pi Agent | `draft` | `pi -p` | text | `output_only` | 本机未安装，`enabled: false` |

统一结果：所有 Harness 的输出先归一化为 `NormalizedResult`（`harness`、`harness_version`、`model`、`status`、`final_output`、`tool_calls`、`usage`、`elapsed_seconds`、`trace_id`、`trace_coverage`、`artifacts`、`error`），evaluator 只消费该结构，不直接解析各家 CLI 原始日志。

Trace 覆盖模式：

- `native`：Agent 自己写 Langfuse Trace，且能挂到 experiment item 的 trace 上下文。
- `bridged`：Agent 只输出事件流；适配器把事件写成 experiment item Trace 下的子 span，并保留 `trace_id`。Harness 自带的独立 Trace 只能作为附件记录，不能冒充实验关联。
- `output_only`：既无 Trace 也无事件流，只做输出级评分；报告中必须写明“无 Agent Trace”，不得声称已关联。

公平规则（`evals.yml -> comparison`）：

- 同一数据集版本、同一题面、同一超时与并发；模型仅在 Harness 支持时对齐，不支持则记录差异。
- 能力缺失（MCP、Skill、子 Agent、JSON 事件）按 `missing_capability: skip` 跳过并在 `metrics.json` 记 `skipped`，不得用替代实现伪造。
- 结果按 Harness、按维度分别报告，不做单一总分排名；跨 Harness 只比较同口径指标。
- 每个 Harness 的版本、调用参数、系统 Prompt、权限模式写入 `manifest.json`。

新增 Harness 的步骤：

1. 用 `--help` 核对非交互调用、JSON 事件、模型参数、MCP 配置和超时行为，记录版本号。
2. 按 `schemas/harness.schema.json` 新建 `harnesses/<name>.yaml`，能力未知的字段先保守填 `false`。
3. 在 `evals.yml -> harnesses` 注册路径，并在对照实验的 `harnesses` 列表中加入。
4. 实现事件解析与 `normalize`，产出 `NormalizedResult`；跑 2–3 条小样本，核对输出、用量与 Trace。
5. 通过后把 `status` 从 `draft` 改为 `verified`，再进入正式实验；未验证的 Harness 不参与结论。

## 4. 数据集规范

### 4.1 Langfuse DatasetItem

数据集以 Langfuse `DatasetItem` 为最小单位；业务字段嵌套，不平铺到顶层。

| 字段 | 要求 | 说明 |
| --- | --- | --- |
| `id` | 项目必填 | 项目级唯一，不能跨数据集复用 |
| `input` | 项目必填 | Agent 可见的输入 |
| `expected_output` | 项目必填 | Gold；只给评测端 |
| `metadata` | 项目必填 | 分类、难度、维度、规则、来源 |
| `source_trace_id` / `source_observation_id` | 可选 | 从真实轨迹构造时填写 |
| `status` | 可选 | `ACTIVE` / `ARCHIVED` |

- 本地 JSONL 是版本化源文件；加载脚本逐行调用 `create_dataset_item` 写入 Langfuse。Langfuse 不直接消费本地 JSONL。
- `expected_output`、隐藏实例、评分规则、Judge 标准不得拼进模型 Messages。
- 复制到 master 与专项数据集时使用不同的 DatasetItem ID；按 `source_case_id + target_turn` 去重统计。

### 4.2 端到端 SQL 数据集（现有）

`task/end-to-end/SQL/datasets/` 下三档难度，共 81 题：

| 文件 | 题数 | ID 前缀 |
| --- | --- | --- |
| `simple-evals-data/simple-evals-business.json` | 36 | `simple-B-NNN` |
| `medium-evals-data/medium-evals-business.json` | 34 | `medium-B-NNN` |
| `high-evals-data/high-evals-business.json` | 11 | `high-B-NNN` |

每条样本字段：

- `id`、`question`、`question_enum`（4 条同义改写）、`gold_sql`。
- `target_tables`、`target_columns`（`{table, column}`）、`related_ddl`（`{table, description, ddl}`）。
- `checkpoints`：结构化判定条件，含 `filters`、`joins`、`aggregations`、`group_by`、`having`、`order_by`。

使用规则：`gold_sql`、`checkpoints`、`target_*` 是隐藏判定，不给 Agent；Agent 只看到当前问题。现有文件是 JSON 数组，接入 Langfuse 前由加载脚本转换为 DatasetItem（`input` 放问题，`expected_output` 放 Gold/checkpoints，`metadata` 放难度与来源），不要直接当 JSONL 逐行读取。新增题目沿用同一 schema，并同步更新题数统计。

**已知数据问题：`question_enum` 与 Gold 不匹配。** 每条样本只有一个 `gold_sql` 和一套 `checkpoints`，它们只对应 `question_enum[0]`（即 `question`）。`question_enum[1..3]` 改动的是日期范围、过滤条件等业务约束（例如 `simple-B-001` 的 enum[0] 是 2023 全年，enum[1] 是 2024-03-01～2024-06-30，而 `gold_sql` 仍是 2023-01-01～2024-01-01）。因此当前**不能**把 4 条改写各建一条 DatasetItem 复用同一 Gold，否则 3/4 的样本 Gold 错误。需要多改写时，为每条改写单独补 Gold，或只使用 enum[0] 并在 `metadata` 标注。

### 4.3 过程评测数据集

字段以各方案 `3-2 评测样本集要求` 为准，不在本文件重复。共同要求：

- `input` 只放评测起点前 Agent 可见的内容；`expected_output` 放决策、Gold、允许终态。
- `metadata` 至少包含 `task_type`、`difficulty`、`eval_dimensions`、`scenario_tags`、`evaluation_rules`；跨模块共享的样本再加 `source_case_id`、`target_turn`。
- 多轮样本把历史冻结进 `input.conversation_history` / `trace`，当前轮作为评分对象。
- 负样本必须显式标注空 Gold 或“不应调用/不应生成”，不能用缺标注代替。

### 4.4 质量门槛

各方案有自己的覆盖率与分布要求，以下为共同底线；具体数值以对应文档为准：

- 标注：≥2 人独立标注，Cohen's κ > 0.8 才采纳（Schema Recall 明确要求）。
- 分布：难度与任务类型分别分层报告，单类任务 ≥10 条。
- 规模：Schema Recall ≥100 条；歧义澄清、业务检索、SQL 生成首期 ≥200 条；意图识别按 T01–T09 分布建设，具体以方案为准。
- 负样本：Schema Recall 占 10%–15%；检索类 `must_search` / `must_not_search` 各 ≥20 条，`may_search` 单列。
- 覆盖：Schema Recall 要求每个物理表 ≥2 个样本、每个字段 ≥1 个、每对关联路径 ≥1 个；歧义澄清要求每个歧义类型 ≥10 条、多歧义组合 ≥5 条、无歧义对照 ≥30 条、多轮 ≥30 条。
- 验收：字段齐全、ID 唯一、Gold 可执行/可核对、无越权对象；只做格式校验不算通过质量门槛。

### 4.5 版本与冻结

- 数据集发布后打版本（`v01`）并记录时间戳/哈希；修改标注必须升版本，不能原地改已发布文件。
- 实验读取固定版本（`langfuse.get_dataset(name=..., version=...)`），报告中记录数据集时间戳。
- 已产生的预测、逐样本评分和报告不改写；修正通过新 run 或新 `rep` 目录。
- 真实患者数据、凭据、完整原始轨迹不入库；合成/脱敏数据方可入库。

## 5. 脚本规范（只写适配层）

### 5.1 职责

一个评测任务的脚本通常只有四类：

1. **数据集加载**：JSONL → `create_dataset_item`（幂等、可重跑、不重复建 ID）。
2. **task**：按固定起点驱动 Agent / Gateway / MCP，冻结本次输出（首版、最终版、工具调用、终态）；跨进程调用 Agent CLI 时传递 experiment item 的 trace 上下文（见 §3.4）；多 Harness 对照通过 `harnesses/<name>.yaml` 适配，不在 task 里写死某一家 CLI（见 §3.6）。
3. **evaluator**：逐样本评分；程序规则优先，语义判断用 Teacher-LLM + 人工抽检。
4. **run_evaluator / 报告**：检查样本完整性、分母、版本，聚合维度/难度/变体，导出报告。

### 5.2 约定

- 适配函数命名：`build_<eval>_harness`、`run_<eval>_agent`、`evaluate_<eval>`、`aggregate_<eval>_results`。这些是项目适配器，不是框架内置 API。
- 依赖锁定（`pyproject.toml` / lock 文件）；报告记录实际 `langfuse`、`ragas` 版本。接口基线见 docs：Langfuse `4.15.2`、RAGAS `v0.4.3`；升级需重新验证。
- 评测依赖放在评测侧，不引入 MCP/SDK 生产检索路径。
- Langfuse 的 N/A 语义：`ScoreBody.value` 不接受 `None`；用 `{metric}_applicable=0/1` 标记适用性，仅在适用时发布数值 Score，真实 0 必须保留。
- 失败调用进入调用数与错误率分母；报告同时给出每项指标的适用数、有效数、失败数。
- 证据分层：`agent_visible`（Agent 实际看到的反馈）与 `offline_validation`（评测端隐藏执行）分开记录，不能把离线耗时算作 Agent 延迟。

### 5.3 环境

- Windows 使用 PowerShell；Python 用 `backend/.venv/Scripts/python.exe -X utf8 -B` 或独立 uv 环境。
- 真实联调前先启动 Gateway（`make dev`，8001）；固定模型、Prompt、Skill、工具定义版本。
- Langfuse 凭据通过环境变量注入：`LANGFUSE_TRACING=true`、`LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`、`LANGFUSE_BASE_URL`。DeerFlow 已在 graph 根节点接入 Langfuse tracing（由 `LANGFUSE_TRACING` 启用），评测侧读取同一批 Trace，不另建观测体系。
- 评测用 MCP 配置放 `evals/mcp_extensions.json`；凭据只写环境变量引用（如 `$VFSSQL_OAUTH_CLIENT_SECRET`），不写明文。
- 禁止在脚本、参数日志、报告中写入凭据、Cookie、认证头。

### 5.4 禁止

- 自建第二套评分引擎、指标调度或实验存储；不要把旧脚本的分数包装成 Langfuse/RAGAS 结果。
- mock 掉框架调用后宣称“已接入”。
- 用文本 Exact Match 代替业务语义/结果比较；用单实例结果代替多实例 TSA。
- 从多次 SQL 中挑一条正确的算最终成绩；失败样本重跑择优。
- 把 N/A 当 0 或 100；把预览当完整结果；把执行成功当业务正确。

## 6. 运行与记录

### 6.1 一次实验必须固定

- 数据集名 + 版本时间戳 + 哈希。
- Harness 名称与版本、调用参数、Trace 覆盖模式（`native` / `bridged` / `output_only`）。
- 模型、`reasoning_effort`、Prompt、Skill、工具定义、数据库方言/实例、比较器、Judge 与评分规则版本。
- 并发、超时、重试与预算。
- 代码版本（commit）与框架版本。

### 6.2 报告结构

`reports/<run_id>/` 至少包含：

```text
report.md          # 人读：结论、分组指标、失败归因
metrics.json       # 机读：总体/分组指标与分母
manifest.json      # 版本与配置：数据集、模型、Prompt、Skill、工具、框架、commit、哈希
cases.jsonl        # 逐样本：输入摘要、输出、评分、失败原因
raw/               # 原始轨迹/响应（大体积、含敏感信息时不入库）
```

报告必须区分：首版/最终版、主集/专项、frozen/live、不同 `intent_revision`、不同难度；给出适用数、有效数、失败数，不静默丢弃样本。

### 6.3 迭代

- 每轮只改一个变量（Prompt / Skill / 工具 / 检索策略），其余固定；对照实验使用同一冻结数据集。
- 版本对照看同一指标的分母与分布，不只看平均值。
- 结论写回 `docs/` 对应方案或 `reports/`，不要只留在对话里。

### 6.4 入库范围与忽略

- 入库：`datasets/` 下的合成/脱敏数据、`reports/<run>/` 下的 `report.md`、`metrics.json`、`manifest.json`、`cases.jsonl`、`docs/`、评测配置（`evals.yml`、`mcp_extensions.json`）与本文件；配置中的凭据只允许环境变量引用。
- 不入库：`.tmp/`、`reports/**/raw/`、`cache/`、`.pytest_cache/`、`*.log`、`.env`、Langfuse 原始导出、真实患者数据、凭据与认证头。
- 上述规则已写入根 `.gitignore`；新增忽略项时同步维护，不要忽略整个 `datasets/` 或聚合报告。新增 JSONL 时在 `.gitattributes` 补 `*.jsonl text eol=lf`，文本统一 UTF-8。

## 7. 新增评测任务清单

- [ ] 在 `docs/` 写方案：目标、边界、环境、五维指标、数据集字段、Langfuse 接入、坑。
- [ ] 建 `task/process/<name>_evals/`（或 `task/end-to-end/<name>/`）及 `datasets/ scripts/ reports/ .tmp/`，放 `README.md` 防目录丢失。
- [ ] 按命名规范产出 `v01` JSONL；跑覆盖率、分布、标注一致性、负样本、规模门槛。
- [ ] 写数据集加载脚本，建 Langfuse Dataset 并校验 item 数/ID。
- [ ] 写 task + evaluator + run_evaluator；程序规则优先，语义判断留人工抽检。
- [ ] 对照实验先确认 `evals.yml` 的 Harness 注册表与 `harnesses/<name>.yaml` 可用；草稿 Harness（`status: draft`）先用小样本核对 CLI 参数和 Trace，再转 `verified`。
- [ ] 固定版本跑通小样本，再跑正式集；产出 `report.md` + `metrics.json` + `manifest.json` + `cases.jsonl`。
- [ ] 在 Langfuse Experiments 页面核对 Dataset Item ID、Experiment Run ID、Trace ID 和 Input/Expected Output/Output 均已关联；独立 CLI 必须验证 trace 上下文传递。
- [ ] 更新 `docs/`、本文件的状态表；如新增顶层目录，同步根 `AGENTS.md`。
- [ ] 测试、review、合并回 `dev`。

## 8. 常见坑（摘自方案文档）

- SQL 文本不同就判错：别名、JOIN 顺序、CTE 写法可以不同；Exact Match 只作辅助。
- SQL 能执行就认为业务正确：统计单位、漏过滤、JOIN 放大都可能执行成功。
- 单份数据碰巧相同：用多实例区分 `COUNT(*)` 与去重计数。
- 把预览当完整结果：行数/字符截断、零行预览与真正空集分开。
- 用 Gold 帮 Agent 纠错：隐藏差异、参考 SQL 不能作为本次反馈。
- 从多次尝试挑最好：按 Agent 实际最终交接评分，记录修正退化。
- 沙箱拦截就算安全通过：违规尝试仍要计入安全事件。
- 只汇总成功样本：失败、N/A、未执行都要有明确分母。
- 把 N/A 写成空 Score：用 `*_applicable` 标记，数值 Score 仅在适用时发布。
- 只记录 `trace_id` 就算关联：以 Langfuse Dataset Run Item 为准；独立 Agent CLI 不传 `trace_context` 会产生独立 Trace，Experiments 页面看不到 Agent 调用链。
- 混合提示条件：自主发现 Skill 与显式 Skill、不同 `intent_revision` 分组报告，不能合并成一个“平均分”。
- 跨 Harness 直接排名：各家系统 Prompt、工具集、默认轮数不同，能力缺失时按 `missing_capability` 跳过并记录；按 Harness、按维度分别报告，不做单一总分排名。
- 把 Harness 自带 Trace 当实验 Trace：只有挂到 Dataset Run Item 上的 Trace 才算关联；`bridged` 模式必须由适配器把事件写成子 span，`output_only` 必须明确标注无 Trace。

## 9. 相关文档与代码

- 根指南：[../AGENTS.md](../AGENTS.md)
- 后端指南：[../backend/AGENTS.md](../backend/AGENTS.md)
- 旧评测资产：[../backend/evaluates/AGENTS.md](../backend/evaluates/AGENTS.md)
- Langfuse tracing 接入：[../backend/packages/harness/deerflow/tracing/AGENTS.md](../backend/packages/harness/deerflow/tracing/AGENTS.md)
- 评测方案：[docs/](docs/)
- Harness 适配：[harnesses/README.md](harnesses/README.md)、[evals.yml](evals.yml)、[schemas/](schemas/)
- 公共 API 变更：[../docs/guide/used-api.md](../docs/guide/used-api.md)
- Langfuse 官方文档：
  - [Datasets](https://langfuse.com/docs/evaluation/experiments/datasets)
  - [Experiments via SDK](https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk)
  - [Experiments Data Model](https://langfuse.com/docs/evaluation/experiments/data-model)
  - [Scores Data Model](https://langfuse.com/docs/evaluation/scores/data-model)
