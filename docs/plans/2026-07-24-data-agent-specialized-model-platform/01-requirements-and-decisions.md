# 阶段 0：需求与架构决策

## 1. 目标

冻结业务目标、系统边界和非功能需求，避免开发过程中重复调整 Agent 职责。

## 2. 功能需求

### 2.1 DataAgent Lead

- 理解垂直业务用户问题。
- 识别指标、维度、过滤条件、时间范围、粒度和排序。
- 识别用户没有明确表达的业务口径。
- 调用 `sqlrag_retrieve` 获取当前数据源的 Schema Evidence。
- 生成并维护 Query Snapshot。
- 根据任务类型选择 SQL、Analysis 或 Chart SubAgent。
- 汇总结构化结果并生成最终答案。

### 2.2 SQL SubAgent

- 只处理当前 SQLTask 中的业务口径和 Schema Evidence。
- 生成 SQL。
- 调用 SQL 校验工具。
- 调用 SQL 执行工具。
- 根据结构化校验错误和数据库错误修复 SQL。
- 在重试预算内重新校验和执行。
- 返回 SQLResult Artifact。

### 2.3 Analysis SubAgent

- 消费 SQLResult Artifact。
- 完成描述统计、对比、趋势、归因、异常检测、预测、分类、聚类等任务。
- 返回 AnalysisResult Artifact。

### 2.4 Chart SubAgent

- 消费 SQLResult 或 AnalysisResult Artifact。
- 生成字段映射、图表类型、标题、单位、图例和 ChartSpec。
- 返回 ChartResult Artifact。

## 3. 非功能需求

- Agent 模型可以独立配置和替换。
- Contract 向后不兼容变更必须提升主版本。
- 每个任务固定模型、Prompt 和 Contract 版本。
- 数据库连接使用只读账号。
- SQL、分析和图表执行具有超时、资源和结果大小限制。
- 任务、模型、工具、错误和结果可以追踪。
- 失败任务可以定位到 Agent、Model、Tool、Executor 或数据源。

## 4. 架构决策

### ADR-001：保留 SQL SubAgent

保留原因是 SQL SubAgent 绑定独立 SQL 模型，并负责完整 SQL 闭环。若 SQL SubAgent 只转发 SQL 字符串，则应删除该 SubAgent，改为 Lead 直接调用类型化工具。

### ADR-002：Lead 不审核 SQL 技术正确性

Lead 负责业务口径确认。SQL AST、对象权限、只读限制和执行限制由确定性校验器与 Executor 负责。

### ADR-003：SQL Executor 与 SQL SubAgent 分离

SQL SubAgent 负责推理，SQL Executor 负责数据库访问。Executor 可以有两种部署：

- 进程内 Provider：本地开发和首版联调。
- 内网 Query Executor：生产环境隔离数据库身份时使用。

### ADR-004：不使用 bash 承担垂直能力

通用 bash 缺少 SQL Contract、权限范围和结果校验。垂直能力必须使用专用工具。

## 5. Todo

- [ ] 0.1 与业务方确认首版支持的查询、分析和图表场景。
- [ ] 0.2 确认首版数据源、数据库类型和只读账号策略。
- [ ] 0.3 确认 Lead、SQL、Analysis、Chart 的职责边界。
- [ ] 0.4 确认各 Agent 首版模型名称和部署地址。
- [ ] 0.5 确认 SQL Executor 首版采用进程内 Provider 还是内网服务。
- [ ] 0.6 确认响应时间、重试次数、结果行数和结果大小预算。
- [ ] 0.7 将已确认的决策更新到本目录 README。

## 6. 阶段退出条件

- [ ] 功能需求和非功能需求无未决冲突。
- [ ] Agent 职责无重复执行路径。
- [ ] SQL Executor 部署方式已有首版结论。
- [ ] 后续阶段使用的模型名称、数据源和预算已有配置草案。
