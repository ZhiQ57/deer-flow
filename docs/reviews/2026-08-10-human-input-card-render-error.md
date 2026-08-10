# HumanInputCard 前端报错修复审查

## 1. 错误现象

`frontend/src/components/workspace/messages/human-input-card.tsx` 无法通过 TypeScript 解析，首个错误位于原文件第 674 行，后续产生多个 JSX 闭合标签和表达式语法错误，导致相关前端模块无法编译。

## 2. 根因分析

合并提交 `d921969c` 同时引入了两条功能线：

- `dev` 一侧新增 `multi_question_choice` 多问题审批卡。
- 上游 `main` 一侧新增 v2 `form` 表单卡。

冲突合并后的 JSX 将普通选项条件表达式的开头残留在表单表达式之前，却没有保留对应分支和闭合部分；文本输入表达式末尾也残留了一个没有内容的已回答分支。此外，导入区重复引入了 `CheckIcon`。这些残留使 JSX 抽象语法树无法建立，属于合并结果结构损坏，并非运行时状态或接口数据问题。

## 3. 修复内容

- 删除悬空的普通选项条件表达式，使 v2 表单恢复为独立渲染分支。
- 删除文本输入尾部的空条件分支。
- 将单选已回答提示限定为非多问题、非文本和非表单模式，避免多问题审批卡重复显示回答。
- 保留并纳入工作区已有的重复 `CheckIcon` 导入清理。
- 未修改 `HumanInputCard` 属性、`HumanInputRequest`/`HumanInputResponse` 协议或模块边界。

## 4. 验证结果

- HumanInputCard 单元测试与 DOM 测试：通过，共 2 个测试文件、13 个测试。
- 目标组件 ESLint：通过。
- 目标组件 Prettier：通过。
- `git diff --check`：通过。
- 全量 TypeScript 检查：目标组件原有的 12 个 JSX 解析错误已全部消失；仓库当前仍有其他文件的既有类型错误，集中在 `chat-box.tsx`、`token-usage-indicator.tsx`、`core/messages/utils.ts` 和 `query-intent-card.test.ts`，不属于本次目标文件修复范围。

## 5. 审查结论

修复为最小范围的 JSX 结构校正，覆盖普通选项、自由文本、表单和多问题审批四类现有渲染路径。现有测试同时验证表单和多问题模式，回归风险较低。未发现公共 API、目录结构或模块边界变化，因此无需更新 `docs/guide/used-api.md`。
