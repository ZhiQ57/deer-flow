# VSSAG MCP-only 接入计划

> 历史阶段记录：同日后续用户要求自动目录预加载与 Codex 标准插件布局。当前开发以 `2026-10-09-vssag-plugin-middleware.md` 为准；下文“不再动态注入”仅描述前一阶段，不是现行限制。Bash 不拦截的边界继续有效。

本计划替代知识库 Bash 拦截方案。检索通过标准 MCP tools/call；Bash、文件写入和 Sandbox 保持 DeerFlow 默认实现。现有工作区位于本需求分支 `feat/knowledge-virtual-directory`，继续在该分支实现，不提交、合并或覆盖其他未提交工作。

## 1、确定最小接入契约
- [X] 1.1 核对已有 MCP 工具发现、认证拦截器及 SDK 接口。
- [X] 1.2 取消知识库 Bash 拦截与混合命令执行；目录由 MCP 工具按需返回，不再动态注入。
- [X] 1.3 明确可信身份传递、SDK 服务启动和只读检索工具契约；服务限私有 stdio。

## 2、先测试，再实现外置接入
- [X] 2.1 增加工具发现、检索、权限隔离、并行反馈和普通 Bash 不变的回归测试。
- [X] 2.2 提供可启动的 VSSAG MCP 服务及必要的外置上下文传递，不向 Harness 新增业务逻辑。
- [X] 2.3 提供默认禁用的配置与操作指引，保留已有服务和真实凭据不变。
- [X] 2.4 清理本需求旧知识库 Harness 模块及专属注入代码、测试和配置。

## 3、验证与交付
- [X] 3.1 验证真实 MCP 协议、配置加载和 stdio 会话池；真实 SDK 内存索引及原生写文件闭环通过，外部模型以确定性测试模型替代。
- [X] 3.2 运行相关默认测试、blocking-I/O 测试和 Ruff，记录无关/环境失败；全仓在 5 个失败后停止，不宣称全绿。
- [X] 3.3 同步 API 指引、原计划、Review 和 README，说明部署前仍需配置的数据源和权限。

## 验证记录

- 相关专项集合 177 passed；随后新增的真实配置→stdio pool 单项 1 passed。
- Ruff check 与 format --check 通过（5 个 Python 文件）。
- 广泛 MCP/文件/清洗回归：384 passed、3 failed；默认全仓限失败运行：2022 passed、37 skipped、3 deselected、5 failed；全 blocking-I/O 限失败运行：70 passed、5 failed。
- 失败明细及未完成的生产验收见 [Review](../reviews/2026-10-09-vssag-mcp-only.md)。本 Todo 完成表示离线 MVP 开发/核验完成，不表示真实服务已启用。

## 明确不做

- 不改 Sandbox provider、Bash middleware、文件工具或 Agent builder。
- 不引入混合文件系统解释器，不自动将检索结果写入工作区。
- 不把模型提供的 user_id、user_role 或授权属性当作权限来源。
- 不静默启用真实服务，不读取或展示真实凭据，不声称已完成线上数据源验收。

## 4、SDK 迁移与服务目录独立化（2026-10-09）

用户已将 SDK 移到 `examples/deerflow-vssag-mcp/extension/`，服务 Python 包改名为 `vssag_mcp_server`。保留用户的迁移结果，不恢复旧目录或旧导入兼容层。

- [X] 4.1 核对新目录、打包入口及旧引用，确认无须修改 Harness。
- [X] 4.2 先更新迁移回归测试（2 项红），再修复包名、CLI、MCP 配置和 SDK 本地依赖来源。
- [X] 4.3 服务使用自身 Python 3.11.10 虚拟环境及 uv.lock；Gateway 基础包不依赖 SDK。
- [X] 4.4 同步部署、API、SDK 目录指引及 Review，明确 stdio 不等同于 HTTP 常驻服务。
- [X] 4.5 独立 Python 3.11.10 stdio 启动与真实协议冒烟通过；迁移及相关回归合并运行 98 passed，Ruff check/format --check 通过（6 个 Python 文件）。未重新跑全仓测试，先前无关失败仍按 Review 记录。
