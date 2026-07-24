# 阶段 3：DataAgent Lead 与 Schema-RAG

## 1. 目标

让独立训练的 DataAgent Lead 完成业务意图解析、Schema-RAG、业务口径确认和任务路由。

## 2. 模型职责

DataAgent Lead 模型训练目标：

- 垂直业务术语识别。
- 隐含指标和业务口径识别。
- Schema-RAG Query 生成。
- Evidence 选择。
- 歧义识别。
- SubAgent 路由。
- 多个结构化结果的汇总。

模型不负责：

- SQL 技术校验。
- 数据库执行。
- 机器学习算法执行。
- 图表文件生成。

## 3. Schema-RAG

- 使用精确 MCP 工具名 `sqlrag_retrieve`。
- `hybrid-search` 接收完整自然语言问题。
- `search-*` 接收短关键词列表。
- `expand-join-graph` 接收候选表。
- 检索结果必须绑定当前用户轮次和 `data_source_id`。
- Evidence 不足时继续检索或请求用户确认，不生成猜测 SQLTask。

## 4. 业务确认

支持：

- `auto`
- `on_ambiguity`
- `always`

确认通过后生成不可变 `QuerySnapshotV1`。用户修改查询条件时生成新 Snapshot，旧 SQLTask 和执行授权失效。

## 5. 路由规则

- 数据查询：SQL SubAgent。
- 数据查询后需要统计分析或机器学习：SQL SubAgent 完成后调用 Analysis SubAgent。
- 数据查询后需要可视化：SQL SubAgent 完成后调用 Chart SubAgent。
- 分析结果需要图表：Analysis SubAgent 完成后调用 Chart SubAgent。

## 6. Todo

- [ ] 3.1 定义 DataAgent Lead Prompt 和 Prompt Version。
- [ ] 3.2 将业务标签、歧义和 Evidence 统一写入 QuerySnapshot。
- [ ] 3.3 实现 Schema-RAG 调用次数和 Token 预算。
- [ ] 3.4 实现业务确认、修改、取消和恢复流程。
- [ ] 3.5 实现 QuerySnapshot 到 SQLTask 的转换。
- [ ] 3.6 实现 SQL、Analysis、Chart 的路由规则。
- [ ] 3.7 实现 `needs_evidence` 返回后的补充检索和重新委派。
- [ ] 3.8 增加意图、检索、确认、路由和恢复测试。

## 7. 阶段退出条件

- [ ] Lead 可以稳定生成完整 QuerySnapshot。
- [ ] Lead 未获得有效 Evidence 时不会启动 SQL SubAgent。
- [ ] 用户确认前不会执行 SQL。
- [ ] SQL、Analysis 和 Chart 路由符合任务类型。
