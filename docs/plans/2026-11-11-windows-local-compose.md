# Windows 本地开发容器编排计划（2026-11-11）

## 目标

在 Docker Desktop 中以 Compose 项目 `deerflow-dev` 管理 PostgreSQL、Redis、Nginx 三个容器；Windows 本地运行 Gateway 和前端。宿主机端口分别为 PostgreSQL 7432、Redis 7379、Nginx 2026、Gateway 7001、前端 7000。Compose 项目名只能使用小写字母，因此采用 `deerflow-dev` 作为项目名。

## 步骤

1. - [X] 清点并保护现有数据
   1.1 - [X] 确认 PostgreSQL 真正使用的数据卷、版本、数据库和迁移前状态。
   1.2 - [X] 检查五个目标端口的监听、系统排除范围和临时绑定；现有 2026 占用来自待迁移的 Nginx 容器。
2. - [X] 实现 Windows 开发编排
   2.1 - [X] 从本地最新 `dev` 创建 `feat/windows-desktop-compose` 分支。
   2.2 - [X] 新增三个服务的 Compose 配置，使用持久化卷和仅本机开放的端口。
   2.3 - [X] 更新本地 Gateway/前端连接配置、VS Code 启动配置和 Nginx 代理目标。
3. - [X] 更新文档
   3.1 - [X] 更新 `docs/guide` 中的 Windows 本地启动说明及相关配置指引。
   3.2 - [X] 同步 README、AGENTS 开发指引和验证记录。
4. - [X] 迁移、启动与验收
   4.1 - [X] 在停止 PostgreSQL 写入后保留原卷并迁移至可读名称的持久卷；新旧卷离线校验一致，属主 999:999、权限 700；启动后集群标识、业务表数和数据库数与迁移前一致。
   4.2 - [X] 启动 Compose 项目及本地 Gateway、前端，检查数据库、Redis、Nginx、API 与页面。
   4.3 - [X] 审核改动、运行必要测试并记录结果。

## 验收记录

- Compose 项目 `deerflow-dev` 正常启动三个容器，PostgreSQL 与 Redis 健康；宿主机仅回环监听 `7432`、`7379`、`2026`。
- 迁移后 PostgreSQL 集群标识 `7668226481324228646`、`deerflow` 业务表 27 张、可连接数据库 3 个，与迁移前一致；原匿名卷与 `postgres_data` 均保留。
- Windows 侧 TCP 可达全部五个端口；从宿主机向 Redis 发送 `PING` 返回 `+PONG`。
- `http://127.0.0.1:7001/health/ready` 与 `http://127.0.0.1:2026/health/ready` 均返回 `database: ok`、`checkpointer: ok`；`7000` 与 `2026` 首页均返回 200。
- Gateway 启动日志未出现 Redis 连接错误；Nginx 生成配置通过 `nginx -t`。
- `backend/tests/test_windows_local_compose.py` 与既有 Compose 绑定地址测试共 9 项通过；`ruff format --check`、`ruff check`、`git diff --check` 通过。

现有 `backend/evaluates/` 和 `evals/` 在本分支显示为未跟踪内容，属于切换分支后显露的既有工作区文件，本需求不修改或提交它们。
