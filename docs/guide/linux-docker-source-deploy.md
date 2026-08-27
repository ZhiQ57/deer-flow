# DeerFlow Linux Docker 源码映射部署手册

本文适用于下面的部署目标：

- 服务器上保留 `docs/guide/linux-local-start.md` 已经创建的 PostgreSQL、Redis 和 Nginx 容器。
- 前端和 Gateway 后端改为由 Docker Compose 启动。
- 前端、后端使用服务器上的 Git 工作区作为源码挂载目录。
- Docker 容器设置为 `unless-stopped`，服务器重启或 Docker 服务重启后自动拉起。
- 代码更新后执行一次 Compose 重建/重启，不需要再手动打开两个终端启动前后端。

本方案使用 Linux 的 `network_mode: host`，因此只适用于 Linux Docker Engine。它的目的，是让新启动的前端和 Gateway 容器直接复用已有中间件的宿主机端口：

```text
现有 PostgreSQL  127.0.0.1:55432
现有 Redis       127.0.0.1:6379
现有 Nginx       Docker 容器，对外提供 0.0.0.0:2026
新 Gateway       Docker 容器，host network，监听 0.0.0.0:8001
新 Frontend      Docker 容器，host network，监听 0.0.0.0:3000
```

浏览器仍然只访问 Nginx 入口：

```text
http://<服务器IP>:2026
```

> 注意：源码映射模式使用 Next.js dev server 和 Gateway reload，适合服务器开发、联调和频繁更新。正式生产环境建议使用仓库已有的 `make up`，把前端构建产物烘焙进镜像，而不是把源码目录挂载进生产容器。

## 1. 与现有本地启动方式的关系

`linux-local-start.md` 的前后端是宿主机进程：

```text
宿主机 uvicorn  :8001
宿主机 Next.js  :3000
Docker Nginx     :2026
```

本文会把前两个进程替换为两个 Docker 容器：

```text
Docker Gateway   :8001
Docker Frontend  :3000
Docker Nginx     :2026
```

现有 Nginx 容器继续代理 `host.docker.internal:8001` 和 `host.docker.internal:3000`，所以不需要改浏览器访问地址。切换前必须停止原来手动启动的 uvicorn 和 Next.js，否则会出现端口占用。

本文新增的 Compose 文件是：

```text
docker/docker-compose-source-host.yaml
```

它只定义 `gateway` 和 `frontend`，不会重复定义或删除现有 PostgreSQL、Redis、Nginx 容器。

## 2. 前置条件

以下命令以 Bash 为例。假设服务器代码目录为 `/opt/deer-flow`，请按实际路径修改 `ROOT`：

```bash
export ROOT="/opt/deer-flow"
cd "$ROOT"
```

确认 Docker 和 Compose 可用：

```bash
docker --version
docker compose version
docker info
```

确认当前分支没有阻止更新的本地修改：

```bash
git status --short --branch
git branch --show-current
```

确认代码、配置文件和扩展配置已经存在：

```bash
test -f "$ROOT/config.yaml"
test -f "$ROOT/extensions_config.json"
test -f "$ROOT/.env"
test -f "$ROOT/frontend/.env"
```

如果是首次配置，可以按需从示例复制，不能覆盖已有配置：

```bash
test -f "$ROOT/config.yaml" || cp "$ROOT/config.example.yaml" "$ROOT/config.yaml"
test -f "$ROOT/extensions_config.json" || cp "$ROOT/extensions_config.example.json" "$ROOT/extensions_config.json"
test -f "$ROOT/frontend/.env" || cp "$ROOT/frontend/.env.example" "$ROOT/frontend/.env"
```

`config.yaml` 中的模型、数据库和 sandbox 配置仍按项目实际情况填写。真实 API Key、数据库密码和其他凭据只写入服务器上的 `.env` 或配置文件，不要提交 Git。

## 3. 确认现有中间件容器

本文假设你已经按照 `linux-local-start.md` 创建了下列容器：

| 容器 | 用途 | 宿主机端口 |
| --- | --- | --- |
| `deerflow-postgres` | PostgreSQL 持久化数据库 | `127.0.0.1:55432` |
| `deer-flow-redis` | Redis Stream Bridge | `127.0.0.1:6379` |
| `deer-flow-nginx-host` | DeerFlow 统一入口 | `0.0.0.0:2026` |

查看状态：

```bash
docker ps -a \
  --filter "name=deerflow-postgres" \
  --filter "name=deer-flow-redis" \
  --filter "name=deer-flow-nginx-host" \
  --format "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"
```

如果容器存在但没有运行：

```bash
docker start deerflow-postgres deer-flow-redis deer-flow-nginx-host
```

验证 PostgreSQL 和 Redis：

```bash
docker exec deerflow-postgres pg_isready
docker exec deer-flow-redis redis-cli ping
```

期望分别看到数据库可接受连接和：

```text
PONG
```

确认 Nginx 容器使用的是上一份手册生成的宿主机代理配置：

```bash
docker exec deer-flow-nginx-host nginx -T 2>/dev/null \
  | grep -E "host\.docker\.internal:(3000|8001)"
```

如果没有匹配结果，请重新执行 `linux-local-start.md` 的“Nginx 中间件”章节。该章节会把 upstream 从容器内的 `127.0.0.1` 改成 `host.docker.internal`，并添加：

```text
--add-host=host.docker.internal:host-gateway
```

## 4. 停止原来宿主机启动的前后端

先确认 `3000` 和 `8001` 的进程属于 DeerFlow，再停止它们：

```bash
sudo ss -ltnp '( sport = :8001 or sport = :3000 )'
sudo fuser -k 8001/tcp 3000/tcp
```

如果之前使用的是仓库自带的 Docker 开发栈，也先停止它，避免服务名和端口混用：

```bash
docker compose -p deer-flow-dev \
  -f "$ROOT/docker/docker-compose-dev.yaml" \
  down
```

不要执行 `docker compose down -v`，否则会删除该 Compose 项目管理的命名卷。

## 5. 配置 Docker 源码映射环境

### 5.1 数据库和 Redis 地址

由于本文的前后端容器使用 Linux host network，容器内的 `127.0.0.1` 就是服务器的网络命名空间。因此，已有 `linux-local-start.md` 配置的下面地址可以继续使用：

```dotenv
DATABASE_URL=postgresql://<数据库用户>:<数据库密码>@127.0.0.1:55432/deerflow
DEER_FLOW_STREAM_BRIDGE_REDIS_URL=redis://127.0.0.1:6379/0
```

并确认 `config.yaml` 引用了数据库环境变量：

```yaml
database:
  backend: postgres
  postgres_url: $DATABASE_URL
  postgres_schema: deerflow
```

如果当前 `DATABASE_URL` 使用的是别的用户名、密码或数据库名，保持它与 `deerflow-postgres` 容器实际创建的账号一致。不要直接照抄示例中的凭据。

### 5.2 前端入口和局域网访问

如果只从服务器本机访问，`frontend/.env` 可以保留本机配置。

如果通过局域网 IP 或域名访问，在项目根目录 `.env` 中设置实际入口 origin。例如服务器 IP 为 `192.168.1.20`：

```dotenv
DEER_FLOW_TRUSTED_ORIGINS=http://192.168.1.20:2026,http://localhost:2026
DEER_FLOW_DEV_ALLOWED_ORIGINS=192.168.1.20
```

`DEER_FLOW_DEV_ALLOWED_ORIGINS` 只用于 Next.js dev server。缺少它时，页面可能能返回 SSR HTML，但 `/_next/*`、字体或 HMR 资源被拒绝，浏览器端无法完成 hydration，表现为页面一直 `Loading...` 或按钮无响应。

本文的 Compose 文件已经将浏览器 API 地址设为空，让浏览器通过 Nginx 的同源 `/api` 访问 Gateway：

```dotenv
NEXT_PUBLIC_BACKEND_BASE_URL=
NEXT_PUBLIC_LANGGRAPH_BASE_URL=
```

如果 `frontend/.env` 中已经设置了直连 `localhost:8001` 的值，Compose 的环境配置会覆盖它；不需要为浏览器改成 `http://<服务器IP>:8001`。

### 5.3 AIO sandbox（纯 DooD）模式

如果 `config.yaml` 使用：

```yaml
sandbox:
  use: deerflow.community.aio_sandbox:AioSandboxProvider
```

且没有 `provisioner_url`，Gateway 需要访问宿主机 Docker daemon。请在项目根目录 `.env` 中补充宿主机绝对路径：

```dotenv
DEER_FLOW_HOST_BASE_DIR=/opt/deer-flow/backend/.deer-flow
```

启动时追加仓库已有的 DooD overlay：

```bash
-f "$ROOT/docker/docker-compose.dood.yaml"
```

挂载 Docker socket 等价于允许 Gateway 控制宿主机 Docker，具有较高权限。只有明确使用纯 DooD sandbox 时才追加该 overlay。

如果使用 `LocalSandboxProvider`，不要追加 DooD overlay。

## 6. 首次启动前端和 Gateway

```bash
cd ~/.../deer-flow

docker compose \
  --env-file .env \
  -p deer-flow-source \
  -f docker/docker-compose-source-host.yaml \
  up -d --build --remove-orphans
```

-p: `deer-flow-source` 项目名称

如果是纯 DooD sandbox，使用带 overlay 的函数：

```bash
docker compose \
  --env-file .env \
  -p deer-flow-source \
  -f docker/docker-compose-source-host.yaml \
  -f docker/docker-compose.dood.yaml \
  up -d --build --remove-orphans
```

这条命令会完成以下工作：

1. 构建 backend/frontend 的开发镜像。
2. 将服务器上的 `backend/` 映射到 Gateway 容器。
3. 将服务器上的 `frontend/` 映射到 Frontend 容器。
4. 启动时执行 `uv sync --all-packages` 和 `pnpm install --frozen-lockfile`。
5. 启动 Gateway reload 和 Next.js dev server。
6. 将容器设置为 `unless-stopped`。

检查服务端口：

```bash
curl -i http://127.0.0.1:8001/health
curl -I http://127.0.0.1:3000
curl -i http://127.0.0.1:2026/health
```

浏览器访问：

```text
http://<服务器IP>:2026
```

首次部署进入 `/setup`，已有账号进入 `/login`。

## 7. 代码更新和重新部署

### 7.1 重新启动

```bash
docker compose \
  --env-file .env \
  -p deer-flow-source \
  -f docker/docker-compose-source-host.yaml \
  up -d
```

查看日志：
```bash
docker compose \
  --env-file .env \
  -p deer-flow-source \
  -f docker/docker-compose-source-host.yaml \
  logs -f
```

检查服务状态：
```bash
curl -fsS http://127.0.0.1:8001/health
curl -fsS http://127.0.0.1:2026/health
```

停止服务：
```bash
docker compose \
  --env-file .env \
  -p deer-flow-source \
  -f docker/docker-compose-source-host.yaml \
  down
```


### 7.2 确保彻底按照最新代码重建

以下文件发生变化时尤其需要 `--build`：

- `backend/Dockerfile`
- `frontend/Dockerfile`
- `backend/pyproject.toml`
- `backend/uv.lock`
- `frontend/package.json`
- `frontend/pnpm-lock.yaml`
- `docker/docker-compose-source-host.yaml`

推荐开发阶段使用：
```bash
cd ~/.../deer-flow

docker compose \
  --env-file .env \
  -p deer-flow-source \
  -f docker/docker-compose-source-host.yaml \
  build --no-cache
```

然后:
```bash
docker compose \
  --env-file .env \
  -p deer-flow-source \
  -f docker/docker-compose-source-host.yaml \
  up -d --remove-orphans
```


### 7.3 只修改业务源码时

前后端目录已经映射到容器，Gateway reload 和 Next.js dev server 通常会自动发现源码变化。若更新后需要立即清理进程状态，可以执行：

```bash
dc restart gateway frontend
```

如果修改了配置、依赖、Dockerfile 或 Compose 文件，使用完整的 `up -d --build --force-recreate`。

### 7.4 更新 Docker 基础镜像

如果还要拉取更新后的基础镜像：

```bash
dc build --pull gateway frontend
dc up -d --force-recreate --remove-orphans
```

## 8. 日常运维命令

查看前端和 Gateway 状态：

```bash
dc ps
```

查看前端实时日志：

```bash
dc logs -f frontend
```

Gateway 的开发入口会把日志写入仓库的 `logs/gateway.log`，查看方式：

```bash
tail -f "$ROOT/logs/gateway.log"
```

查看现有 Nginx 日志：

```bash
docker logs --tail 100 deer-flow-nginx-host
```

停止本文新增的前后端容器：

```bash
dc stop
```

停止并删除本文 Compose 创建的前后端容器，但保留命名卷：

```bash
dc down
```

服务器重启后，Docker 会自动拉起已经创建过且未被手动停止的容器：

```bash
docker ps
```

`restart: unless-stopped` 不负责首次创建容器；首次部署仍需要先执行一次 `dc up -d`。

## 9. 常见问题

### 9.1 `bind: address already in use`

检查端口占用：

```bash
sudo ss -ltnp '( sport = :3000 or sport = :8001 or sport = :2026 )'
docker ps --format "table {{.Names}}\t{{.Ports}}\t{{.Status}}"
```

常见原因：

- 旧的宿主机 uvicorn 仍占用 `8001`。
- 旧的宿主机 Next.js 仍占用 `3000`。
- `docker-compose-dev.yaml` 或生产 Compose 栈仍在运行。
- 现有 Nginx 容器以外的程序占用了 `2026`。

确认属于本项目后停止旧进程，再执行：

```bash
sudo fuser -k 8001/tcp 3000/tcp
dc up -d --build --force-recreate
```

### 9.2 Nginx 返回 `502 Bad Gateway`

按顺序检查：

```bash
dc ps
curl -i http://127.0.0.1:8001/health
curl -I http://127.0.0.1:3000
docker exec deer-flow-nginx-host nginx -T 2>/dev/null \
  | grep -E "host\.docker\.internal:(3000|8001)"
docker logs --tail 100 deer-flow-nginx-host
```

如果 `8001` 或 `3000` 直连失败，先看对应服务日志；如果直连成功但 `2026` 仍然 502，通常是 Nginx 配置没有指向 `host.docker.internal`，或者 Nginx 容器缺少 `host-gateway` 映射。

### 9.3 Gateway 报 PostgreSQL 连接失败

确认本文 Compose 文件仍然使用：

```yaml
network_mode: host
```

确认数据库容器正在运行：

```bash
docker exec deerflow-postgres pg_isready
```

确认容器内环境变量存在且与数据库账号一致：

```bash
dc exec gateway sh -lc 'python -c "import os; print(bool(os.getenv(\"DATABASE_URL\")))"'
```

如果 `DATABASE_URL` 使用了容器内不存在的地址或错误凭据，修改项目根目录 `.env` 后重新执行：

```bash
dc up -d --force-recreate gateway
```

### 9.4 Gateway 报 Redis 连接失败

确认：

```bash
docker exec deer-flow-redis redis-cli ping
dc exec gateway sh -lc 'python -c "import os; print(os.getenv(\"DEER_FLOW_STREAM_BRIDGE_REDIS_URL\", \"<未设置>\"))"'
```

host network 模式下默认应为：

```text
redis://127.0.0.1:6379/0
```

修改 `.env` 后重建 Gateway：

```bash
dc up -d --force-recreate gateway
```

### 9.5 页面一直 `Loading...` 或按钮没有反应

通过服务器 IP 或域名访问时，确认根目录 `.env` 包含实际入口：

```dotenv
DEER_FLOW_TRUSTED_ORIGINS=http://<服务器IP>:2026,http://localhost:2026
DEER_FLOW_DEV_ALLOWED_ORIGINS=<服务器IP>
```

然后重启前端：

```bash
dc up -d --force-recreate frontend
```

同时检查浏览器开发者工具和：

```bash
dc logs --tail 200 frontend
```

### 9.6 AIO sandbox 无法启动

确认：

1. `config.yaml` 使用了 `AioSandboxProvider`。
2. 追加了 `docker/docker-compose.dood.yaml`。
3. Docker daemon 正常运行。
4. `.env` 中的 `DEER_FLOW_HOST_BASE_DIR` 是服务器上的绝对路径。
5. Gateway 容器内存在 Docker CLI 和 `/var/run/docker.sock`。

检查：

```bash
docker info
dc config
dc exec gateway sh -lc 'test -S /var/run/docker.sock && echo docker-socket-ok'
```

如果当前并不需要 AIO sandbox，改用 `LocalSandboxProvider` 并移除 DooD overlay。

### 9.7 `git pull` 后页面代码没有变化

确认挂载生效：

```bash
dc exec frontend sh -lc 'pwd; test -f /app/frontend/package.json && echo frontend-source-mounted'
dc exec gateway sh -lc 'pwd; test -f /app/backend/pyproject.toml && echo backend-source-mounted'
```

再执行一次完整更新：

```bash
dc up -d --build --force-recreate --remove-orphans
```

如果只是浏览器缓存，使用无痕窗口或强制刷新验证。

## 10. 安全和适用范围

本文使用 `network_mode: host`，前端和 Gateway 会直接监听服务器的 `3000`、`8001` 端口。即使用户只打算访问 `2026`，也应在防火墙或云安全组中限制 `3000/8001`，只允许本机或可信来源访问。

本方案适合：

- 可信局域网开发服务器；
- 需要频繁 `git pull` 和快速重启的联调环境；
- 已有中间件容器，不希望重复创建 PostgreSQL、Redis、Nginx 的环境。

本方案不适合直接作为公网生产方案。公网部署应使用 HTTPS、反向代理访问控制、最小权限数据库账号、凭据管理和仓库已有的生产镜像部署方式。
