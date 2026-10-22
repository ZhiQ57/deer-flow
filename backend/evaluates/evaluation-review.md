# 评估自动化交付记录（2026-10-22）

## 第 36 题停批与执行前拒绝分类

2026-10-22 15:54:30（北京时间），第 36 题自然 success，耗时 84.211772 秒，有 177 字符最终答复，capture_complete=true 且原始证据审计通过。sql_learn 返回“is deferred and has not been promoted yet”，is_error=true。核对 DeferredToolFilterMiddleware：命中未 promoted 工具时直接返回错误 ToolMessage，没有调用 handler，故 MCP 学习未执行。

前版监督器仅识别参数验证拒绝，遗漏 deferred guard，因而第 36 题后停止。修订为按调用是否在执行前被拒绝分类，记录具体拒绝类型；覆盖参数验证、未启用的延迟工具、明确 ok=false/applied=false 的服务关闭/权限拒绝。存在执行成功、写入 ID、pending 或未知错误时仍不自动放行。原冻结采集/评分协议及全部原预测继续保留，续跑从第 37 题开始。

42 项合成检查及 Ruff check/format 通过，原始第 29/36 题分别识别 argument_validation_rejected、deferred_tool_not_promoted，均通过轨迹审计。原有 36 题预测哈希完全一致。2026-10-22 16:32:41（北京时间）恢复同一运行目录，worker PID 15336，监督器代码提交 5fd0132ea，快照 SHA-256=970487b98d008aa6918bc5cbfa4150fc53b71a07cdfbd8f2427eb10920d7643a；独立看到 16:33:11、16:33:41 心跳，以及 37/38 题自然完成、39 题受理，确认进度实际推进。

## 第 29 题停批与监督续跑

用户报告日志停止更新。核对到 worker PID 57308 已退出，2026-10-22 15:11:50（北京时间）pipeline_status=needs_attention，错误为“检测到知识学习调用，已停止批次”。已归档 29/324 题。第 29 题 Gateway 自然 success，实际运行 167.707676 秒，评估 status=failed、最终答复为空；本次没有取消请求。

原生 sql_learn 返回 McpError: invalid tool arguments; use the advertised input schema，is_error=true，调用在参数验证阶段被拒绝。原始证据重放审计通过，不存在成功写入的返回。旧采集器对任何学习请求一律停批，误将这次明确拒绝也作为整批停止条件。

新增独立监督器编排原冻结流水线：只对这类已审计的验证拒绝续跑下一题，保留本题失败及全部原预测字节；成功写入、状态不明或其他故障不自动放行。监督器独立留代码哈希快照，采集和评分仍使用该轮原冻结版本，不变更原题消息、模型、Skill、执行等待策略或结果。增加 30 秒心跳、监督状态和续跑事件记录，启动器拒绝重复后台实例。

38 项评估器合成检查、Ruff check/format 和 git diff --check 通过。2026-10-22 15:42:48 恢复同一轮流水线，实际 worker/监督器 PID 12680，独立核验 running/process_alive=true，第 30 题已受理，原有 29 题预测哈希全部保持一致。监督器快照 SHA-256=9f43aec524848169ec3d4274bd0eaba8a9e16c2441271e5eddb2df8de19513a2，源码提交 012e5728d；采集/评分仍用原冻结代码。

## 最新要求：等待模型自然结束

用户进一步要求不因耗时或次数中断模型，且本地服务无需限制消耗。当前协议移除单题 1200 秒限制，timeout_seconds=null；评估采集代码没有取消运行的接口调用。活动会话遇到控制接口故障时持续重连等待，确认自然终态后才进入下一题。

每题 elapsed_seconds 优先取 Gateway 终态 created_at→updated_at，另记 observed_elapsed_seconds、capture_elapsed_seconds 和 timing_source。原生首题 thread=c19a407a-5ec2-444d-8ba4-976d60b4326d、run=76e88a08-93a0-4ee2-85f2-035649bd4c93，在只分离采集进程后仍在 Gateway 继续运行，已自然 success；没有调用取消或重发问题。新轮 native-unlimited-01 沿用这些 ID 和原始证据，源摘要保存在 adoption.json，排除切换采集器的观察空档对服务耗时的影响。

36 项合成检查与 Ruff 通过，新增覆盖超过 1200 秒仍允许成功、采集器没有取消 API、服务计时及不改变对话的证据接管。以下记录保留先前协议历史；当前执行以本节、README 与 native-unlimited-01 配置为准。

北京时间 14:13:13 启动 native-unlimited-01，worker PID 57308，使用 c761c2792 代码快照。首题完成 705 字符最终答复、14 次 MCP/3 次 SQL，服务运行时间 213.481396 秒，宿主观察 689.262145 秒（包含切换采集器空档），原始轨迹重放审计通过、capture_complete=true。后续题目自动创建并运行，已确认前 4 题均自然 success、完整归档；这是运行状态，正式表字段成功率仍须全量冻结后评分。

## 原生协议修订与停批诊断

旧轮在 16 题停止，实际并发峰值为 1。16 次运行中 6 次 success，10 次 interrupted；SQL 硬上限触发取消 6 题，MCP 硬上限触发取消 4 题。第 16 题第 25 次 MCP 后于 278.111 秒被取消，SSE 未在 15 秒内关闭，未被旧继续条件覆盖，因此整批中止。控制接口为 3125 次 200、10 次取消 202，未见 429。模型端第 5～9 题有上游 502，去重 llm.error 共 20 条；不能由此认定会话并发过高。

旧首条输入包含 12591 字符，原题只有 54 字符。新协议使用用户放置的 public/z-vfssql-retrieval，Gateway 已确认 enabled=true；主文件 SHA-256 为 dc73bb010eee97a38c83cb7cbc505aad50a9d52fba47b49a8fc2415acc9012e4，包内 6 个文件。SQL 服务配置 sql_execution.enable=true，执行超时 10 秒、最多 1000 行。

修订仅涉及 backend/evaluates：每题仅发送原题，固定并发 1，24 MCP/4 SQL 作为观察阈值，1200 秒仍为硬超时。Skill 自主发现，允许原生 read_file 等工具；自然语言答复按原文保存，评分 SQL 从最后实际 sql_execute 请求提取并标注来源。原生流程不要求首个工具必须是目录树。

SSE 与持久化事件/分页消息交叉恢复，流传输告警和证据缺口分开记录；执行成功但无最终答复仍失败。单题失败在确认终态后继续，超时取消 wait=true；状态不明、写知识、冻结输入漂移或连续 5 题环境/采集失败才停批。对安全请求增加有界重试，run 重试复用同一幂等键，不新建模型任务。

原有注入轮、三题原型和预测保持原样。原生正式轮为 runs/20261022-qwen38-ultra-native-01，共 324 题，全部预测冻结后才统一评分；后台启动、实际请求及存活情况以 launcher/pipeline_status/trace 为准。新增合成回归覆盖 question-only、观察阈值、自然答复、原生 Skill、持久化恢复、终态门控和幂等重试，不进行新的三题联调。

本次 34 项评估器合成检查、Ruff check/format 与 git diff --check 均通过。检查不连接业务数据库或模型；实际效果须等原生后台轮完成后以报告为准。

北京时间 2026-10-22 14:01:44 启动原生后台轮，使用 Windows breakaway_from_job；实际 worker PID 35600。独立进程已核对 running/process_alive=true，首个 thread/run 已受理；请求仅有一条 human 消息，content 与原题完全相同（54 字符），没有注入提示。新 run 冻结的代码提交为 637c4d3a4，后续文档更新不改变该轮采集代码。

下方为原型和首轮交付的历史记录。

## 实现范围

变更集中在 backend/evaluates。通过现有 Gateway 运行真实对话，没有修改 DeerFlow 生产代码、模型配置或 VFSSQL 服务。原有用户暂存的 .env/config.dev.yaml 保持在工作区。

## 已完成验证

- 样本哈希与上轮一致：324 题、61 family；只向答题侧投影当前 case_id/question。
- 模型固定 Qwen3.8-27B-FP8-Local-cloud，Ultra/high；预算按用户要求翻倍到 1200 秒、24 MCP、4 SQL。
- 三题真实联调已结束，原型原始采集完整 3/3，调用数量分别为 9、27、19；两题触发预算取消，全部保留原始轨迹和失败记录。
- 真实调用覆盖目录、grep/find、cat 和 SQL 执行；实际成功 SQL 可以返回空结果。
- 确认 SSE 实际事件名 messages 和 `.tool-results` 路径；本题工具原文文件可通过现有 artifact API 获取（HTTP 200）。
- 当前适配器重放三题原始数据，MCP 数量与原始记录一致；支持从前后说明中提取唯一合法最终 JSON。旧预测与原型评分保持原样，联调不计入正式分数。
- 24 项合成检查通过，覆盖分块 UTF-8 SSE、namespace/原生 ID、预算、唯一/歧义 JSON、原文恢复、字段按表绑定、空结果、主指标与严格 SQL 区分、原始字节篡改和预测重放。
- Ruff check/format 和 git diff --check 通过。子图路径由合成协议覆盖；实际联调三题走主代理，后续若缺少子代理原生证据将保留现场并暂停。
- 数据集、Skill 快照、运行日志与完整输入均被 Git 忽略；凭据仅由进程环境传递。

## 后台运行

正式轮次：runs/20261022-qwen38-ultra-independent-01。

2026-10-22 12:06（北京时间）启动隐藏后台进程，已验证脱离父任务的进程存活，且第一个独立 DeerFlow 对话和运行已被 Gateway 接收。进程使用本轮 _private/frozen_evaluator 中的代码和输入，与当前聊天等待状态分离。

run_pipeline.py 负责串行创建并运行全部 324 个对话，全部预测冻结后自动离线评分并生成 report.md / summary.json / scoring/。pipeline_status.json 与 console.log 为当前进度依据。正式评分尚待本轮全量运行结束，不用联调结果代表正式成功率。
