# VS Code Gateway 调试解释器修复 Review

## 根因

VS Code Python Debugger 日志显示：

- `launch.json -> python` 已读取为 `backend\.venv\Scripts\python.exe`。
- 工作区当前活动解释器仍是系统 Python 3.11。
- `debugAdapterPython` 和 `debugLauncherPython` 未设置时，Python Debugger
  2026.6.0 将两者解析为活动解释器，最终调试命令以系统 Python 3.11 开头。

## 变更内容

- 在 `.vscode/launch.json` 中同时固定：
  - `python`
  - `debugAdapterPython`
  - `debugLauncherPython`
- 新增 `.vscode/settings.json`，将工作区默认解释器设置为
  `backend\.venv\Scripts\python.exe`。
- 更新 `.gitignore`，允许共享 `.vscode/settings.json`。
- 更新 Windows 本地启动手册，补充重新加载 VS Code 窗口的排错步骤。
- 修复 Skill Review 对已删除 `skillscan.orchestrator` 的直接导入，改为使用
  `deerflow.skills.skillscan` 包入口提供的保守扫描实现，避免 Gateway 导入失败。
- 新增回归测试，锁定 Skill Review 使用包入口的约束。

## 验证记录

- 已通过：`launch.json` 与 `settings.json` JSON 解析。
- 已通过：三个调试 Python 字段和默认解释器均解析到
  `backend\.venv\Scripts\python.exe`。
- 已通过：项目虚拟环境加载 VS Code Bundled Debugpy 1.8.20。
- 已通过：在项目虚拟环境中通过 Debugpy 启动 Uvicorn，访问
  `http://127.0.0.1:8001/health` 返回
  `{"status":"healthy","service":"deer-flow-gateway"}`。
- 已通过：
  `uv run pytest tests/test_skill_review_core.py::test_skill_review_uses_package_level_skillscan_fallback -q`。
- 已通过：
  `uv run ruff check packages/harness/deerflow/skills/review/analyzer.py tests/test_skill_review_core.py`。
- 已通过：`git diff --check` 和变更文件 UTF-8 校验。

## 已知测试基线

完整运行 `tests/test_skill_review_core.py` 时有 5 个旧断言失败。这些断言仍期待
已经被当前分支删除的原始 SkillScan 规则实现，而当前 Windows 基线使用
`scanner-quarantine-policy` 保守阻断实现。该差异不影响 Gateway 启动和本次
VS Code 调试链修复；本次没有恢复可能再次被终端安全软件隔离的原始实现。
