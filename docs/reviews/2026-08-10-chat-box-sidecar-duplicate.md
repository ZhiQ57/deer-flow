# ChatBox Sidecar 重复定义修复审查

## 1. 错误现象

访问 `/workspace/chats/new` 时 Next.js 返回 500，Turbopack 报告 `chat-box.tsx` 中 `SidecarPanel` 和 `useMaybeSidecar` 被重复定义。

## 2. 根因分析

合并提交 `d921969c` 的两个父版本采用了不同的右侧面板加载方式：

- 原 `dev` 从 `../sidecar` 静态导入 `SidecarPanel` 和 `useMaybeSidecar`。
- 上游 `main` 为缩小初始包体，将 `SidecarPanel` 改为 `next/dynamic` 动态加载，并从 `../sidecar/context` 直接导入 Hook。

冲突合并同时保留了两种实现，因此同一模块作用域内出现两个 `SidecarPanel` 和两个 `useMaybeSidecar` 绑定。合并还将 SQL 执行模块的静态导入留在动态组件声明之后，违反项目导入顺序规则。

## 3. 修复内容

- 删除 `../sidecar` 的重复静态导入。
- 保留 `SidecarPanel` 的动态加载，维持交互后加载的包体边界。
- 保留 `useMaybeSidecar` 从上下文模块的直接导入。
- 将 SQL 执行模块导入移动到顶部导入组，恢复合法且符合 ESLint 的模块结构。
- 未修改 ChatBox 属性、右侧面板优先级、布局状态或 SQL 执行行为。

## 4. 验证结果

- 右侧面板动态加载边界测试：通过，共 1 个测试文件、3 个测试。
- 目标文件 ESLint：通过。
- 目标文件 Prettier：通过。
- TypeScript 检查不再报告 `chat-box.tsx` 的重复定义错误；仓库其他文件仍有既有类型错误。
- 通过当前 Next.js 开发服务请求 `/workspace/chats/new`：返回预期的未登录重定向 `307`，不再返回 `500`，开发服务日志显示重新编译成功。

## 5. 审查结论

修复恢复了上游性能优化要求的动态加载边界，同时保留 `dev` 中的 SQL 结果面板扩展。变更仅涉及导入组织，不改变公共 API、目录结构或运行时分支，回归风险较低，无需更新 `docs/guide/used-api.md`。
