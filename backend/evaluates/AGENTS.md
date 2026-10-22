# VFSSQL 独立对话评估

本目录只实现评估采集、样本准备、离线评分和报告，不修改 DeerFlow 或 MCP 的生产逻辑。

- 通过正在运行的 Gateway 登录、创建 thread 和 run；一题一个新 thread。
- 模型固定为 Qwen3.8-27B-FP8-Local-cloud，Ultra、reasoning_effort=high。
- 固定并发 1；1200 秒超时。原双倍预算 24 MCP/4 SQL 在原生轮作为观察阈值，超出不取消，主代理和子代理合计。
- 凭据来自 VFSSQL_EVAL_EMAIL / VFSSQL_EVAL_PASSWORD，禁止保存认证头、Cookie 或登录响应。
- datasets/、skills/、runs/ 是被忽略的本地评估材料；禁止提交题库、完整模型输入、答案、原始日志或凭据。
- 宿主题面仅投影 case_id/question；human content 必须等于 question 原文，仅发一条消息。不得注入 Skill、题号、预算、输出格式、提示或追问。评分标签只在冻结预测之后读取。
- DeerFlow 自主发现用户的 skills/public/z-vfssql-retrieval；保持原生 Skill 与 SQL 执行配置，快照仅用于审计，不修改用户 Skill。
- 不使用 sql_learn，不改索引，不把前题 SQL 或评分反馈送入后题。
- 原始 SSE 字节、分页 HTTP 正文、接收收据与文件哈希是证据；不以手写轨迹代替。
- 只计实际返回的表字段；子代理摘要不补造原生工具 ID。自然语言答复保存 answer_text；评分 SQL 来源明确为最后一次实际 sql_execute 请求，不强迫模型交付 JSON。
- 改采集代码、Skill、提示词或参数后新建运行目录，禁止改写既有预测。
- start_evaluation.py 启动隐藏后台进程；工作进程使用该轮 _private/frozen_evaluator 归档代码，完成后自动评分，状态保存在 pipeline_status.json。
- SSE 缺口可用完整分页 messages 恢复，warning 与实际证据缺口分开。单题失败记录后继续；前题必须终态确认。状态不明、学习写入、冻结内容漂移或连续 5 题采集/环境失败才暂停。
- 对旧轮恢复使用旧轮归档代码；原生协议必须新建目录，不覆盖旧 16 题数据或混合统计。
- 文档在 README.md、evaluation-plan.md；每次改采集协议同时更新文档和合成测试。
