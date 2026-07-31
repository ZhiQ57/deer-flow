# Gateway 调试启动合并回归

## 1. 问题定位

- [X] 1.1 确认首次 `KeyboardInterrupt` 发生在 debugpy 调用 Windows WMI 的阶段，Gateway 尚未导入。
- [X] 1.2 复测 `platform.platform()`、WMI 查询和 debugpy 导入，确认当前环境已恢复。
- [X] 1.3 定位第二次启动失败为 `00223b1c` 合并冲突错误：`task_tool.py` 重复保留 `if` 语句。
- [X] 1.4 确认合并同时重新带回了 SQL 工具 content 回退逻辑，违反 Gateway artifact 权威边界。
- [X] 1.5 确认 VS Code Gateway 调试参数使用了 Uvicorn 不支持的 `asyncio:SelectorEventLoop`。
- [X] 1.6 `service_agent/data_agent/` 是用户正在重构的代码，本次修复明确排除该目录。

## 2. 后端修复

- [X] 2.1 修复 `task_tool.py` 的语法错误。
- [X] 2.2 删除合并重新带回的 SQL content 回退，只信任 Gateway artifact。
- [X] 2.3 执行 Harness/Gateway Python 全量语法编译检查。
- [X] 2.4 执行 DataAgent 查询流和 task tool 定向测试。
- [X] 2.5 恢复合并时被误删的 `backend/tests/test_skillscan_native.py`，不运行会触发本机安全软件隔离的 SkillScan 测试。
- [X] 2.6 恢复正式根级 `service_agent/config.py` 的数据库类型、Secret 引用、只读和数值预算校验。

## 3. 前端检查

- [X] 3.1 确认异常发生在 Gateway Python 导入阶段，前端代码无需修改。

## 4. 智能体检查

- [X] 4.1 验证 SQL SubAgent 结果仍只通过 Gateway ToolMessage artifact 投影。
- [X] 4.2 验证普通工具 content 无法伪造 SQL 校验或执行结果。

## 5. 调试配置

- [X] 5.1 增加 VS Code Gateway 启动参数回归测试。
- [X] 5.2 删除无效的 Uvicorn `--loop asyncio:SelectorEventLoop` 参数。
- [X] 5.3 验证调试配置继续使用 `backend/.venv` 和现有 Windows Selector policy。

## 6. Review 与集成

- [X] 6.1 更新 README、`backend/AGENTS.md` 和 Review 记录。
- [X] 6.2 完成 `git diff --check`、变更范围 Ruff 检查和扩大定向回归。
- [X] 6.3 提交修复分支并通过 fast-forward 合并回本地 `dev`。
