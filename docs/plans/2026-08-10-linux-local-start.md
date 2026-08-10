# Linux 本地手动启动手册计划

1. 文档结构与现有手册对齐
   - [X] 1.1 阅读 Windows 本地启动手册，确认服务拓扑、端口和验证方式。
   - [X] 1.2 阅读 nginx 本地配置和前端启动脚本，确认 Linux 下需要调整的命令。

2. 编写 Linux 手册
   - [X] 2.1 说明 Linux 本地部署目标：宿主机前后端、Docker 中间件、局域网访问。
   - [X] 2.2 补充前置条件、端口检查、依赖安装、Redis、Gateway、Frontend、Nginx、验证、停止和常见问题。
   - [X] 2.3 明确 `0.0.0.0` 监听与局域网 IP 访问、Linux Docker 访问宿主机的 `host.docker.internal` 配置。

3. 验证变更
   - [X] 3.1 检查 Markdown 文件内容和命令片段是否完整。
   - [X] 3.2 查看 git 状态，确认本次新增 `docs/guide/linux-local-start.md` 和本计划文件；工作区仍保留原有未提交文档变动。

4. 补充 DeerFlow PostgreSQL 数据库
   - [X] 4.1 阅读配置示例，确认 DeerFlow PostgreSQL 连接方式。
   - [X] 4.2 在 Linux 手册中补充 PostgreSQL Docker 启动、验证和 `config.yaml` 配置。
   - [X] 4.3 同步调整 Gateway 环境变量、端口检查、运行状态、停止服务和常见问题。
   - [X] 4.4 复查文档差异和 git 状态。
