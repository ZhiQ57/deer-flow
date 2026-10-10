# VSSAG 插件开发边界

- 当前目录为 `mcp-extensions/vssag/`，归属仓库 MCP 聚合目录；目录迁移不改变发行名 `deerflow-vssag-mcp`、导入名 `vssag_mcp_server` 或既有 Middleware 类路径。
- 按 `https://developers.openai.com/plugins/build/plugins` 的 portable plugin 规范维护根 `plugin.json`、`mcp.json`、`skills/`；OpenAI 专属展示和 hooks 配置放在 `extensions.com.openai`。
- `.codex-plugin/plugin.json` 与 `.mcp.json` 是用户提供的可选 Codex 兼容资源；变更时与 portable 文件保持一致，不作为 DeerFlow 注册入口。
- `middlewares/` 是 DeerFlow 专属 Python 源码，通过既有 `extensions_config.json -> middlewares` 加载；不得假称 Codex 会自动发现或执行该目录。
- 不修改 Harness、Sandbox 或 Bash。知识库读取只走 MCP，结果保存另行调用正常文件工具。Hook 不重写或执行模型 Shell。
- 目录由外置 Middleware 在每次模型请求前获取并作为临时数据上下文注入，与原有 Sandbox 目录说明共同可见；不得合成模型 tool call、写入对话历史或冒充真实挂载。宿主目录查询须显式校验权限并审计，不能假称自动经过 ToolNode；SDK 留在 MCP 服务端，不从全局工具缓存绕过权限取工具。
- 身份仅来自可信 DeerFlow runtime；Codex 原生调用没有这套身份协议时拒绝，不默认匿名或管理员。hooks 不替代 MCP 身份认证。
- SDK、配置和私密 dotenv 留在服务侧；Gateway 只安装轻量基础包。真实配置不提交，不打印秘密或正文，不以 pgvector 静默内存降级冒充真实检索成功。
- `extension/` 是其他项目维护的 SDK 捆绑目录，只能读取和调用，禁止修改（包括格式化、依赖锁与测试修补）；接口缺口记录修改目标交由用户处理。
- 文档统一写在仓库 `docs/`，测试在 `backend/tests/test_vssag*.py`。修改公共入口必须更新 API、plan、review。
- 根 `extensions_config.example.json` 的默认禁用服务须与本目录示例一致；根列表保持不导入可选包，启用时按指南合并 Middleware、interceptor 和服务三项。示例的 `APP_ENV=dev` 对应可信 SDK dotenv。
- 迁移后重建服务环境并重新安装 Gateway 的轻量可编辑基础包，避免安装记录引用旧目录；Gateway 依赖同步不得移除该包。MCP 按需由 DeerFlow 自动启动，指南不混合启动其他服务。两份示例须通过真实池化 stdio 测试，不把离线闭环或匿名拒绝当作生产数据网页问答验收。
- 真实启动测试沿用 Gateway 的实际扩展配置，不额外补传宿主凭据；stdio 不继承完整宿主环境，凭据须在服务 dotenv 或显式配置的 env 中提供。
