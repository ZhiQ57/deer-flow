# ChatBox Sidecar 重复定义修复计划

## 1. 问题确认

- [X] 1.1 阅读前端开发规范并检查工作区状态。
- [X] 1.2 运行 TypeScript 类型检查复现 `SidecarPanel` 和 `useMaybeSidecar` 重复定义错误。
- [X] 1.3 对比合并提交 `d921969c` 的两个父版本，确认冲突合并来源。

## 2. 修复实施

- [X] 2.1 从最新本地 `dev` 创建 `fix/chat-box-sidecar-duplicate` 分支。
- [X] 2.2 删除冲突合并残留的 Sidecar 静态导入。
- [X] 2.3 保留动态加载边界和 Sidecar 上下文 Hook 的直接导入。
- [X] 2.4 保持 ChatBox 公共属性、右侧面板优先级和 SQL 执行逻辑不变。

## 3. 验证与审查

- [X] 3.1 运行右侧面板动态加载边界单元测试。
- [X] 3.2 运行 TypeScript 类型检查，确认目标错误消失并记录其他既有错误。
- [X] 3.3 运行目标文件 ESLint、Prettier 和 Git 差异检查。
- [X] 3.4 通过现有 Next.js 开发服务验证聊天路由不再返回 500。
- [X] 3.5 审查修复范围与回归风险。
- [X] 3.6 在 `docs/reviews/` 记录根因、修复与验证结论。
