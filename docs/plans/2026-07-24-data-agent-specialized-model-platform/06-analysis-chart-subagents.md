# 阶段 5：Analysis 与 Chart SubAgent

## 1. 目标

将统计分析、机器学习和图表生成从 Lead 中拆分为独立模型能力。

## 2. Analysis SubAgent

输入：

- SQLResult Artifact。
- 分析目标。
- 特征字段和目标字段。
- 算法限制。
- 资源预算。

处理：

- 数据质量检查。
- 描述统计。
- 对比、趋势和相关性分析。
- 预测、分类、聚类或异常检测。
- 模型评估。

输出：

- AnalysisResult Artifact。
- 方法、参数、指标、结论和限制。
- 可选模型文件和中间结果引用。

## 3. Chart SubAgent

输入：

- SQLResult 或 AnalysisResult Artifact。
- 用户展示要求。
- 图表风格和布局限制。

处理：

- 图表类型选择。
- 字段、维度和指标映射。
- 标题、单位、图例和标签生成。
- ChartSpec 校验。
- 可选图表文件生成。

输出：

- ChartResult Artifact。
- ChartSpec。
- 图表文件引用。
- 警告和适用性说明。

## 4. 权限

- Analysis 和 Chart 不获得数据库 DSN。
- 不获得 `data_execute_sql`。
- 不获得 `task`。
- 只能读取任务声明的 Artifact。
- 输出只能写入当前任务 workspace 或 Artifact Store。

## 5. Todo

- [ ] 5.1 定义 AnalysisTask 和 AnalysisResult。
- [ ] 5.2 定义 ChartTask 和 ChartResult。
- [ ] 5.3 配置独立 Analysis 模型和 Chart 模型。
- [ ] 5.4 实现 Analysis Tool 和资源限制。
- [ ] 5.5 实现 Chart Tool、ChartSpec 和字段校验。
- [ ] 5.6 实现 Artifact 授权读取。
- [ ] 5.7 实现 Lead 的 Analysis/Chart 路由和结果汇总。
- [ ] 5.8 增加分析算法、图表类型、权限和资源预算测试。
- [ ] 5.9 增加前端分析结果和图表渲染。

## 6. 阶段退出条件

- [ ] Analysis 和 Chart 可以使用独立模型。
- [ ] 两个 SubAgent 均无法直接查询数据库。
- [ ] Chart 字段全部来自上游 Artifact schema。
- [ ] 分析和图表结果可以在页面刷新后恢复。
