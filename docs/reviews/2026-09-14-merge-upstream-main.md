# upstream/main 合并复核

## 结论

已在 `dev` 分支完成 `main` 合并冲突处理，当前保留官方实现为主，并保留本地 DataAgent SQL 定制能力。合并提交尚未创建，由维护者自行提交。

## 本地功能保留

- DataAgent SQL 手动执行 Gateway 路由。
- 外部 SQL MCP 配置与对应工具装配。
- `allowed_subagents` 与历史 `allowable_subagents` 配置兼容，避免现有 DataAgent 配置立即失效。
- Docker 本地日志输出、Gateway reload 开关及 SQL MCP 服务依赖。
- 前端 SQL 执行相关消息展示与交互。

## 官方实现采用

- Gateway readiness/健康检查与生产启动等待机制。
- MCP durable tasks、task receipts、task continuity。
- Subagent batches、managed subagents 与新的权限策略。
- 项目工作区、线程归档/删除、用户偏好跨浏览器同步等前端与后端能力。
- 官方 SkillScan 扫描器及其审查豁免契约。

## 验证结果

- 后端定向测试：176 passed。
- 前端定向测试：80 passed。
- `pnpm check`：通过。
- 关键后端文件 `ruff check`、Python 编译检查：通过。
- JSON/YAML 语法检查：通过。
- Git 未发现未合并路径或冲突标记。

## 注意事项

`backend/tests/test_skillscan_native.py` 曾被 Windows 安全软件自动隔离，索引中的官方文件仍然保留；恢复该文件后若再次被隔离，不属于合并逻辑问题，应将仓库目录加入安全软件白名单后重跑完整 SkillScan 测试。

后续建议将 DataAgent 配置从 `allowable_subagents` 迁移到官方 `allowed_subagents`，完成迁移后删除兼容分支，避免长期维护两套字段。
