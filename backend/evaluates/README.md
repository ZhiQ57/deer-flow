# DeerFlow VFSSQL 独立对话评估

本目录评估真实 DeerFlow Gateway、指定模型和已接入 VFSSQL 的组合效果。一题一个新 thread，固定并发 1，324 题只发送原始问题；结果统一保存在本目录 runs/。执行计划见 evaluation-plan.md。

## 最新：缺检索样本显式 Skill 复测（2026-10-23）

### 2026-10-26：模型服务异常恢复

#### 同三题接管与324题四组对照

三题已自然完成：case-0048、case-0044、case-0047，全部有最终答复、原生Skill激活、完整采集和证据重放；合计16次SQL成功、7次成功检索调用。旧workflow_validation.json要求每题都检索，因此因两题没有检索而未通过。服务恢复使用独立的provider_workflow_validation.json验收同三题；检索行为仍按原业务标准计分，不再增加模型联调题。

`recovery_suite.py --run-dir runs/20261026-infra-retry`归档新控制器并接续已准备的170题，使用原_private/frozen_evaluator，保持采集、题面、模型和预测不变。需从临时进程环境提供原有凭据；已运行的控制器会拒绝重复启动。控制器30秒心跳，完成后自动调用独立comparison_metrics.py。

用户指定的四组表保存在`runs/20261026-infra-retry/comparison_metrics/report.md`，精确数值见metrics.json，逐题指标及检索轮数见case_metrics.json/csv，来源及哈希见input_manifest.json。全部有效预测冻结前，DeerFlow全量指标为null；可查看154题原有效结果及补测进度，不能将模型服务错误计作业务失败。

- 首次候选：首个成功非空响应内部去重顺序的R@1/3/5/10；累计候选Recall/Precision、逐题Macro-F1、候选完整率。
- 结构：表Recall/Precision、逐题Macro-F1、目标字段Recall和逐题0/1完整率。
- SQL：首条/末条真实执行无错率，检索完整且最终执行成功率，进一步要求最终SQL使用全部目标表字段的联合率；分母均324。
- 效率：全题平均保存耗时、P50、P95、平均MCP，以及主成功题平均耗时和MCP；另存宿主观察/完整采集耗时和基础设施故障尝试成本。

Codex基线取自原324题冻结结果并核对sample_id和哈希。“保存耗时”指已保存的elapsed_seconds，Codex为共享会话宿主Date.now观察，DeerFlow为Gateway整次独立对话运行时间，报告明确计时范围差异。当前组合最终是79题自主发现Skill与245题显式Skill，分组来源单列，不能描述成统一提示的一次全新324题运行。只用记忆而无合格检索的题不计MCP主成功。

用户要求不能将运行异常直接判作业务失败。已核验上轮168个错误样本全部在调用MCP前收到模型HTTP502，共504条模型错误事件；原81题保留组另有case-0072、case-0113两道未完成运行，一并恢复，共170题。原正常完成的154题保留，原报告及错误轨迹保持不变，原目录新增status_correction.md指向修正结果。

```powershell
& backend/.venv/Scripts/python.exe -X utf8 -B backend/evaluates/retry_retrieval.py `
  --recover-infrastructure `
  --source-run backend/evaluates/runs/20261023-skill-retry-v2 `
  --pilot-dir backend/evaluates/runs/20261026-infra-pilot `
  --run-dir backend/evaluates/runs/20261026-infra-retry
```

先由DeerFlow运行三题验证，再自动后台恢复正式170题。`provider_recovery.py`只重试模型服务异常，原问题与显式Skill提示不变；每次重试建立独立新会话，旧错误尝试按原生ID、原始字节和哈希保留在infra_attempts。服务错误自然终止后暂停当前队列，按60、120、300、600秒退避，持续每30秒心跳；当前题未恢复前不开始下一题。没有模型取消调用，也没有业务失败择优重试。

运行时查看recovery_status.json的等待原因及next_retry_at_utc；comparison_report.md和comparison_summary.json明确154题旧有效结果、新有效但待统一评分记录、待恢复题数。只有170题全部产生有效预测、统一冻结并评分后才更新全量324题最终业务指标。正常完成但未检索或SQL不合格的题仍按业务失败评估。模型错误尝试及其耗时另外保留，不混入业务失败分母。

用户要求 MCP 能力评估排除只用记忆完成任务的情况。新协议选择原轮没有定位或内容读取工具的 243 题（240 题无 VFS，3 题只有目录初始化），使用 DeerFlow 原生 `/vfssql-retrieval` 激活并增加固定一句“必须使用 vfssql-retrieval skill。”，其后为原问题。该激活会由宿主注入 Skill 正文，采集器按原生激活事件的名称和哈希核验；不会复制答案、目标表列或额外评估提示。

`retry_retrieval.py` 先归档完整旧轮，再建立三题联调和 243 题正式复测两个新目录。三题覆盖单表、多表和长结构场景，由 DeerFlow/Qwen 实际执行；验证全部请求、Skill 注入、成功检索、自然终态、最终答复、证据重放及评分文件，并确认至少一次 SQL 实际执行成功。通过后自动启动正式后台，无需保持 Codex 本轮会话。流程成败与三题业务评分分别记录。

```powershell
& backend/.venv/Scripts/python.exe -X utf8 -B backend/evaluates/retry_retrieval.py `
  --source-run backend/evaluates/runs/20261022-qwen38-ultra-native-unlimited-01 `
  --pilot-dir backend/evaluates/runs/20261023-skill-pilot `
  --run-dir backend/evaluates/runs/20261023-skill-retry
```

凭据仍只来自 VFSSQL_EVAL_EMAIL / VFSSQL_EVAL_PASSWORD。此命令创建隐藏独立编排进程：`suite_status.json` / `suite.log` 记录三题验收及自动转批；各轮 `console.log`、`pipeline_status.json`、`supervisor_status.json` 记录单题进度和 30 秒心跳。`status_evaluation.py --run-dir <正式目录>` 同时展示套件阶段和三题进度。原轮完整归档到短路径 `runs/_archive/20261022-native/`；目录被 Windows 打开句柄占用时使用冻结文件硬链接快照，再只清除原路径 243 题的轨迹和预测，81 题保留。原路径的 active_data_cleanup.json 指向完整历史归档，原324题报告应从归档读取。

模型、账号、Ultra/high、并发 1 和无限等待保持既定设置。MCP 主成功仍要求真实检索完整覆盖目标表列、最终 SQL 成功并有自然语言最终答复；只列目录或记忆直接生成 SQL 单独标记。正式 report.md 只统计新 243 题；完成后另生成 comparison_report.md，将保留的原 81 题与新 243 题分组汇总，注明提示条件不同。未使用工具的题仍保留为失败，不循环重做择优。

以下章节保留上一轮仅原问题协议和离线复评的说明。

本次最终活动目录为 `runs/20261023-skill-retry-v2`；验收目录为 `runs/20261023-skill-pilot-v2`。首次联调发现宿主按归一化换行后的正文取哈希、文件快照按原始 CRLF 字节取哈希，评估侧现将 skill_text_sha256 与 skill_sha256 分开保存。通过 `--source-archived --adopt-pilot <原三题目录>` 等待原三题自然结束并重放同一 thread/run 的原始证据；原错误校验快照保留，不重新发送三道测试问题。2026-10-23 09:59:55 三题全部验收通过，09:59:56 正式243题自动启动。

## 1、固定协议

- 模型 Qwen3.8-27B-FP8-Local-cloud；Ultra（思考/计划/子代理均开启）；reasoning_effort=high。
- 等待模型自然运行结束，不设单题执行时间上限，不因耗时或调用次数中断。24 次 VFSSQL MCP、4 次 sql_execute 仅为观察阈值；所有主/子代理失败、重试和枚举计入。
- 数据集 324 题、61 family；seed=20261015；SHA-256=e6d2778929830da9d76ca09f981789d3929692baeeaf63964f477d28734bcae4。
- Skill 由 DeerFlow 从 skills/public/z-vfssql-retrieval 自主发现；评估器只冻结文件哈希和审计快照，不注入 Skill、题号、预算、输出格式或任何额外提示。
- 新会话只有一条 human 消息，其 content 与样本 question 逐字一致。模型为自然语言回答；不会追加让模型补 JSON 或 SQL 的消息。
- 主成功：真实检索完整覆盖目标表字段，最后一次实际 SQL/参数成功执行，并且运行成功、有最终答复。空结果允许通过；SQL 来自 sql_execute 请求，来源独立标注，严格 SQL 引用目标字段单列。
- find/grep 每次往返计一轮，结构读取另计；未完整覆盖的首次完成轮数为 null。

## 2、准备

以下命令在项目根目录的 PowerShell 执行，使用已有 backend/.venv，不安装额外服务：

```powershell
& backend/.venv/Scripts/python.exe -X utf8 -B backend/evaluates/prepare_dataset.py `
  --dataset 'D:/A-AICodeWork/ownmindforge/backend/evaluate/vfssql-evluate-data/vfssql-evaluation-dataset.v1.json' `
  --native-skill 'D:/A-PythonWork/AOpenGithub/deer-flow/skills/public/z-vfssql-retrieval' `
  --run-dir backend/evaluates/runs/20261022-qwen38-ultra-native-unlimited-01
```

样本已经迁移过时，可直接用下面的后台启动器准备并运行。代码、样本、原生 Skill 文件及参数都会冻结哈希；变化后须新建运行目录。原生 Skill 在运行中变化会暂停，避免混合版本。datasets/skills/runs 均不提交。--pilot 仍可准备三题联调，但既有三次联调已经完成，不需要重复。

## 3、执行

账号和密码分别从 VFSSQL_EVAL_EMAIL / VFSSQL_EVAL_PASSWORD 环境变量读取，不写入文件、参数日志或 HTTP 收据。默认 Gateway 为 http://127.0.0.1:8001。

```powershell
& backend/.venv/Scripts/python.exe -X utf8 -B backend/evaluates/run_deerflow.py `
  --run-dir backend/evaluates/runs/20261022-qwen38-ultra-native-unlimited-01
```

--limit N 只限制本次新处理题数，不改变冻结样本集合。再次运行会跳过冻结预测；对已受理 run 查询原 ID，不重新提交模型任务。新协议必须新建轮次，不能接着旧注入轮次混跑。旧轮恢复只能使用其 _private/frozen_evaluator 代码。

正式运行可直接使用 `run_pipeline.py --run-dir ...`。它会持续采集全部题目，全部预测冻结后自动离线评分并写 report.md；pipeline_status.json 记录 running/scoring/complete/needs_attention，标准输出只含题号、终态、次数和时间。

### 独立后台自动执行

```powershell
& backend/evaluates/start_evaluation.ps1 -RunDir 'D:/A-PythonWork/AOpenGithub/deer-flow/backend/evaluates/runs/20261022-qwen38-ultra-native-unlimited-01'
```

启动器准备正式 324 题并启动隐藏的独立 Python 进程，使用该轮归档代码持续运行。窗口或本轮 Codex 对话无需保持等待。凭据按提示输入，或由当前进程环境提供；不写入脚本。console.log/error.log 为后台输出。恢复同一目录时显式传入 -Resume；已有存活进程或已完成结果会拒绝重复启动。

启动器同时归档独立监督器，原冻结采集/评分代码不变。监督器每 30 秒向 console.log 输出 supervisor_heartbeat，supervisor_status.json 记录其存活状态。旧采集器若在题目自然结束后因 sql_learn 停批，监督器在原始证据重放审计通过后按执行前拒绝契约分类：McpError 参数拒绝、MCP sql_learnArguments 参数模型验证失败（覆盖字段错误和多个错误）、DeerFlow 延迟工具未启用，以及服务明确关闭/权限拒绝。SDK 的 disabled/forbidden 原生回执可不含 applied，但须匹配 sql_learning_disabled/learning_scope_required 固定错误码。全部学习调用均确认未执行时自动续跑下一题；原预测字节、失败判定及调用记录均保留，不重做题目。监督续跑原因、逐调用拒绝类型和预测哈希保存到 supervisor_incidents.jsonl。成功写入、pending 候选、输出校验或状态不明的调用不自动恢复。

无法自动恢复时，监督器会在 console.log 最后一行输出 event=evaluation_stopped、stopped=true、明确原因和已归档题数，同时保存 needs_attention 状态；完整堆栈仍在 error.log。正常运行时为 supervisor_heartbeat。以日志末尾事件和状态文件区分正常等待与退出。

只读查看进度：

```powershell
& backend/.venv/Scripts/python.exe -X utf8 -B backend/evaluates/status_evaluation.py `
  --run-dir backend/evaluates/runs/20261022-qwen38-ultra-native-unlimited-01
```

采集器没有取消模型运行的 API 调用。活动会话遇到 Gateway/事件接口故障会重连等待，记录累计观察时间，不会开始下一题；SQL 错误、模型慢、超过 1200 秒、MCP/SQL 调用多均不会触发中断。模型自然结束后归档其最终答复或服务错误；无最终答复仍计失败，记录后继续。知识写入、Skill/代码漂移或无法保存原始证据时停止后续评估，不打断当前模型。GET 和使用稳定 Idempotency-Key 的 run 请求对连接故障、429/502/503/504 做有界重试；活动运行的状态查询在这些重试用尽后仍持续等待。所有失败保留，不重跑择优计分。

2026-10-22 15:11:50（北京时间）旧冻结采集器在第 29 题后触发学习停批：sql_learn 返回 McpError: invalid tool arguments，参数验证拒绝，未成功写入。第 29 题自然 success、耗时 167.707676 秒，但无最终答复，仍为失败。此次通过监督器续跑同一目录、跳过原有 29 题；原始预测与冻结代码不变，没有新建轮次或重发已完成问题。旧 error.log 中的历史停批堆栈保留，最新运行状态以 pipeline_status.json/supervisor_status.json 和日志末尾为准。

15:54:30 第 36 题再次触发旧学习停批；此次为 deferred tool not promoted，DeerFlow 在调用 MCP handler 前直接拒绝。原监督器仅识别参数验证拒绝而退出，属于监督编排遗漏。本次补齐执行前拒绝分类，保留 36 题原记录并继续第 37 题；第 36 题已有自然最终答复，84.211772 秒，其未授权学习调用仍保留在评分诊断中。

18:00:43 第 95 题后再次停批：该题一条 deferred 拒绝、一条 ToolException 包装的 sql_learnArguments 输入模型校验异常，旧监督器没有识别后者。已核对 MCP Tool.run 在 validate_arguments 成功后才 call_fn，故本次没有调用知识写入函数。补齐整个输入模型校验类别和停止事件日志，保留原 95 题及其审计证据，继续第 96 题。

2026-10-22 用户要求移除全部中断预算后，从 native-01 接管首个已受理对话到 native-unlimited-01；只切换采集进程，原模型在 Gateway 内继续运行，未取消或重发问题。`--adopt-from` 仅允许输入、模型和原生 Skill 完全相同的原题轮次，保存源证据哈希，沿用 thread/run ID；不会合并旧注入轮的 16 题。恢复无限等待轮次仍用 `-Resume`，接管旧原题轮则使用新的目录及 `-AdoptFrom <原目录>`。

## 4、采集与审计

traces/case-XXXX/ 保存请求、thread/run 受理信息、原始 stream.sse、原始 http-bodies.bin、两组带字节位置/哈希/UTC/monotonic 时间的收据，以及工具已公开引用的 tool-results 文件。登录响应、认证头、Cookie 不进入记录器。

原生工具请求/结果按 namespace＋tool_call_id 关联。子代理卡片仅用于发现漏采，不能补造 ID 或被截断的返回。长结果恢复需依据本题工具实际返回的 `.tool-results` 文件引用。SSE、事件和分页 messages 正文均可重放；有确认终态及完整消息导出时，流末帧缺失只记 warning，原生工具请求/返回缺失仍使本题失败。自然语言最终答复原文保存在 answer_text，SQL 来源为 last_sql_execute_request；不强制树查询顺序或特殊输出格式。

每题 `elapsed_seconds` 优先记录 Gateway run.created_at 到终态 updated_at 的运行秒数，`timing_source` 明示来源；`observed_elapsed_seconds` 另记采集器从受理至观察终态的累计时间，`capture_elapsed_seconds` 包含归档耗时。服务时间缺失时回退至宿主 monotonic，并明确标注。这样接管采集器的空档不会混入服务实际运行时间。工具接收间隔不能当作服务端单次调用延迟。

每轮 `_private/frozen_evaluator/` 保存当时代码及输入；当前代码变化后，使用该目录的同名脚本和原 --run-dir 可重放旧轮，既有预测保持原样。

## 5、评分

所有预测完成后，采集器写 predictions_frozen.json；评分器在此之前拒绝评分。联调与正式轮次分别保存。

```powershell
& backend/.venv/Scripts/python.exe -X utf8 -B backend/evaluates/score_results.py `
  --run-dir backend/evaluates/runs/20261022-qwen38-ultra-native-unlimited-01
```

schema_metrics.py 复用原纯离线匹配及 SQL 诊断；来源和本地适配见 metrics-provenance.json。主指标与严格 SQL 指标分开。每次评分写独立 scoring/ 快照，并更新根 summary.json/report.md。报告记录首次完成检索轮数、全部定位轮数和读取调用次数，也记录超出观察阈值的题数。审计会重放原始响应并核对冻结预测。

### 2026-10-23：允许记忆直接生成 SQL 的离线复评

用户取消“必须存在检索轨迹”的前置条件。当前轮 `runs/20261022-qwen38-ultra-native-unlimited-01/sql-reassessment/rescore_sql.py` 只读取冻结预测、原始 SSE 和数据集 DDL，不调用模型、MCP 或数据库；每次指定新的 `--output-dir`，原报告及预测保持不变。

- 最终 SQL 本身命中：最后一次 SQL 成功执行，且该 SQL 独立覆盖全部目标表和按表绑定的列。
- 扩展原任务口径：最后一次 SQL 成功执行，且检索或该 SQL 任一完整命中。这样保留原来合格的检索题，再补入直接生成 SQL 的合格题。
- 任意一条成功 SQL 命中仅作辅助统计，不能把多条探查 SQL 的字段拼成一条命中。
- 字段名称精确命中与既有 COUNT(*)/id 等价规则分列；最终文字答复完整性、记忆注入和定位轮数另列。
- 复评输出 `summary.json`、`scores.json`、`successful_sql.json`、`report.md`、`case_details.md`；全量冻结哈希复核与 SQL 解析诊断均随结果保存。

## 7、旧轮停批诊断

旧 runs/20261022-qwen38-ultra-independent-01 保持原样：16 题里 6 次运行 success、10 次 interrupted。脚本因 SQL 次数上限取消 6 题、MCP 次数上限取消 4 题；取消接口返回 10 次 202。第 16 题第 25 次 MCP 后取消，SSE 15 秒未收尾触发整批中止。实际评估并发峰值为 1，并不是大量会话同时提交。控制接口记录 3125 次 200、10 次 202，未见 429；模型事件另有第 5～9 题上游 502，去重后共有 20 条 llm.error 事件。旧首条模型输入为 12591 字符，原题为 54 字符。新轮采用 question-only 原生 Skill 协议，不能与旧轮合并计分。

## 6、合成检查

```powershell
& backend/.venv/Scripts/python.exe -X utf8 -B -m pytest backend/evaluates/tests -q -p no:cacheprovider
```

合成检查不读正式答案，不调用模型、MCP、数据库或其他网络服务。实际联调仍需检查真实事件格式、完整工具证据与指定模型的运行表现。
