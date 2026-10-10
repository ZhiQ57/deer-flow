# VSSAG 独立启动文档评审

> 历史调整记录。用户随后选择 stdio 自动启动，当前指南已改为 MCP 聚合目录的接入步骤；本页描述的是当时的独立调试文档。

## 1、文档调整

- `docs/guide/vssag-mcp.md` 缩减为 52 行，只保留进入目录、安装依赖、服务配置、独立启动、停止和重启。
- 指令统一使用 cd 和 uv，不使用 PowerShell 变量、函数、复杂代码或联合启动脚本；Windows/Linux 只需替换 SDK 配置绝对路径。
- 凭据统一在服务目录的 `.env.dev` 配置，通过 uv 的 `--env-file` 加载，不要求读取 DeerFlow 根 dotenv。
- README 与 used-api 同步修正文档用途，不再把指南描述为 Windows 联合启动或网页验收教程。

## 2、验证

- 使用文档中的 uv 参数启动独立 MCP 子进程，完成 MCP 初始化，发现 build_vfs、search、grep_file 三个工具。
- 验证只启动 MCP，未启动、停止或重启 DeerFlow、数据库、网页等服务；不调用业务检索，不写业务数据。
- 测试在当前 Windows 环境运行，沿用已存在的宿主凭据；未复制真实凭据，未修改 SDK 捆绑目录。Linux 指令使用同一 uv/Python 入口，但未进行 Linux 实机测试。
- 自动检查指南无联合启动脚本或 PowerShell 专属指令；git diff --check 通过。

## 3、必须说明的边界

- 当前服务仅支持私有 stdio；手工启动可以用于独立调试，不提供 HTTP 端口，DeerFlow 不能连接或复用这个已启动进程。
- 用户要求的“独立常驻 MCP + DeerFlow 后连接”尚需开发 HTTP 传输及可信身份认证，不是重写文档或调整 URL 就能实现。本轮不擅自新增网络入口或放宽身份信任。
