# DataAgent 标签时序与递归收敛修复计划

## 目标

修复 DataAgent Web 调试流程中查询标签过早展示和工具循环无法收敛的问题，保持完整的
TableRAG 检索、标签展示、SQL 校验、SQL 执行和结果呈现链路。

## 计划

1. 仓库与问题核对
   - [X] 1.1 从 `dev` 创建 `fix/data-agent-label-flow-recursion` 分支。
   - [X] 1.2 阅读仓库及 backend 开发规范。
   - [X] 1.3 核对 DataAgent Skill、middleware、状态、Web 页面和失败日志。
2. 测试驱动定位
   - [X] 2.1 增加检索前禁止发布查询标签的失败测试。
   - [X] 2.2 增加检索完成后允许发布标签及阶段顺序测试。
   - [X] 2.3 增加工具预算耗尽后强制最终回答的收敛测试。
   - [X] 2.4 增加 Web 页面阶段顺序与收敛错误呈现测试。
3. 代码修复
   - [X] 3.1 让 DataAgent 标签 middleware 强制依赖首次有效 TableRAG 检索。
   - [X] 3.2 调整 DataAgent prompt，使流程与 Skill 文档保持一致。
   - [X] 3.3 增加单轮总工具预算和确定性强制最终回答状态。
   - [X] 3.4 修复 Web 页面标签、阶段轨迹和递归错误的展示。
4. 验证与审查
   - [X] 4.1 运行标签工具、DataAgent 编排和 Web 页面专项测试。
   - [X] 4.2 运行 Ruff、格式检查和 Python 编译检查。
   - [X] 4.3 审查改动并修复发现的问题。
5. 文档同步
   - [X] 5.1 更新相关 README、backend/AGENTS.md 和 API 使用说明。
   - [X] 5.2 新增 review 文档并记录测试结论。
