# SQL Run 上下文生命周期修复 Review

## 结论

修复解决了 `start_run()` 在 `RunRecord` 创建前访问 `record.run_id` 导致的 500，并恢复 SQL Execution 运行时能力的完整释放路径。Review 未发现阻塞合并的问题。

## 实现检查

- SQL 数据源绑定和 Secret 分离在持久化准入前异步完成，不依赖尚未生成的 `run_id`。
- Run 准入成功后只执行同步注册，准入与任务挂载之间没有新增 `await`。
- 任务完成回调覆盖成功、异常和取消，包括协程第一次执行前即被取消的情况。
- 任务挂载失败路径会立即释放已注册能力，并沿用 `fail_start_if_pending()` 关闭待运行记录。
- Harness 运行上下文只接收无密钥绑定；数据库 Secret 副本仍只保存在 Gateway 运行时注册表。

## 测试记录

- TDD 红灯：新增生命周期测试在旧实现上复现 `UnboundLocalError`。
- `test_gateway_services.py` 与 `test_gateway_sql_run_context.py`：128 passed。
- `test_runtime_lifecycle_e2e.py`：8 passed。测试进程显式清空本地 `.env` 的 Redis Stream Bridge 覆盖，以使用测试配置的进程内桥。
- Ruff check：通过。
- Ruff format check：通过。
- `git diff --check`：通过。

## 环境限制

全量离线测试首次收集时发现工作区中的已跟踪文件 `backend/tests/test_skillscan_native.py` 被外部删除；再次执行剩余测试超过 10 分钟后由命令超时终止。该删除和超时均不属于本修复，未纳入提交。定向 Gateway、SQL Execution 和真实运行生命周期回归均已通过。
