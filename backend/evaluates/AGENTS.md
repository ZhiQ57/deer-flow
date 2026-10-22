# VFSSQL 独立对话评估

本目录只实现评估采集、样本准备、离线评分和报告，不修改 DeerFlow 或 MCP 的生产逻辑。

- 通过正在运行的 Gateway 登录、创建 thread 和 run；一题一个新 thread。
- 模型固定为 Qwen3.8-27B-FP8-Local-cloud，Ultra、reasoning_effort=high。
- 用户于 2026-10-22 批准预算翻倍：1200 秒、24 次 VFSSQL MCP、4 次 SQL；主代理和子代理合计。
- 凭据来自 VFSSQL_EVAL_EMAIL / VFSSQL_EVAL_PASSWORD，禁止保存认证头、Cookie 或登录响应。
- datasets/、skills/、runs/ 是被忽略的本地评估材料；禁止提交题库、完整模型输入、答案、原始日志或凭据。
- 题面仅投影 case_id/question，评分标签只在冻结预测之后读取。
- 不使用 sql_learn，不改索引，不把前题 SQL 或评分反馈送入后题。
- 原始 SSE 字节、分页 HTTP 正文、接收收据与文件哈希是证据；不以手写轨迹代替。
- 只计实际返回的表字段；子代理摘要不补造原生工具 ID。证据缺失保留失败并暂停批次检查。
- 改采集代码、Skill、提示词或参数后新建运行目录，禁止改写既有预测。
- start_evaluation.py 启动隐藏后台进程；工作进程使用该轮 _private/frozen_evaluator 归档代码，完成后自动评分，状态保存在 pipeline_status.json。
- 预算取消导致未完成工具返回时保留 incomplete 并将本题计失败，继续后题；其他采集或环境故障暂停批次。
- 文档在 README.md、evaluation-plan.md；每次改采集协议同时更新文档和合成测试。
