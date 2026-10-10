# VSSAG 独立启动指南调整

> 历史调整记录。用户随后选择 stdio 自动启动，当前目录与操作步骤以 [迁移计划](2026-10-10-vssag-mcp-directory-migration.md) 和 [接入指南](../guide/vssag-mcp.md) 为准。

## 1、核对启动入口
- [X] 1.1 核对当前服务仅支持私有 stdio，--config 必须为绝对路径。
- [X] 1.2 验证 uv 的跨平台运行、dotenv 参数和服务帮助入口。

## 2、重写操作步骤
- [X] 2.1 指南只保留独立 MCP 的安装、配置、启动和停止，不启动 DeerFlow 或其他服务。
- [X] 2.2 使用 Windows/Linux 通用简单指令，说明手工 stdio 进程不能被 DeerFlow 通过 URL 连接。

## 3、检查与记录
- [X] 3.1 文档 uv 命令在 Windows 完成独立启动、MCP 初始化和三个工具发现；SDK 未修改，指南格式与 git diff --check 通过。
- [X] 3.2 README/API 文档引用已同步；Review 记录私有 stdio 与独立网络部署的区别及 Linux 未实机测试的范围。
