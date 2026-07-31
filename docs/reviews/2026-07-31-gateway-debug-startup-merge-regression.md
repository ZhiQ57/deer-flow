# Gateway 调试启动合并回归 Review

## 1. 结论

- [X] 首次 `KeyboardInterrupt` 是 debugpy 初始化期间的 Windows WMI 短暂阻塞，不是 Gateway、前端或智能体异常。
- [X] 第二次 `IndentationError` 来自合并提交 `00223b1c` 对 `task_tool.py` 冲突块的错误拼接。
- [X] 同一合并还在当前正式运行路径重新带回了 Harness 本地 SQL 校验和 SQL content 回退。
- [X] VS Code 调试配置传入了 Uvicorn 不支持的 `asyncio:SelectorEventLoop`。
- [X] 修复后 Harness 只验证 Gateway artifact，不调用 `SqlExecutionService`，SQL SubAgent 仍通过 Gateway 路由获得结果。

## 2. 后端修复

- 删除 `task_tool.py` 重复的 `if`，恢复 Python 语法。
- 删除 `SqlStageMiddleware` 对已移除 `_sql_executor` 的调用，并恢复 database type、binding fingerprint 与 Gateway artifact 校验。
- 删除 `SqlStageMiddleware` 和 `task_tool` 的普通 content JSON 回退。
- 恢复正式根级 `service_agent/config.py` 的数据库方言归一化、Secret 引用格式、只读约束、布尔数值预算拒绝和安全错误日志。
- 恢复合并误删的 `backend/tests/test_skillscan_native.py`，内容哈希与 `356d4145` 完全一致。
- `service_agent/data_agent/` 是用户正在重构的代码，已按 `HEAD` 原样恢复并排除在本次修复范围之外。

## 3. 前端检查

- 前端未参与本次异常链路，不需要代码修改。
- 手动 SQL Execution API 和结果面板合同未改变。

## 4. 智能体检查

- SQL SubAgent 的 task 结果必须提供 Gateway ToolMessage artifact。
- 普通 task content 即使包含结构完整的 SQL JSON，也会返回 `SQL_SUBAGENT_CONTRACT_INVALID`。
- 新审批/历史恢复流程的测试工具改为 `content_and_artifact`，与生产 task 工具合同一致。

## 5. 调试配置

- `.vscode/launch.json` 删除无效的 `--loop asyncio:SelectorEventLoop`。
- `backend/sitecustomize.py` 继续负责 Windows Selector event-loop policy。
- Gateway 模块导入验证通过；当前 `config.yaml` 版本为 20，运行时提示最新版本为 29，但该提示不阻断启动。

## 6. 验证记录

- Harness/Gateway Python 语法编译：通过。
- SQL/DataAgent/Gateway 扩大定向回归：`365 passed, 2 skipped, 10 deselected`。
- `test_task_tool_core_logic.py` 与 `test_data_agent_query_flow.py`：`96 passed`。
- `test_data_agent_service_ability.py`：`27 passed`。
- VS Code Gateway loop 配置回归测试：通过。
- 变更范围 Ruff check/format：通过。
- VS Code debugpy bundled import、Python `platform.platform()` 和 WMI 查询均已恢复正常。
- 扩大回归 deselect 的 8 个 Skill 安装用例受本机 SkillScan 隔离策略阻断；另外 2 个既有 deselect 与本次修改无关。
- `test_dev_entrypoint.py` 的其余 POSIX shell 用例在本机 Windows 环境因未安装 `sh` 无法执行，与本次 Gateway 配置测试无关。

## 7. Review 结果

当前未发现阻断 Gateway 调试启动的剩余 Python 语法或 SQL Execution 边界问题。修复已通过 fast-forward 合并回本地 `dev`；SkillScan 测试文件在合并前后均通过哈希校验，`service_agent/data_agent/` 用户重构目录保持原样。
