# DataAgent 标签时序与递归收敛修复 Review

审查日期：2026-07-15

## 1、问题复盘

失败日志 `log_20260715_155453_546.txt` 中，单轮共出现 13 次工具调用：

- `read_file` 3 次；
- `tool_search` 3 次；
- TableRAG 检索 4 次；
- `publish_query_labels`、`data_validate_sql`、`data_execute_sql` 各 1 次。

原流程允许 `source=user/derived` 标签在检索前发布，和
`skills/public/z_sqltable-rag/SKILL.md` 的
“首次有效检索后分析意图并发布标签”不一致。原有分阶段预算只返回错误
ToolMessage，模型仍可更换 `read_file`、`tool_search` 或其他 TableRAG 工具继续探索；
不同工具和参数也无法触发现有“重复调用”检测，最终由 LangGraph
`recursion_limit=150` 被动终止。

日志还显示模型引用“之前成功执行过的 SQL”继续推测当前 Schema。历史对话和 memory
因此被补充规则明确限制为偏好/纠正上下文，不能作为当前数据库 Evidence。

## 2、实现结论

- 实验性 DataAgent 使用
  `QueryLabelsMiddleware(require_retrieval=True, stage_name="labels_published")`。
- 查询标签只能在当前轮次首次有效 TableRAG 检索后发布。
- `data_validate_sql` 增加标签阶段门禁，固定顺序为：
  `TableRAG -> 查询标签 -> SQL校验 -> SQL执行 -> 可选ChartSpec -> 最终回答`。
- `DataAgentState` 新增 `data_force_final_answer`，新用户轮次会重置该状态。
- 单轮新增所有 ToolMessage 的总预算，默认 10，可通过
  `data_agent_max_total_tool_calls` 在 `1..50` 范围内配置。
- 达到总预算、分阶段硬预算、成功 SQL 结果或完成 ChartSpec 后，下一次模型请求使用
  `tools=[]`，禁止模型通过更换工具继续循环。
- DataAgent prompt 明确禁止把历史 SQL、历史对话或 memory 当作当前表、字段、字段值、
  Join 或业务口径的证明。
- 实验图会移除 lead-agent 按 `agent_name=data-agent` 注入的通用标签 middleware，
  再插入带检索门禁的专用实例，避免重复拦截。

## 3、页面 Review

- 页面面板顺序调整为先 TableRAG、后用户意图标签。
- 阶段条新增 `labels_published`，主流程文案明确展示
  `TableRAG → 意图标签 → SQL校验 → SQL执行 → 最终回答`。
- 标签空状态改为“等待首次有效 TableRAG 检索后发布”。
- 新增收敛保护面板，展示 `data_force_final_answer`。
- `GraphRecursionError` 仍作为异常兜底保留，但页面会说明这是 LangGraph 安全上限，
  不建议仅提高 `recursion_limit` 掩盖循环。
- 原有 MySQL 预检、TableRAG、SQL 校验/执行、结果表、ChartSpec、工具事件、
  NDJSON 流和时间戳日志均保留。

## 4、验证结果

- `tests/service_agent/test-data-agent`：80 passed。
- `tests/test_query_labels_tool.py`：3 passed。
- `tests/test_lead_agent_model_resolution.py` 与
  `tests/test_harness_boundary.py`：24 passed。
- `tests/test_create_deerflow_agent.py`：47 passed。
- 相关文件 Ruff check、Ruff format check、Python compileall 和
  `git diff --check`：通过。

仓库当前 `uv.lock` 第 810 行存在重复 `monocle` key，导致 `uv run pytest` 在测试启动前
解析失败。本次没有修改该锁文件，验证改用同一项目虚拟环境
`backend\.venv\Scripts\python.exe` 执行。

## 5、剩余风险

- 默认总预算 10 是依据实际 13 次调用在 recursion limit 处失败的日志设置，目标是给最终
  模型回答保留足够图步数。业务库检索特别复杂时可能提前进入“证据不足”回答，可在明确
  评估 LangGraph 步数和模型成本后小幅调整配置，不应无边界提高。
- `data_force_final_answer` 强制的是停止继续调用工具，不会伪造成功结果。SQL 未成功时，
  最终回答必须明确报告缺失 Evidence、校验失败或执行失败。
