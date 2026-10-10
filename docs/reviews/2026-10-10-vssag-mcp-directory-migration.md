# VSSAG MCP 聚合目录迁移评审

## 1、适配内容

- 当前服务目录改为 `mcp-extensions/vssag/`，捆绑 SDK 在其 `extension/` 下；保留发行名 deerflow-vssag-mcp、Python 导入名 vssag_mcp_server、Middleware 和 interceptor 入口。
- 更新测试源路径、开发指引清单、根配置示例、README、聚合目录 README、当前 API 与指南；历史计划/评审保留当时路径，不作为现行部署依据。
- 本机根 .env 的两个 VSSAG 路径已更新；该真实凭据文件不提交。
- 新目录原先没有服务虚拟环境，Gateway 可编辑安装指向旧目录；已在新位置安装独立服务环境，并无依赖重装 Gateway 的轻量基础包。
- stdio 服务由 DeerFlow 按需自动启动，不新增 HTTP、Sandbox 实体挂载或 Bash 拦截，不改 Harness 运行代码。
- 同步根与服务目录的默认禁用示例，保留中文服务说明，补齐三项接入说明；新增现行入口不再引用旧目录的回归测试。

## 2、验证

- 迁移前回归复现旧 plugin.json 路径 FileNotFoundError；迁移后 VSSAG 定向测试 68 passed。
- 严格非阻塞定向测试 2 passed；配置写入、MCP 自定义 interceptor 与 context headers 回归 84 passed、3 skipped。
- 实际根 .env/JSON 解析得到新服务解释器和新 SDK YAML；Gateway 中间件从新目录导入，真实 MCP 发现三个 VSSAG 工具；实际启动及匿名拒绝测试 1 passed。
- 按 backend/ruff.toml 校验外置封装与相关测试，Ruff lint、12 个文件格式检查通过；不格式化捆绑 SDK。
- SDK 的 188 个非缓存文件迁移后逐文件 SHA-256 与本轮开始一致；无 SDK 源码或配置接口修改。
- 独立环境/协议通过不等于生产文档问答通过；本轮未新增业务数据，有命中文档问答仍需 SDK 文档索引及 owner/ACL。

## 3、已有仓库问题

- 开发指引回归 11 passed、1 failed：backend/AGENTS.md 在 HEAD 已有 292182 字节，超过既有 32768 字节预算；本次没有调整预算或改造整份历史指南。
- 全量离线回归使用 maxfail=5：2022 passed、39 skipped、3 deselected、5 failed 后停止。失败分别为已有 DataAgent SOUL 查询规则不匹配、指南预算超限、Windows 目录权限模式、Windows 执行位语义及 Windows 符号链接权限；未修改这些模块或放宽测试。
- 全量严格非阻塞套件使用 maxfail=3：36 passed、3 failed 后停止，三个失败均为既有 channel runtime config 在 Windows 下无法得到 POSIX 0600 权限模式；VSSAG 的两个非阻塞测试已单独通过。
- SDK 自带的 extension/vssag/AGENTS.md 在 EOF 有额外空行，暂存 diff --check 对此报警；为遵守 SDK 只读约束不修剪。排除 SDK 的暂存差异检查通过。

## 4、提交与后续启动

- 仅提交该功能的公开源码、捆绑 SDK 公共文件、标准插件资源、测试和关联文档；排除真实 dotenv、配置/备份、日志/截图、虚拟环境、缓存及无关测试删除。
- 229 个暂存文件已扫描本机已知凭据、私钥及常见 API Key 模式，无发现；暂存清单确认不含真实配置、日志、缓存或无关测试删除。
- 当前运行的 Gateway 仍持有启动时环境变量。用户下次按原方式重启 Gateway 后才会读取新路径，不能只刷新浏览器。无需手工启动 MCP。
- 提交当前本地 feat/knowledge-virtual-directory 分支，不合并 dev、不推送远端。
