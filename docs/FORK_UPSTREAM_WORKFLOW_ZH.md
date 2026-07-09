# Fork 仓库与 `dev` 长期二次开发工作流

本文档记录本仓库后续固定采用的 Git 分支模型：保留 `main` 作为追踪 DeerFlow 原生仓库的干净基线，使用 `dev` 作为自己的长期二次开发集成分支。

## 1. 远程仓库约定

- `origin`：自己的 fork 仓库，地址为 `https://github.com/ZhiQ57/deer-flow.git`，用于推送自己的代码。
- `upstream`：DeerFlow 原生仓库，地址为 `https://github.com/bytedance/deer-flow.git`，只用于拉取官方更新。

检查命令：

```powershell
cd D:\A-PythonWork\AOpenGithub\deer-flow
git remote -v
```

## 2. 分支模型

```text
upstream/main  DeerFlow 原生仓库主线，只拉取不推送
origin/main    fork 仓库中的原生基线，尽量保持等同 upstream/main
origin/dev     自己的长期二次开发集成分支
feat/*         单个功能分支，从 dev 创建
fix/*          单个修复分支，从 dev 创建
```

核心原则：

- 不在 `main` 上做二次开发。
- `main` 只负责同步 `upstream/main`。
- `dev` 保存 DataAgent、TableRAG、业务定制等二次开发代码。
- 每个新需求从最新 `dev` 新建功能分支，完成后合并回 `dev`。

## 3. 同步 DeerFlow 原生仓库更新

推荐先更新 `main`，再把 `main` 合并到 `dev`：

```powershell
cd D:\A-PythonWork\AOpenGithub\deer-flow

git fetch upstream
git fetch origin

git switch main
git merge upstream/main
git push origin main

git switch dev
git pull origin dev
git merge main
```

如果合并 `main` 到 `dev` 时出现冲突：

```powershell
git status
# 手动解决冲突文件
git add <conflict-files>
git commit
```

验证通过后推送：

```powershell
git push origin dev
```

如果确认本次合并方向错误，可以在提交前放弃：

```powershell
git merge --abort
```

## 4. 新功能开发流程

```powershell
cd D:\A-PythonWork\AOpenGithub\deer-flow

git switch dev
git pull origin dev
git switch -c feat/my-feature
```

开发完成后：

```powershell
git status --short
git add <changed-files>
git commit -m "feat: 中文说明做了什么、为什么做、验证了什么"
```

合并回 `dev`：

```powershell
git switch dev
git merge feat/my-feature
git push origin dev
```

## 5. 当前任务推荐验证命令

后端配置或 DataAgent 改动：

```powershell
cd D:\A-PythonWork\AOpenGithub\deer-flow\backend
uv run pytest tests/test_data_agent_config.py -q
```

TableRAG MCP 导入验证：

```powershell
D:\A-PythonWork\AOpenGithub\deer-flow\backend\.venv\Scripts\python.exe -m table_rag.mcp --help
```

前端路由或页面改动：

```powershell
cd D:\A-PythonWork\AOpenGithub\deer-flow\frontend
pnpm exec tsc --noEmit --pretty false
```

## 6. 注意事项

- 不要提交 `config.yaml`、`extensions_config.json`、`tabelrag.yaml` 等本地真实配置或 DSN。
- 不要提交生成物、缓存、日志、虚拟环境目录。
- 禁止使用 `codex/*` 分支名。
- `main` 如需强制保持和 `upstream/main` 一致，必须先确认自己的二次开发代码已经在 `dev` 中保留。
