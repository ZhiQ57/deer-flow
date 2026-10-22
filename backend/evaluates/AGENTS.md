# VFSSQL 独立对话评估

本目录只实现评估采集、样本准备、离线评分和报告，不修改 DeerFlow 或 MCP 的生产逻辑。

- 通过正在运行的 Gateway 登录、创建 thread 和 run；一题一个新 thread。
- 模型固定为 Qwen3.8-27B-FP8-Local-cloud，Ultra、reasoning_effort=high。
- 固定并发 1；用户最新要求等待模型最终回答，不设执行时间上限，不允许评估器取消模型。原双倍预算 24 MCP/4 SQL 仅为观察阈值，主代理和子代理合计。
- 凭据来自 VFSSQL_EVAL_EMAIL / VFSSQL_EVAL_PASSWORD，禁止保存认证头、Cookie 或登录响应。
- datasets/、skills/、runs/ 是被忽略的本地评估材料；禁止提交题库、完整模型输入、答案、原始日志或凭据。
- 宿主题面仅投影 case_id/question；human content 必须等于 question 原文，仅发一条消息。不得注入 Skill、题号、预算、输出格式、提示或追问。评分标签只在冻结预测之后读取。
- DeerFlow 自主发现用户的 skills/public/z-vfssql-retrieval；保持原生 Skill 与 SQL 执行配置，快照仅用于审计，不修改用户 Skill。
- 不使用 sql_learn，不改索引，不把前题 SQL 或评分反馈送入后题。
- 原始 SSE 字节、分页 HTTP 正文、接收收据与文件哈希是证据；不以手写轨迹代替。
- 只计实际返回的表字段；子代理摘要不补造原生工具 ID。自然语言答复保存 answer_text；评分 SQL 来源明确为最后一次实际 sql_execute 请求，不强迫模型交付 JSON。
- 改采集代码、Skill、提示词或参数后新建运行目录，禁止改写既有预测。
- start_evaluation.py 启动隐藏后台进程；工作进程使用该轮 _private/frozen_evaluator 归档代码，完成后自动评分，状态保存在 pipeline_status.json。
- supervise_evaluation.py 单独归档到 _private/supervisors；保持既有冻结代码及预测不变，仅对审计确认的学习执行前拒绝自动调用原流水线续跑下一题。分类含 McpError 参数拒绝、MCP sql_learnArguments 输入模型校验、DeerFlow deferred guard、明确服务关闭/权限错误码；不能将 isError=true 等同于未执行，不能将输出或业务阶段异常当成输入校验。未知失败、pending 或实际写入不自动重试，禁止重做失败题择优计分。
- 监督器每 30 秒输出心跳；supervisor_status.json 记录进程状态，supervisor_incidents.jsonl 保存续跑依据和原预测摘要。不要将历史 error.log 堆栈当作当前进程状态。
- 监督器无法恢复时必须在 console.log 明确输出 evaluation_stopped/stopped=true 和原因，再退出；不能只保存堆栈后静默停止。
- SSE 缺口可用完整分页 messages 恢复，warning 与实际证据缺口分开。活动运行的接口故障持续重连等待；前题必须自然终态确认后才能开始下一题。不得调用取消接口或因超时/次数主动中断。
- 每题记录服务 created_at→updated_at 的运行时间、宿主观察时间和时间来源。接管时沿用原 thread/run 及题面，不重发问题；源证据哈希保存在 _private/adoption.json。
- 新协议使用 native-unlimited 独立目录。含取消逻辑的旧轮不能用当前启动器直接恢复；原题/模型/Skill 相同的原生对话可用 --adopt-from 接管。不能接管或混合旧注入轮的 16 题。
- 文档在 README.md、evaluation-plan.md；每次改采集协议同时更新文档和合成测试。
