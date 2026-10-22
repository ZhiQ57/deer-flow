# DeerFlow VFSSQL 评估计划（2026-10-22）

## 1、准备

- [X] 1.1 核对 Gateway、专用账号、模型以及 Ultra/高的实际参数。
- [X] 1.2 从 dev 建立 feat/vfssql-deerflow-evaluation，快进复用已验收 MCP 集成提交，保留用户工作区修改。
- [X] 1.3 迁移 324 题、61 family 和上轮 Skill；固定哈希、洗牌种子与双倍预算。

## 2、采集与离线评分

- [X] 2.1 实现逐题新建 thread、原始 SSE/分页事件归档、取消、断点继续。
- [X] 2.2 按原生调用 ID 合并主代理/子图证据，检查摘要截断和长结果落盘。三题实际走主代理；子图协议由合成事件覆盖，缺失原生证据会暂停并保留现场。
- [X] 2.3 复用原表字段匹配与 SQL 诊断，补充首次完整检索轮数和证据审计。
- [X] 2.4 24 项合成检查和 Ruff 检查通过；兼容已有 SQLGlot 30.12 的作用域属性，不修改项目依赖。

## 3、实际运行

- [X] 3.1 完成 3 题采集联调，原型采集完整 3/3；保存原始轨迹与原型评分，不计入正式分数。
- [ ] 3.2 冻结正式配置，串行完成全部 324 个全新对话。
- [ ] 3.3 冻结预测后统一离线评分，生成逐题轮数、汇总报告及新旧运行方式对照。

## 4、后台自动化交付

- [X] 4.1 提供 start_evaluation.ps1 / start_evaluation.py；凭据仅由进程环境继承，隐藏进程使用该轮归档代码运行。
- [X] 4.2 run_pipeline.py 自动采集全部问题、冻结预测、评分并生成 report.md；status_evaluation.py 无需凭据即可只读查看进度。
- [X] 4.3 处理 `.tool-results` 实际路径、原生 messages SSE 事件和最终响应中的唯一 JSON；原型预测保持原样，采集适配不改变答案或目标标签。
- [ ] 4.4 启动正式后台进程并验证独立存活及新对话受理。

## 冻结条件

- 每题 1200 秒、24 次 VFSSQL MCP、4 次 sql_execute；失败、重试、枚举均计数。
- 模型 Qwen3.8-27B-FP8-Local-cloud；thinking_enabled/is_plan_mode/subagent_enabled 均为 true；reasoning_effort=high。
- 一题一个新 thread；无前题 SQL 复用；全量冻结后评分。
- 主指标是检索完整覆盖目标表字段且最终 SQL 实际成功执行；空结果允许通过。
- 严格 SQL 表字段引用单列，不替代用户主指标。
