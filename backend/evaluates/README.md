# DeerFlow VFSSQL 独立对话评估

本目录评估真实 DeerFlow Gateway、指定模型和已接入 VFSSQL 的组合效果。一题一个新 thread，固定并发 1，324 题只发送原始问题；结果统一保存在本目录 runs/。执行计划见 evaluation-plan.md。

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

启动器同时归档独立监督器，原冻结采集/评分代码不变。监督器每 30 秒向 console.log 输出 supervisor_heartbeat，supervisor_status.json 记录其存活状态。旧采集器若在题目自然结束后因 sql_learn 停批，监督器只在原始证据重放审计通过、全部学习调用明确被 MCP 参数验证拒绝且未执行时自动续跑下一题；保留原题失败、预测字节及调用记录，不重做题目。监督续跑原因和预测哈希保存到 supervisor_incidents.jsonl。成功写入或无法确认的学习调用不自动恢复。

只读查看进度：

```powershell
& backend/.venv/Scripts/python.exe -X utf8 -B backend/evaluates/status_evaluation.py `
  --run-dir backend/evaluates/runs/20261022-qwen38-ultra-native-unlimited-01
```

采集器没有取消模型运行的 API 调用。活动会话遇到 Gateway/事件接口故障会重连等待，记录累计观察时间，不会开始下一题；SQL 错误、模型慢、超过 1200 秒、MCP/SQL 调用多均不会触发中断。模型自然结束后归档其最终答复或服务错误；无最终答复仍计失败，记录后继续。知识写入、Skill/代码漂移或无法保存原始证据时停止后续评估，不打断当前模型。GET 和使用稳定 Idempotency-Key 的 run 请求对连接故障、429/502/503/504 做有界重试；活动运行的状态查询在这些重试用尽后仍持续等待。所有失败保留，不重跑择优计分。

2026-10-22 15:11:50（北京时间）旧冻结采集器在第 29 题后触发学习停批：sql_learn 返回 McpError: invalid tool arguments，参数验证拒绝，未成功写入。第 29 题自然 success、耗时 167.707676 秒，但无最终答复，仍为失败。此次通过监督器续跑同一目录、跳过原有 29 题；原始预测与冻结代码不变，没有新建轮次或重发已完成问题。旧 error.log 中的历史停批堆栈保留，最新运行状态以 pipeline_status.json/supervisor_status.json 和日志末尾为准。

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

## 7、旧轮停批诊断

旧 runs/20261022-qwen38-ultra-independent-01 保持原样：16 题里 6 次运行 success、10 次 interrupted。脚本因 SQL 次数上限取消 6 题、MCP 次数上限取消 4 题；取消接口返回 10 次 202。第 16 题第 25 次 MCP 后取消，SSE 15 秒未收尾触发整批中止。实际评估并发峰值为 1，并不是大量会话同时提交。控制接口记录 3125 次 200、10 次 202，未见 429；模型事件另有第 5～9 题上游 502，去重后共有 20 条 llm.error 事件。旧首条模型输入为 12591 字符，原题为 54 字符。新轮采用 question-only 原生 Skill 协议，不能与旧轮合并计分。

## 6、合成检查

```powershell
& backend/.venv/Scripts/python.exe -X utf8 -B -m pytest backend/evaluates/tests -q -p no:cacheprovider
```

合成检查不读正式答案，不调用模型、MCP、数据库或其他网络服务。实际联调仍需检查真实事件格式、完整工具证据与指定模型的运行表现。
