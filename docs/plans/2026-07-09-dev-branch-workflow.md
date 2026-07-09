# dev 长期二次开发分支调整计划

## 1、分支模型确认

- [X] 1.1 确认 `origin` 指向 fork 仓库，`upstream` 指向 DeerFlow 原生仓库。
- [X] 1.2 确认本地存在 `dev` 分支，当前开发分支为 `feat/data-agent-text2sql`。
- [X] 1.3 明确 `main` 作为原生基线，`dev` 作为长期二次开发集成分支。

## 2、规范与文档更新

- [X] 2.1 更新根目录 `AGENTS.md`，将新功能分支来源从 `main` 调整为 `dev`。
- [X] 2.2 更新 fork/upstream 工作流文档，补充 `main -> dev -> feat/*` 的长期维护流程。
- [X] 2.3 将本地 `tabelrag.yaml` 加入忽略规则，避免提交真实 DSN 配置。

## 3、合并与推送

- [X] 3.1 执行必要验证，确认当前 DataAgent / TableRAG 变更可提交。
- [X] 3.2 提交当前功能分支。
- [ ] 3.3 合并功能分支到 `dev`。
- [ ] 3.4 推送 `origin/dev`。


