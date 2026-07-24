# 阶段 6：模型训练、评测与版本管理

## 1. 目标

建立四类模型的独立训练、离线评测、灰度和回滚流程。

## 2. 训练数据边界

| 模型 | 训练数据 |
|---|---|
| DataAgent Lead | 用户问题、业务术语、业务口径、Schema-RAG 查询、Evidence、路由和最终回答 |
| SQL 模型 | SQLTask、目标 SQL、校验错误、执行错误、修复 SQL 和最终结果 |
| Analysis 模型 | AnalysisTask、算法选择、参数、指标和结论 |
| Chart 模型 | ChartTask、数据 schema、ChartSpec 和展示要求 |

训练数据必须脱敏，不能包含真实 DSN、密码、Token 或未授权业务数据。

## 3. 版本字段

- `model_id`
- `model_version`
- `prompt_version`
- `contract_version`
- `dataset_version`
- `evaluation_version`

一次任务开始后固定模型和 Prompt 版本。重试期间不热切换。

## 4. 评测指标

### 4.1 DataAgent Lead

- 意图识别准确率。
- 业务口径识别准确率。
- Schema-RAG Recall@K。
- 无效检索次数。
- SubAgent 路由准确率。
- Query Snapshot 完整率。

### 4.2 SQL 模型

- SQL execution accuracy。
- 首次执行成功率。
- SQL 修复成功率。
- 平均 Attempt 数。
- 非法 SQL 拒绝率。
- `needs_evidence` 判断准确率。

### 4.3 Analysis 模型

- 方法选择准确率。
- 参数有效率。
- 指标计算正确率。
- 结果可复现率。

### 4.4 Chart 模型

- 图表类型准确率。
- 字段映射正确率。
- ChartSpec 解析成功率。
- 不适合绘图场景拒绝准确率。

## 5. 发布流程

```text
训练
  -> 离线评测
  -> Contract 兼容检查
  -> 测试环境回放
  -> 小流量灰度
  -> 指标对比
  -> 全量或回滚
```

## 6. Todo

- [ ] 6.1 定义四类训练样本格式。
- [ ] 6.2 建立数据脱敏和质量检查。
- [ ] 6.3 建立每类模型的基线评测集。
- [ ] 6.4 建立模型注册表和版本解析。
- [ ] 6.5 在 Run、Task 和 Artifact 中记录模型与数据版本。
- [ ] 6.6 实现离线回放和候选模型对比报告。
- [ ] 6.7 实现按 Agent 独立灰度和回滚。
- [ ] 6.8 建立模型替换后的 Contract 回归检查。

## 7. 阶段退出条件

- [ ] 每个模型均有独立训练集、评测集和基线。
- [ ] 替换一个模型不会修改其他 Agent 配置。
- [ ] 任一生产结果可以追溯到具体模型和 Prompt 版本。
- [ ] 灰度异常时可以只回滚对应 Agent 模型。
