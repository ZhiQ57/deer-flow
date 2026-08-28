# Linux Docker 映射本地代码部署计划

## 目标

补充一套适用于 Linux 服务器的部署方式：保留现有 PostgreSQL、Redis 和 Nginx 容器，将 DeerFlow 前端、Gateway 后端也交给 Docker Compose 自动启动，同时把服务器上的代码目录映射进容器，便于 `git pull` 后重启或重建服务。

## 执行清单

### 1. 梳理现有运行方式

- [X] 1.1 阅读 `docs/guide/linux-local-start.md`，确认现有中间件容器名称、端口和 Nginx 代理方式。
- [X] 1.2 阅读 `docker/docker-compose-dev.yaml`、Dockerfile 和启动脚本，确认已有的源码映射、依赖缓存和自动重启逻辑。
- [X] 1.3 确认现有 Nginx 容器与新增前后端容器之间的网络、配置和端口边界。

### 2. 编写 Docker Compose 映射部署方案

- [X] 2.1 增加只负责前端和 Gateway 的 Compose 文件，避免与现有 Nginx、PostgreSQL、Redis 容器抢占端口或重复启动。
- [X] 2.2 编写 `docs/guide` 部署手册，说明首次部署、现有容器复用、启动、更新、重启、日志和故障排查。
- [X] 2.3 明确 `docker compose update` 不是 Docker Compose 的有效命令，并给出代码更新后的正确命令。

### 3. 校验和 Review

- [X] 3.1 使用 Docker Compose 配置解析检查服务、卷、环境变量和网络定义。
- [X] 3.2 检查文档中的路径、容器名、端口、命令与仓库实际配置一致。
- [X] 3.3 编写 Review 记录，说明验证结果和仍需按服务器环境确认的事项。
- [X] 3.4 修复源码映射 Gateway 的 PostgreSQL extra、日志输出和 reload 启动问题。
- [X] 3.5 文档统一使用完整 `docker compose` 命令，不使用 `dc` 函数。
