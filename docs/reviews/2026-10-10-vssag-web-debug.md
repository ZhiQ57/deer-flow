# VSSAG 网页工具缺失：排查与验收

## 1、根因

- 用户提供的日志确认：VSSAG 在工具发现期间 `Connection closed`，DeerFlow 跳过该服务并缓存 0 个 MCP 工具。
- 同一份真实配置启动 MCP 子进程，捕获并脱敏 stderr，最终异常为 SDK YAML 引用的 `DASHSCOPE_API_KEY` 未定义。
- 两个 DashScope 变量存在于宿主环境，但不存在于服务 `.env.dev`，也没有在 MCP `env` 中转发。stdio 仅继承 SDK 允许的基础环境，不继承所有宿主变量。
- `assembly_0` 无事件循环由现有 `asyncio.run()` 分支兜底；Selector 和 Proactor 均复现连接关闭，不修改事件循环或 Harness。

## 2、最小修复

- 实际根 `extensions_config.json -> mcpServers.vssag.env` 增加 `DASHSCOPE_API_KEY: $DASHSCOPE_API_KEY` 和 `DASHSCOPE_BASE_URL: $DASHSCOPE_BASE_URL`，只引用已有变量，不复制或回显真实凭据。
- 当前运行的 Gateway 在下一轮请求检测到 JSON 修改，重新发现工具；无需为这次 JSON 修改重启网页或 Gateway。
- 真实启动测试改为读取实际 Gateway 扩展配置，不在测试中偷偷额外补传凭据，避免与网页启动条件不一致。
- SDK 捆绑代码、Sandbox、Bash 和 Harness 均未修改。

## 3、验收结果

- 独立 MCP 初始化和 `tools/list` 成功，返回 `build_vfs`、`search`、`grep_file`。
- 真实 DeerFlow 工具加载返回 `vssag_build_vfs`、`vssag_search`、`vssag_grep_file`。
- 实际配置启动及匿名拒绝测试：1 passed；变更测试文件 Ruff 检查、格式检查通过。
- 已打开会话的真实网页请求成功：模型先调用 `tool_search`，随后调用 `vssag_build_vfs` 与 `vssag_search`，再调用 `vssag_grep_file`；运行状态为 success，数据库事件记录三项 VSSAG 工具反馈均为 success。
- 同一运行的四次模型调用前，`VssagDirectoryMiddleware` 均留下 `injected` 审计记录；目录不是靠模型自述确认。
- 目录为 `/home/mnt/knowledge/vssag/` 下仅有 `index.md`；keyword 检索“知识库”返回 0 条，索引入口正文仅有 `# Knowledge Base`。
- 只读查询 SDK 配置对应数据库：`public.vssag_index_records` 存在，但记录数为 0，pgvector 扩展存在。不能据此宣称其他数据源、SQL 索引或所有用户知识库都没有数据。

## 4、限制和已有测试问题

- 接入故障已修复；有命中文档的问答尚未验收。需在 SDK 当前文档索引中导入测试文档，并让其 owner/ACL 匹配网页登录身份。本轮不写业务数据、不伪造身份或扩大授权。
- 正常轻量 Gateway 环境的相关离线回归：46 passed、1 skipped、1 failed。失败是两份已有示例的 description 文案不同，不是本次环境变量修复引入。
- 显式把捆绑 SDK 加到测试路径后：61 passed、6 failed；其中 5 项因 Gateway 测试环境缺少 SDK 的 `json_repair` 依赖失败，另 1 项仍为 description 差异。严格 blocking-I/O 测试也因该依赖无法收集；未给 Gateway 安装整套 SDK 来掩盖分离环境边界。
- 未运行整个 backend 离线套件，不宣称所有测试通过。
- SDK 的 PgVectorIndexStore 仍存在异常时退回内存的行为；本轮用直接只读数据库计数独立核验空结果，不把工具 success 或空目录当作生产检索成功。
