# DeerFlow VFSSQL 独立对话评估

本目录评估真实 DeerFlow Gateway、指定模型和已接入 VFSSQL 的组合效果。一题一个新 thread，324 题独立生成检索与 SQL；结果统一保存在本目录 runs/。执行计划见 evaluation-plan.md。

## 1、固定协议

- 模型 Qwen3.8-27B-FP8-Local-cloud；Ultra（思考/计划/子代理均开启）；reasoning_effort=high。
- 用户批准双倍预算：每题 1200 秒、24 次 VFSSQL MCP、4 次 sql_execute。所有主/子代理失败、重试和枚举计入。
- 数据集 324 题、61 family；seed=20261015；SHA-256=e6d2778929830da9d76ca09f981789d3929692baeeaf63964f477d28734bcae4。
- 使用上轮 Skill 快照，显式注入每题；不写入全局技能目录；不调用 sql_learn。
- 主成功：真实检索完整覆盖目标表字段，且交付 SQL/参数与最后一次成功执行对应。空结果允许通过；严格 SQL 引用目标字段单列。
- find/grep 每次往返计一轮，结构读取另计；未完整覆盖的首次完成轮数为 null。

## 2、准备

以下命令在项目根目录的 PowerShell 执行，使用已有 backend/.venv，不安装额外服务：

```powershell
& backend/.venv/Scripts/python.exe -X utf8 -B backend/evaluates/prepare_dataset.py `
  --dataset 'D:/A-AICodeWork/ownmindforge/backend/evaluate/vfssql-evluate-data/vfssql-evaluation-dataset.v1.json' `
  --skill 'C:/Users/admin/.codex/skills/vfssql-retrieval/SKILL.md' `
  --run-dir backend/evaluates/runs/20261022-qwen38-ultra-pilot --pilot
```

正式运行使用新的 --run-dir 并去掉 --pilot。代码、提示词、样本、Skill 和参数都会冻结哈希；变化后须新建运行目录。datasets/skills/runs 均不提交。

## 3、执行

账号和密码分别从 VFSSQL_EVAL_EMAIL / VFSSQL_EVAL_PASSWORD 环境变量读取，不写入文件、参数日志或 HTTP 收据。默认 Gateway 为 http://127.0.0.1:8001。

```powershell
& backend/.venv/Scripts/python.exe -X utf8 -B backend/evaluates/run_deerflow.py `
  --run-dir backend/evaluates/runs/20261022-qwen38-ultra-pilot
```

--limit N 只限制本次新处理题数，不改变冻结样本集合。再次运行会跳过冻结预测；对已受理 run 查询原 ID，不重新提交模型任务。认证、运行环境或原始证据缺失时停止批次，保留全部轨迹，不静默更换模型、缩减 Ultra 或改生产代码。

正式运行可直接使用 `run_pipeline.py --run-dir ...`。它会持续采集全部题目，全部预测冻结后自动离线评分并写 report.md；pipeline_status.json 记录 running/scoring/complete/needs_attention，标准输出只含题号、终态、次数和时间。

### 独立后台自动执行

```powershell
& backend/evaluates/start_evaluation.ps1 -RunDir 'D:/A-PythonWork/AOpenGithub/deer-flow/backend/evaluates/runs/20261022-qwen38-ultra-independent-01'
```

启动器准备正式 324 题并启动隐藏的独立 Python 进程，使用该轮归档代码持续运行。窗口或本轮 Codex 对话无需保持等待。凭据按提示输入，或由当前进程环境提供；不写入脚本。console.log/error.log 为后台输出。恢复同一目录时显式传入 -Resume；已有存活进程或已完成结果会拒绝重复启动。

只读查看进度：

```powershell
& backend/.venv/Scripts/python.exe -X utf8 -B backend/evaluates/status_evaluation.py `
  --run-dir backend/evaluates/runs/20261022-qwen38-ultra-independent-01
```

个别题目检索失败、SQL 错误、预算耗尽会保存失败并继续。预算取消时未返回的工具调用保留 incomplete，不补造成功；它仍使本题失败，不会让预期取消阻塞整轮。尚未生成完成的工具参数增量不计为真实 MCP 请求。认证、连接或其他原始证据采集故障进入 needs_attention，保留原始现场；恢复使用原 thread/run，不通过重做失败题择优计分。

## 4、采集与审计

traces/case-XXXX/ 保存请求、thread/run 受理信息、原始 stream.sse、原始 http-bodies.bin、两组带字节位置/哈希/UTC/monotonic 时间的收据，以及工具已公开引用的 tool-results 文件。登录响应、认证头、Cookie 不进入记录器。

原生工具请求/结果按 namespace＋tool_call_id 关联。子代理卡片仅用于发现漏采，不能补造 ID 或被截断的返回。长结果恢复需依据本题工具实际返回的 `.tool-results` 文件引用。宿主接收间隔不等于服务器执行耗时。预算同时观察原始 SSE，避免等待持久化事件批次。最终响应允许从前后说明中提取唯一、完整的四字段 JSON；多个不同候选不会择优选取。

每轮 `_private/frozen_evaluator/` 保存当时代码及输入；当前代码变化后，使用该目录的同名脚本和原 --run-dir 可重放旧轮，既有预测保持原样。

## 5、评分

所有预测完成后，采集器写 predictions_frozen.json；评分器在此之前拒绝评分。联调与正式轮次分别保存。

```powershell
& backend/.venv/Scripts/python.exe -X utf8 -B backend/evaluates/score_results.py `
  --run-dir backend/evaluates/runs/20261022-qwen38-ultra-pilot
```

schema_metrics.py 复用原纯离线匹配及 SQL 诊断；来源哈希见 metrics-provenance.json。主指标与严格 SQL 指标分开。每次评分写独立 scoring/ 快照，并更新根 summary.json/report.md。审计会重放原始响应并核对冻结预测。

## 6、合成检查

```powershell
& backend/.venv/Scripts/python.exe -X utf8 -B -m pytest backend/evaluates/tests -q -p no:cacheprovider
```

合成检查不读正式答案，不调用模型、MCP、数据库或其他网络服务。实际联调仍需检查真实事件格式、完整工具证据与指定模型的运行表现。
