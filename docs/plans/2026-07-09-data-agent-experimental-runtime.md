# DataAgent 实验性运行层开发计划

## 1、现状确认

- [X] 1.1 阅读根目录 `AGENTS.md` 与 `backend/AGENTS.md`，确认后端 harness/app 边界、middleware 链和测试要求。
- [X] 1.2 从 `dev` 新建功能分支 `feat/data-agent`，避免直接在 `dev` 上开发。
- [X] 1.3 梳理 `create_deerflow_agent(...)`、原生 lead-agent 工厂、TableRAG MCP Server、`table-rag-agent` Skill 和既有 DataAgent 模板。

## 2、实验性 DataAgent 运行层设计

- [X] 2.1 在 `backend/packages/harness/deerflow-dev/agents/data_agent` 下建立实验性 DataAgent 包，避免污染 `deerflow.*` 稳定公共 API。
- [X] 2.2 定义 DataAgent 常量、状态结构和系统提示片段，默认绑定 `data-agent`、TableRAG Skill 白名单和 `tablerag_*` MCP 使用约束。
- [X] 2.3 通过 `create_deerflow_agent(...)` 重新创建图，同时复用 lead-agent 的模型、工具、prompt、deferred tool、skill 和 middleware 组装逻辑。

## 3、QueryContextMiddleware 编排

- [X] 3.1 实现意图归一化：用可配置黑话/别名表把用户问题中的业务黑话映射为标准值。
- [X] 3.2 实现轻量实体抽取：从问题中抽取时间、指标、维度、地区、排序、数量和关键词标签。
- [X] 3.3 将结构化 Query Context 写入 `DataAgentState`，并作为隐藏上下文注入模型请求，同时通过 custom stream event 输出给执行脚本观察。

## 4、执行脚本与文档

- [X] 4.1 在 `backend/tests/service_agent/test-data-agent` 下编写 Python 控制台流式执行脚本，不新增 Gateway 路由。
- [X] 4.2 更新 `docs/guide/used-api.md`、`backend/AGENTS.md` 和 DataAgent 文档，说明新的实验入口和执行方式。
- [X] 4.3 编写 review 文档记录实现、验证命令、限制和后续优化方向。

## 5、验证

- [X] 5.1 增加后端单元测试，覆盖 QueryContextMiddleware、DataAgent middleware 组装和 `create_deerflow_agent(...)` 调用边界。
- [X] 5.2 执行 ruff/pytest/py_compile 定向验证。
- [X] 5.3 检查 `git status`，确认没有生成物、缓存或真实凭据进入变更。

## 6、生产级补强

- [X] 6.1 配置加载改为 fail-closed，保留显式空 `tool_groups` / `skills` 语义，并确保 DataAgent 加载同名 SOUL。
- [X] 6.2 隔离 MCP 工具，只允许只读 TableRAG 检索工具进入 DataAgent；TableRAG 缺失或被 Skill 策略过滤时默认拒绝启动。
- [X] 6.3 增加 MySQL 方言的单语句只读 SQL 校验，拒绝 DDL/DML、多语句、锁、文件读写、危险函数、优化器 Hint、可执行注释和未绑定占位符。
- [X] 6.4 增加只读 MySQL 执行层，限制连接/查询超时、最大返回行数、单元格长度和结果总字符数，并对凭据脱敏。
- [X] 6.5 增加 `data_validate_sql`、`data_execute_sql`、`data_build_chart_spec` 专用工具和成功/失败阶段状态。
- [X] 6.6 将编排 middleware 从纯 Prompt 提示增强为阶段门禁：TableRAG -> SQL 校验 -> SQL 执行 -> ChartSpec，并增加单轮调用预算。
- [X] 6.7 修复 QueryContext 实体去重、长短指标重叠和噪声关键词，补充控制台 `values` 状态输出。
- [X] 6.8 增加 CSV 样例读取、数据库连通性和 1～2 条真实流程验证；真实凭据只通过环境变量注入。
- [X] 6.9 更新 DataAgent 使用说明、公共 API 文档、后端架构说明和 review 结论。
- [X] 6.10 执行格式化、ruff、定向测试、真实 ChartSpec 流程和最终代码审查。

## 7、审查后追加修复

- [X] 7.1 修复异步等待 TableRAG 线程锁被取消时可能遗留永久持锁的问题，并用弱引用锁表避免长期会话键堆积。
- [X] 7.2 修复 MySQL 配置解析失败时 `settings` 未赋值导致二次异常的问题。
- [X] 7.3 修复新 SQL 仅校验、替代 SQL 执行失败或预算耗尽时覆盖最后成功执行状态的问题。
- [X] 7.4 在执行预算耗尽后阻止继续检索/校验，避免最终结构化状态回退。
- [X] 7.5 强制 custom-agent 配置中的 Skill 和工具组不能扩大 DataAgent 白名单。
- [X] 7.6 增加数据库错误凭据脱敏、有限数值 ChartSpec 校验、CSV 样例数和 recursion limit 上限。
- [X] 7.7 将 DataAgent 流式脚本、单元测试和本地 CSV 迁移到 `backend/tests/service_agent/test-data-agent`，并改为向上探测仓库根目录，避免目录移动后固定 `parents[n]` 失效。
- [X] 7.8 为控制台脚本增加 `--log-path`、时间戳日志文件、标准 logging 格式、流式文本落盘、运行变量头信息和凭据脱敏。

## 8、本地可视化调试页面

- [X] 8.1 在 `backend/tests/service_agent/test-data-agent` 新增独立 Python Web 脚本，不接入正式 Gateway 路由。
- [X] 8.2 提供浏览器对话界面，并以结构化轨迹展示 QueryContext、阶段、工具调用、TableRAG 检索、SQL 校验、SQL 执行结果和 ChartSpec。
- [X] 8.3 复用现有时间戳日志和凭据脱敏能力，限制输入、并发与本地监听边界。
- [X] 8.4 增加页面、请求校验和流事件转换测试。
- [X] 8.5 执行 Ruff、pytest、页面启动和浏览器冒烟验证，并同步 README、API 指南和 review 文档。
