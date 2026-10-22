# 评估自动化交付记录（2026-10-22）

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
