# VFSSQL 独立对话评估

本目录只实现评估采集、样本准备、离线评分和报告，不修改 DeerFlow 或 MCP 的生产逻辑。

- 通过正在运行的 Gateway 登录、创建 thread 和 run；一题一个新 thread。
- 模型固定为 Qwen3.8-27B-FP8-Local-cloud，Ultra、reasoning_effort=high。
- 固定并发 1；用户最新要求等待模型最终回答，不设执行时间上限，不允许评估器取消模型。原双倍预算 24 MCP/4 SQL 仅为观察阈值，主代理和子代理合计。
- 凭据来自 VFSSQL_EVAL_EMAIL / VFSSQL_EVAL_PASSWORD，禁止保存认证头、Cookie 或登录响应。
- datasets/、skills/、runs/ 是被忽略的本地评估材料；禁止提交题库、完整模型输入、答案、原始日志或凭据。
- 默认原生轮宿主题面仅投影 case_id/question；human content 必须等于 question 原文，仅发一条消息，不注入额外提示。2026-10-23 用户授权的显式 Skill 复测是独立协议：human content 为 /vfssql-retrieval、固定一句“必须使用 vfssql-retrieval skill。”及原问题；不加表列、题号、预算、输出格式或追问。评分标签只在该轮预测冻结之后读取。
- DeerFlow 自主发现用户的 skills/public/z-vfssql-retrieval；保持原生 Skill 与 SQL 执行配置，快照仅用于审计，不修改用户 Skill。
- 不使用 sql_learn，不改索引，不把前题 SQL 或评分反馈送入后题。
- 原始 SSE 字节、分页 HTTP 正文、接收收据与文件哈希是证据；不以手写轨迹代替。
- 只计实际返回的表字段；子代理摘要不补造原生工具 ID。自然语言答复保存 answer_text；评分 SQL 来源明确为最后一次实际 sql_execute 请求，不强迫模型交付 JSON。
- 2026-10-23 用户修订离线复评：允许使用记忆并省略检索；另列“最终 SQL 实际命中目标表列且执行成功”和“检索或最终 SQL 任一命中且执行成功”。保留原评分和全部预测；新脚本及结果放在当前轮 runs/*/sql-reassessment/ 下，不改冻结采集器。任意成功 SQL 命中仅为辅助指标，不跨 SQL 拼接覆盖。
- 改采集代码、Skill、提示词或参数后新建运行目录，禁止改写既有预测。
- start_evaluation.py 启动隐藏后台进程；工作进程使用该轮 _private/frozen_evaluator 归档代码，完成后自动评分，状态保存在 pipeline_status.json。
- supervise_evaluation.py 单独归档到 _private/supervisors；保持既有冻结代码及预测不变，仅对审计确认的学习执行前拒绝自动调用原流水线续跑下一题。分类含 McpError 参数拒绝、MCP sql_learnArguments 输入模型校验、DeerFlow deferred guard、明确服务关闭/权限错误码；不能将 isError=true 等同于未执行，不能将输出或业务阶段异常当成输入校验。未知失败、pending 或实际写入不自动重试，禁止重做失败题择优计分。
- 监督器每 30 秒输出心跳；supervisor_status.json 记录进程状态，supervisor_incidents.jsonl 保存续跑依据和原预测摘要。不要将历史 error.log 堆栈当作当前进程状态。
- 监督器无法恢复时必须在 console.log 明确输出 evaluation_stopped/stopped=true 和原因，再退出；不能只保存堆栈后静默停止。
- SSE 缺口可用完整分页 messages 恢复，warning 与实际证据缺口分开。活动运行的接口故障持续重连等待；前题必须自然终态确认后才能开始下一题。不得调用取消接口或因超时/次数主动中断。
- 每题记录服务 created_at→updated_at 的运行时间、宿主观察时间和时间来源。接管时沿用原 thread/run 及题面，不重发问题；源证据哈希保存在 _private/adoption.json。
- 新协议使用 native-unlimited 独立目录。含取消逻辑的旧轮不能用当前启动器直接恢复；原题/模型/Skill 相同的原生对话可用 --adopt-from 接管。不能接管或混合旧注入轮的 16 题。
- 文档在 README.md、evaluation-plan.md；每次改采集协议同时更新文档和合成测试。
- retry_retrieval.py 只选择没有 find/grep 或内容读取的旧样本；tree_folder 仅初始化。先将完整旧轮移动到 runs/_archive 保留可恢复审计记录，再为 243 题建立全新活动轨迹；81 道原检索题单列保留。移动前必须验证源和目标绝对路径都在 evaluates/runs 内。
- 本次仅用 3 个真实 DeerFlow 样本检查自动流程，原生 middleware:skill_activation 名称/正文哈希、检索工具成功、独立会话、最终答复及证据重放全部核验后才启动正式后台批次。记忆直接答对而未检索不计 MCP 检索成功。组合报告标明原 81 题与复测 243 题的提示方式不同。
- 2026-10-26用户明确授权重测模型服务异常：上轮168题均有HTTP502模型事件且未调用MCP，原保留组另有2题运行异常，一并恢复。运行异常标记待评估，不直接计业务失败；原始记录保留，正常完成的检索/SQL业务失败不重做择优。
- provider_recovery.py等待当前模型自然终态；模型服务429/5xx或连接故障的已终止尝试归档到当前批次infra_attempts，移出业务预测集合，按60/120/300/600秒退避、每30秒心跳，同题恢复前不提交后题。未知运行异常或未确认知识写入则明确needs_attention，不并发重发。只有有效预测齐全后统一冻结评分。
- 恢复批次经retry_retrieval.py --recover-infrastructure启动，先三题验收再正式170题；持续区分原154题有效记录、新有效待评分记录与待恢复题数，恢复完成前不发布全量最终业务成功率。
- 服务故障恢复可用recovery_suite.py独立控制器接管已停止的套件，冻结采集器及同三题预测保持原样；provider_workflow_validation.json核验独立会话、固定题面、原生Skill、自然最终答复、完整采集与重放，三题合计需有成功检索和真实SQL执行。每题是否检索或命中属于业务表现，不因没检索而重做三题或阻断后续恢复；旧严格验收保留。
- comparison_metrics.py只读原冻结评分/预测，按用户四组指标输出comparison_metrics/report.md、metrics.json和case_metrics.json/csv。@k为首次成功非空候选内部去重顺序；表Macro-F1逐题计算后平均；SQL无错率分母为全324题，从未执行SQL不成功。服务错误不补0，恢复中全量DeerFlow指标为null。
- 对照固定原Codex324题冻结预测与评分，检查sample_id、数据集和哈希；最终组合原79题、已完成显式Skill75题及恢复170题，明确79题原问题、245题显式Skill的混合提示。保存的elapsed_seconds与宿主观察/完整采集时间分别记录，报告说明Codex共享会话Date.now与DeerFlow全运行时间范围不同。
- 新控制器和对照模块单独归档到_private/recovery_controllers，依赖该批次冻结采集代码；每30秒心跳，全部170题完成后自动生成324题四组对照。追加指标检查仅使用合成样本和已有证据，不增加实际DeerFlow联调题数。
