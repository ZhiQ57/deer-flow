# Linux Docker 源码映射部署 Review

审查日期：2026-08-27

## 1. 结论

已补充 Linux 服务器的源码映射部署方案：

- `docker/docker-compose-source-host.yaml` 只启动 `gateway` 和 `frontend`。
- PostgreSQL、Redis、Nginx 继续使用 `linux-local-start.md` 创建的现有容器。
- 前后端使用 `network_mode: host`，复用已有中间件的宿主机端口和现有 Nginx 的 `host.docker.internal` upstream。
- `backend/`、`frontend/` 使用 bind mount 映射服务器源码。
- Python 虚拟环境、uv 缓存、Node 依赖和 Next.js 缓存使用命名卷，避免源码挂载覆盖依赖。
- 文档明确了首次启动、自动重启、代码更新、日志、故障排查和 DooD sandbox 注意事项。

## 2. 变更文件

- `docker/docker-compose-source-host.yaml`
- `docs/guide/linux-docker-source-deploy.md`
- `docs/plans/2026-08-27-linux-docker-local-code-deployment.md`
- `docs/reviews/2026-08-27-linux-docker-local-code-deployment.md`

## 3. 验证结果

已执行：

```text
docker compose --env-file .env -p deer-flow-source \
  -f docker/docker-compose-source-host.yaml config --quiet
```

结果：通过。

已执行带 DooD overlay 的解析检查：

```text
docker compose --env-file .env -p deer-flow-source \
  -f docker/docker-compose-source-host.yaml \
  -f docker/docker-compose.dood.yaml config --quiet
```

结果：通过。

已执行：

```text
git diff --check
```

结果：通过。

## 4. 需要部署时确认的事项

1. 服务器必须是 Linux Docker Engine；`network_mode: host` 不适合作为 Windows、macOS Docker Desktop 的通用方案。
2. 切换前必须停止宿主机上的旧 uvicorn 和 Next.js 进程，否则 `8001` 或 `3000` 会端口冲突。
3. 现有 Nginx 容器必须配置 `host.docker.internal:8001`、`host.docker.internal:3000`，并带有 `host-gateway` 映射。
4. `config.yaml` 使用 AioSandboxProvider 纯 DooD 模式时，必须显式追加 `docker-compose.dood.yaml` 并配置宿主机绝对路径 `DEER_FLOW_HOST_BASE_DIR`。
5. `docker compose update` 不是有效的 Compose 命令，代码更新后应执行 `git pull --ff-only` 和 `docker compose up -d --build --force-recreate --remove-orphans`。
6. 本方案使用开发镜像和 reload，适合源码频繁更新的开发/联调环境；生产环境应使用仓库已有的生产 Compose 方案。
