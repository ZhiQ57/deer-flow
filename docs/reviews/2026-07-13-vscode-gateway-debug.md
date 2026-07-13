# VS Code Gateway 调试配置 Review

## 变更内容

- 新增 `.vscode/launch.json`，提供 `DeerFlow: 调试 Gateway` 配置。
- 调试器直接使用 `backend\.venv\Scripts\python.exe` 启动 `uvicorn` 模块。
- 工作目录、Gateway 入口、监听地址、端口和环境变量与
  `docs/guide/local-manual-start.md` 的 PowerShell 启动方式一致。
- 未配置前端、Redis、Nginx 或其他 Docker 服务的自动启动。
- 调整 `.gitignore`，只允许共享 `.vscode/launch.json`，其他 `.vscode` 本地配置继续忽略。
- 在本地手动启动手册中补充 VS Code 断点调试步骤。

## Review 结论

- `type: debugpy` 与本机已安装的 VS Code Python Debugger 配置架构一致。
- `python`、`cwd`、`config.yaml` 和 `extensions_config.json` 引用路径均可解析。
- `justMyCode: false` 允许进入 DeerFlow 源码及必要的依赖调用链。
- 未启用 `--reload`，避免 Uvicorn 热重载子进程降低断点稳定性。
- 配置不修改公共 API、目录边界或运行时业务逻辑，无需更新
  `docs/guide/used-api.md`。

## 验证记录

- 已通过：PowerShell `ConvertFrom-Json` 解析 `.vscode/launch.json`。
- 已通过：校验全部九个手动启动环境变量均存在。
- 已通过：`backend\.venv\Scripts\python.exe`、后端工作目录及两份运行时配置文件存在。
- 已通过：`backend\.venv\Scripts\python.exe -m uvicorn --help`。
- 已通过：`.vscode/launch.json` 可被 Git 跟踪，`.vscode/settings.json` 仍被忽略。
- 启动冒烟未完成健康检查：当前工作区中的
  `backend\packages\harness\deerflow\skills\skillscan\orchestrator.py`
  缺失，Gateway 导入报
  `ModuleNotFoundError: No module named 'deerflow.skills.skillscan.orchestrator'`。
  该文件属于本次需求开始前已经存在的其他未提交开发改动范围，本次未恢复或覆盖。
