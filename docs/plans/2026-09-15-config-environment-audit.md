# 配置分环境与启动链路检查计划

- [X] 1. 盘点 Windows 本地、Linux 本地和 Linux Docker 源码映射启动链路
  - [X] 1.1 检查 `.env`、三份阶段配置、`extensions_config.json`、`tablerag.yaml`
  - [X] 1.2 检查 Compose 文件和启动脚本对配置文件路径的假设
- [X] 2. 修正影响 Windows 本地启动的配置遗漏
  - [X] 2.1 统一 Windows 阶段配置中的本地 sandbox、数据库、Redis、前端入口
  - [X] 2.2 修正 MCP 与 TableRAG 的跨平台路径和变量引用
- [X] 3. 同步 Linux Docker 源码映射部署
  - [X] 3.1 让源码映射 Compose 使用显式的阶段配置路径
  - [X] 3.2 更新部署手册中的配置文件、`.env` 和验证命令
- [X] 4. 验证配置可解析、路径存在、端口与关键启动前置条件满足
  - [X] 4.1 验证三个阶段 YAML、extensions JSON、TableRAG 路径
  - [X] 4.2 验证 Compose 插值和 Linux 源码映射配置
  - [X] 4.3 使用 `.env` 启动 Windows Gateway，确认 PostgreSQL/Redis 初始化和 `Application startup complete`
