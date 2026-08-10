# TokenUsageIndicator 缓存指标报错修复审查

## 1. 错误现象

打开包含历史令牌用量的对话时，`TokenUsageIndicator` 在渲染缓存详情区域时抛出 `ReferenceError: cacheMetrics is not defined`，导致页面运行时错误。

## 2. 根因分析

合并提交 `d921969c` 的两个父版本分别包含：

- 原 `dev` 的提示词缓存指标展示，通过 `getPromptCacheMetrics(usage)` 派生 `cacheMetrics`。
- 上游 `main` 的上下文窗口百分比展示，通过 `contextUsage` 派生 `contextPercentage`。

冲突合并保留了缓存指标的导入和 JSX 展示区域，也保留了新的上下文百分比逻辑，但遗漏了 `cacheMetrics` 的变量声明。TypeScript 已能静态发现该问题；开发服务仍完成模块编译后，在历史用量触发对应 JSX 分支时产生运行时 `ReferenceError`。

## 3. 修复内容

- 在选择当前头部 token usage 后调用 `getPromptCacheMetrics(usage)`，恢复缓存指标派生变量。
- 保持 `contextPercentage`、历史后端用量优先级、进行中消息累加和用户展示偏好不变。
- 新增组件 DOM 回归测试，使用包含缓存读取量的历史后端用量打开令牌菜单，并验证缓存读取、未缓存输入和命中率均可渲染。

## 4. 验证结果

- TokenUsageIndicator 组件测试及核心 usage 测试：通过，共 2 个测试文件、10 个测试。
- 目标源文件和测试文件 ESLint：通过。
- 目标源文件和测试文件 Prettier：通过。
- TypeScript 检查不再报告 `cacheMetrics` 的 4 个未定义错误；仓库仍有 `core/messages/utils.ts` 和 `query-intent-card.test.ts` 的既有无关类型错误。
- 现有 Next.js 开发服务完成热更新编译，最新日志不再出现 `cacheMetrics` 运行时错误。

## 5. 审查结论

本次修复恢复了冲突合并遗漏的一条纯派生语句，没有引入额外状态或副作用。缓存指标只在有效用量包含缓存读取量时显示，原有空值行为保持不变。未修改公共 API、目录结构或模块边界，无需更新 `docs/guide/used-api.md`。
