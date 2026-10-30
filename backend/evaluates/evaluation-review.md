# 评估自动化交付记录（2026-10-22）

## 2026-10-23：三题显式 Skill 流程验收与正式复测后台启动

按用户要求，只用三个实际 DeerFlow 样本验证流程，随后自动后台运行缺检索子集。筛出240题无VFS、3题仅目录初始化，共243题；原81题定位或内容读取数据保留。完整324题已归档到 runs/_archive/20261022-native，旧活动路径243份预测及243个轨迹目录已删除，独立核验剩余81题与归档324份冻结哈希一致。目录句柄和Windows长路径导致的首次临时归档不完整；自动审批拒绝删除该临时目录，已用INCOMPLETE_ARCHIVE.json标记并排除，完整短路径归档已核验。

输入固定为 `/vfssql-retrieval`、一句“必须使用 vfssql-retrieval skill。”和原问题。宿主通过原生 slash 激活注入 Skill 正文；采集器按 middleware:skill_activation 的名称、路径和正文哈希核验。发现文件原始CRLF字节哈希与DeerFlow read_text归一化正文哈希不同，评估侧新增skill_text_sha256；原字节快照哈希继续保留。没有修改用户Skill或DeerFlow/MCP生产代码。

三题自然结束后，沿用相同thread/run复制原始证据到修正后的三题验收目录，仅重新离线解析，没有增加模型测试题：

| 样本 | 服务耗时（秒） | MCP调用 | 成功SQL调用 | 流程验收 |
| --- | ---: | ---: | ---: | --- |
| case-0002 | 51.312432 | 7 | 3 | 通过 |
| case-0006 | 212.489167 | 25 | 3 | 通过 |
| case-0047 | 108.469415 | 13 | 2 | 通过 |

三题独立会话、输入一致、自然成功终态、有最终答复、原生正文激活、成功检索及原始证据重放全部通过，自动评分文件已生成；业务主成功2/3、执行成功3/3，未将三题业务成绩全通过设为流程验收条件。2026-10-23 09:59:55（北京时间）自动验收通过；09:59:56自动启动正式243题。活动目录 runs/20261023-skill-retry-v2，编排实际PID32968，采集/监督实际PID44120。10:00:56独立查询确认两个进程均存活，首题16.9秒自然结束且归档完整，第二题case-0003运行，日志心跳正常。后续由独立Python进程等待、归档、评分与生成组合报告，不依赖本轮Codex对话。

## 2026-10-23：记忆与 SQL 直接命中复核

用户要求接受省略检索、直接使用记忆生成 SQL 的样本。本次只复评已冻结结果，未调用模型、MCP 或数据库；生产代码和用户 Skill 均未修改。新脚本位于当前正式轮的 sql-reassessment/rescore_sql.py，定稿结果位于 sql-reassessment/v3/，原评分和预测保留。

复核 962 次成功执行的 SQL，覆盖 200 题；最后一次 SQL 成功为 170 题。使用冻结数据集的 31 张表 DDL 解析作用域、别名和字段归属，无解析错误或未解决字段绑定；170 题的最终 SQL 结构判定与原独立 sql_structure_complete 诊断全部一致。SQL 命中不再受 VFS、Skill 正文读取、协议或最终文字门槛影响，答复缺失另列。

- 最终 SQL 本身完整命中且执行成功：46/324（14.20%）；其中 1 题沿用 COUNT(*) 对 id 的既有等价规则，所有字段名称实际出现为 45/324（13.89%）。
- 保留原检索成功口径并允许最终 SQL 直接命中：原 14 题加新增 42 题，共 56/324（17.28%）。其中 10 题只在检索阶段命中，最终 SQL 目标引用不足，故不属于上述 46 题。
- 对话内任意一条成功 SQL 独立完整命中：62/324（19.14%），未跨 SQL 拼凑覆盖。
- 320/324 题观测到宿主动态记忆注入；170 道最终 SQL 执行成功题中为 168 题。46 道 SQL 命中题中，37 题完全无 VFS 调用，且全部观测到记忆注入；不能据此独立证明答案来源的因果关系。
- 46 道中 case-0026、case-0109 没有最终文字答复；若另要求答复完整，最终 SQL 命中为 44/324，扩展原任务口径为 54/324。原始缺口未补造。

逐题 scores.json、全部成功调用 successful_sql.json、200 题 case_details.md 和汇总报告均已输出。324 份冻结预测及原 summary.json、report.md、scores.json、predictions_frozen.json 的 SHA-256 均复核保持一致。本次运行的是离线复评，没有重跑题目择优，也没有运行额外测试套件。

## 第 95 题停批与 MCP 输入模型校验

2026-10-22 18:00:43（北京时间）第 95 题后停批，进程退出；此前监督器已自动越过第 42、53、90 题的已拒绝学习调用。第 95 题自然 success、最终答复 569 字符、172.979191 秒，原始轨迹完整且审计通过。一条学习调用被 deferred guard 拒绝，另一条为 ToolException: Error executing tool sql_learn: 1 validation error for sql_learnArguments，旧判断仅识别 McpError 固定文本，没有覆盖 MCP 的参数模型验证包装。

核对实际运行环境 MCP SDK Tool.run：先 fn_metadata.validate_arguments，捕获 ValidationError 后返回 ToolError，随后才进入 call_fn；本次异常在调用知识函数之前发生。修订按整个 sql_learnArguments 输入校验类别判定，覆盖多个错误、不同字段和异常包装；明确排除输出模型、嵌套工具及业务阶段异常。同步匹配服务原生 disabled/forbidden 固定错误码（实际回执没有 applied 字段）。保留原 95 题预测、未授权学习诊断及冻结采集/评分代码。

新增 console evaluation_stopped 事件，写入明确退出原因和题数，避免未知故障只留下历史堆栈。55 项合成检查通过，覆盖输入校验的四种包装/格式、五类不可认定未执行的异常、服务错误码和实际停止事件。

2026-10-22 19:33:10（北京时间）恢复同一轮，worker PID 51664，监督器代码提交 835b28732，快照 SHA-256=b60f21f2994c4e5d272798212a04ed05b53e952e01d2ae6dbbd4c632c85914b8。原 95 题预测哈希全部保持一致；已回看此前 6 题中的全部学习尝试，均识别为明确执行前拒绝。第 96 题自然结束（约 10 秒），第 97 题已运行，19:33:39 新心跳及进度推进已独立核验；第 96 题输入仍为单条原问题、Ultra/high 指定模型。

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

## 2026-10-26：四组对照指标与服务恢复编排

用户要求按首次候选、结构读取、SQL/任务成功、效率成本四组指标对照324题。新增comparison_metrics.py，仅读取原冻结预测和已有评分；核对数据集、sample_id与哈希；首次候选@k为单个首个成功非空响应内部去重顺序，Macro-F1逐题计算后均值。Codex原324题的全部对照值复算一致：主任务248题、显式SQL联合240题，平均保存耗时5.217660秒，P50=1.048/P95=19.723，MCP均值5.336420。

“保存耗时”按已记录elapsed_seconds汇总，明确Codex原共享会话Date.now与DeerFlow整次Gateway运行时间的差异；DeerFlow宿主观察和完整采集时间另存。恢复中全量DeerFlow指标为null，旧154题只作为已有有效组；新170题全部有效预测冻结并统一评分后才发布完整324题。最终原问题79题/显式Skill245题的混合条件明示，服务错误尝试及耗时单列。

同三题case-0048、case-0044、case-0047均自然成功、最终答复存在、Skill激活及原始证据重放通过；16次SQL执行成功，7次成功检索。旧验收因两题没检索而停止，旧workflow_validation.json及原预测保持不变。新增provider_workflow_validation.json区分链路验收与业务表现，recovery_suite.py独立归档并接续原冻结采集器，不增加实际联调题，不重做正常业务失败择优。

5项离线合成检查通过，覆盖逐题Macro-F1、SQL全样本分母、成功题效率、P95最近秩、首个非空候选内部去重及重复题拒绝；仅新增文件运行Ruff。2026-10-26 09:49（北京时间）后台恢复正式170题，实际控制器PID57072、采集/监督PID60192，已独立确认首题自然success、81.2秒、9次MCP、完整归档，并自动开始次题。新模块与控制器不改DeerFlow/MCP生产逻辑；回调自动生成comparison_metrics/report.md等产物。
