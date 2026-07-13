# Data Lead Agent 与垂直 Subagent 业务架构方案

## 1、方案结论

- [X] 1.1 数据分析业务采用 `data-lead-agent` 作为业务入口智能体。
- [X] 1.2 `data-lead-agent` 负责理解用户问题、判断分析路径、拆分任务、选择专业 subagent、验收结果并向用户交付最终答案。
- [X] 1.3 垂直专业能力沉到 subagent，包括 `SQL-Agent`、`chart-Agent`、`ML-Agent` 等。
- [X] 1.4 subagent 不是只能做单步碎片任务，而是可以完成一个垂直闭环任务。
- [X] 1.5 强业务路由和权限边界后续应由代码或 middleware 兜底，不能只依赖 prompt。

最终分层如下：

```text
data-lead-agent
  -> SQL-Agent
  -> chart-Agent
  -> ML-Agent
```

## 2、角色边界

### 2.1 data-lead-agent

- [X] 2.1.1 作为数据分析业务的主入口，通过 custom agent 配置强制绑定业务。
- [X] 2.1.2 负责识别用户真实意图，例如查询、统计、对比、归因、预测、可视化或报告生成。
- [X] 2.1.3 负责编排任务顺序，例如先查数、再画图、再解释，或先建模、再输出结论。
- [X] 2.1.4 负责调用合适的 subagent，并把上下游结果组织成用户能理解的答案。
- [X] 2.1.5 负责满足用户个性化表达需求，例如口径解释、业务建议、图表说明、报告风格。

`data-lead-agent` 不应承担所有专业执行细节。它可以处理简单查询和简单解释，但复杂 SQL、图表配置、机器学习流程应交给专业 subagent。

### 2.2 SQL-Agent

- [X] 2.2.1 负责数据结构召回，包括表、字段、指标、维度、关联关系和业务口径。
- [X] 2.2.2 负责 SQL 生成、校验、执行和必要时的修正。
- [X] 2.2.3 负责判断 SQL 执行结果是否足以回答用户问题。
- [X] 2.2.4 负责返回结构化结果，包括 SQL、执行数据、口径说明、风险和未满足条件。

`SQL-Agent` 是一个垂直闭环 subagent，不只是“生成 SQL 的一步工具”。

### 2.3 chart-Agent

- [X] 2.3.1 负责根据 SQL 结果或结构化数据选择图表类型。
- [X] 2.3.2 负责生成图表配置、字段映射、标题、单位和图例说明。
- [X] 2.3.3 负责判断数据是否适合可视化，例如样本过少、维度过多、指标类型不匹配。
- [X] 2.3.4 负责输出可被前端渲染或后续报告消费的 ChartSpec。

`chart-Agent` 不直接查库，默认只消费上游结构化数据。

### 2.4 ML-Agent

- [X] 2.4.1 负责预测、分类、聚类、异常检测、特征分析等机器学习任务。
- [X] 2.4.2 负责判断任务是否真的需要机器学习，而不是普通 SQL 统计即可完成。
- [X] 2.4.3 负责模型选择、训练或推理、评估指标和结果解释。
- [X] 2.4.4 负责返回模型结论、可信度、限制条件和业务解释。

`ML-Agent` 只在明确需要建模或高级分析时调用，避免把普通分析问题复杂化。

## 3、训练与微调目标

- [X] 3.1 `data-lead-agent` 的训练目标是业务编排能力。
- [X] 3.2 subagent 的训练目标是垂直专业执行能力。
- [X] 3.3 整体业务效果来自 lead-agent 编排能力与 subagent 专业能力的协同。

训练数据应按目标拆分：

```text
训练 data-lead-agent:
  - 用户意图识别
  - 任务拆解
  - subagent 选择
  - delegation prompt 编写
  - 结果验收
  - 失败重试策略
  - 最终答案合成

训练 SQL-Agent / chart-Agent / ML-Agent:
  - 专业任务 SOP
  - 工具调用顺序
  - 领域知识和业务口径
  - 输出格式
  - 错误修正
  - 结果自检
```

不建议把所有训练数据都塞给一个“大而全”的业务智能体。数据分析业务可以先用 prompt、config、middleware 固定流程，再把高频稳定的专业闭环任务沉淀为 subagent 训练集。

## 4、DeerFlow 当前能力判断

- [X] 4.1 DeerFlow 当前支持 custom lead-agent，通过 `agent_name` 加载业务配置、SOUL、工具组和 skill。
- [X] 4.2 DeerFlow 当前支持 custom subagent，通过 `subagents.custom_agents` 定义专业 subagent 的 prompt、工具、skill、模型、超时和 turn 限制。
- [X] 4.3 当前 `task` 工具由 lead-agent 主动调用，subagent 的执行时机和任务描述主要由 lead-agent 决定。
- [X] 4.4 当前 custom agent 配置还不能直接声明 `allowed_subagents`，无法按业务强制限制可访问的 subagent 类型。
- [X] 4.5 当前 subagent 运行结果会返回给 lead-agent，由 lead-agent 继续综合；lead-agent 不会在 subagent 中间步骤上实时改派，除非后续改造编排层。

因此，当前 DeerFlow 已经具备基础形态，但要做强业务产品，还需要补充业务级 subagent 权限和编排约束。

## 5、推荐落地路线

- [ ] 5.1 创建 `data-lead-agent` custom agent，配置数据分析业务专属 prompt、工具组和 skill。
- [ ] 5.2 创建 `SQL-Agent`、`chart-Agent`、`ML-Agent` custom subagent，分别限定工具和输出协议。
- [ ] 5.3 为 custom agent 增加 `allowed_subagents` 配置字段。
- [ ] 5.4 在 lead-agent prompt 构建时只展示当前业务允许的 subagent。
- [ ] 5.5 在 `task_tool` 执行层强制校验 `subagent_type` 是否属于当前业务允许范围。
- [ ] 5.6 为每个 subagent 定义标准输入和标准输出协议，方便 lead-agent 验收。
- [ ] 5.7 为 SQL、Chart、ML 三类 subagent 分别建设专业训练/评测集。
- [ ] 5.8 为 `data-lead-agent` 建设编排训练/评测集，重点覆盖路由、拆解、验收、失败重试和最终合成。

## 6、设计原则

- [X] 6.1 lead-agent 是业务负责人，不是所有专业细节的执行者。
- [X] 6.2 subagent 是专业能力单元，可以完成垂直闭环任务。
- [X] 6.3 Search、Code、Report、SQL、Chart、ML 这类能力既可以作为 lead-agent，也可以作为 subagent，取决于它们是否面向最终用户作为入口。
- [X] 6.4 在数据分析业务中，SQL、Chart、ML 更适合作为 `data-lead-agent` 下的专业 subagent。
- [X] 6.5 强业务流程应由配置、middleware 或编排代码保证，prompt 和训练只负责提升智能决策质量。

一句话总结：

```text
data-lead-agent 负责安排活、验收活、交付用户结果；
SQL-Agent、chart-Agent、ML-Agent 负责把各自领域的活专业地干完。
```
