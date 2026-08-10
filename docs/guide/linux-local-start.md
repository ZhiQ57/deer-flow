# DeerFlow Linux 本地手动启动手册（Docker 仅部署中间件）

本文记录 Linux 系统下通过纯命令启动 DeerFlow 的方式：**前端和后端在宿主机运行，Docker 只运行中间件**。

- 宿主机后端：Gateway API，监听 `0.0.0.0:8001`
- 宿主机前端：Next.js，监听 `0.0.0.0:3000`
- Docker 中间件：PostgreSQL，宿主机端口 `55432`
- Docker 中间件：Redis，宿主机端口 `6379`
- Docker 中间件：Nginx，统一入口监听 `0.0.0.0:2026`
- 局域网入口：`http://<Linux局域网IP>:2026`
- 后端直连验证入口：`http://<Linux局域网IP>:8001/health`

> 注意：不要使用 `make docker-start` 启动本项目，否则会把 frontend/gateway 也放进 Docker 容器。本文方式只用 Docker 跑 PostgreSQL、Redis 和 Nginx。

## 1. 前置条件

本文命令以 Bash 为例，假设仓库目录为：

```bash
export ROOT="$HOME/deer-flow"
cd "$ROOT"
```

如果你的仓库在 `/opt/deer-flow` 或其他路径，请把 `ROOT` 改成实际目录。

查看当前 Linux 局域网 IP：

```bash
hostname -I | awk '{print $1}'
```

如果机器有多张网卡，请用下面命令确认实际对外网卡 IP：

```bash
ip -4 addr show scope global
```

检查端口是否已有残留服务：

```bash
sudo ss -ltnp '( sport = :8001 or sport = :3000 or sport = :2026 or sport = :6379 or sport = :55432 )'
```

如需停止确认属于本项目的残留进程：

```bash
sudo fuser -k 8001/tcp 3000/tcp 2026/tcp
```

需要已准备：

1. Docker Engine 已安装并运行。
2. 当前用户可执行 Docker 命令；如果没有权限，先配置 docker 用户组或在命令前加 `sudo`。
3. `config.yaml` 已配置模型。
4. `extensions_config.json` 存在。
5. 已安装 `uv`、Node.js、Corepack。

常用安装检查：

```bash
docker --version
docker compose version
uv --version
node --version
corepack --version
```

如果缺少 Corepack：

```bash
corepack enable
```

后端依赖安装：

```bash
cd "$ROOT/backend"
uv python install 3.12
uv sync --all-packages
uv sync --all-packages --extra postgres --extra redis
```

前端依赖安装：

```bash
cd "$ROOT"
python3 scripts/pnpm.py install
```

如果 `pnpm install` 提示 ignored builds，可执行：

```bash
cd "$ROOT"
python3 scripts/pnpm.py approve-builds --all
python3 scripts/pnpm.py install
```

## 2. 启动 DeerFlow PostgreSQL 数据库（Docker）

PostgreSQL 用于 DeerFlow 持久化数据库。本文默认只绑定到 `127.0.0.1:55432`，让宿主机后端访问，不直接暴露给局域网。

```bash
cd "$ROOT"

if docker ps -a --filter "name=^/deerflow-postgres$" --format "{{.Names}}" | grep -qx "deerflow-postgres"; then
  docker start deerflow-postgres
else
  docker run -d \
    --name deerflow-postgres \
    -e POSTGRES_USER=myuser \
    -e POSTGRES_PASSWORD=123456 \
    -e POSTGRES_DB=deerflow \
    -p 127.0.0.1:55432:5432 \
    -v deerflow-postgres-data:/var/lib/postgresql/data \
    --restart unless-stopped \
    postgres:15
fi
```

等待数据库就绪：

```bash
until docker exec deerflow-postgres pg_isready -U myuser -d deerflow; do
  sleep 2
done
```

期望输出包含：

```text
accepting connections
```

验证连接：

```bash
docker exec -it deerflow-postgres psql -U myuser -d deerflow -c "select version();"
```

DeerFlow 后端使用的连接串：

```bash
export DATABASE_URL="postgresql://myuser:123456@127.0.0.1:55432/deerflow"
```

如果 `config.yaml` 还没有切到 PostgreSQL，添加或修改下面配置：

```yaml
database:
  backend: postgres
  postgres_url: $DATABASE_URL
  postgres_schema: deerflow
```

`postgres_schema: deerflow` 会让 DeerFlow 自动创建并使用 `deerflow` schema。首次启动时会自动初始化应用表、LangGraph checkpointer 和 Store 表。

如确实需要让局域网其他机器直连 PostgreSQL，把端口映射改为：

```bash
-p 0.0.0.0:55432:5432
```

同时需要放通防火墙的 `55432/tcp`。开发测试环境通常不建议这样做。

## 3. 启动 Redis 中间件（Docker）

Redis 只给宿主机后端使用，默认绑定到 `127.0.0.1:6379`，不暴露给局域网。

```bash
cd "$ROOT"

if docker ps -a --filter "name=^/deer-flow-redis$" --format "{{.Names}}" | grep -qx "deer-flow-redis"; then
  docker start deer-flow-redis
else
  docker run -d \
    --name deer-flow-redis \
    -p 127.0.0.1:6379:6379 \
    -v deer-flow-redis-data:/data \
    --restart unless-stopped \
    redis:7-alpine redis-server --appendonly yes
fi

docker exec deer-flow-redis redis-cli ping
```

期望输出：

```text
PONG
```

## 4. 启动后端 Gateway（宿主机控制台）

新开一个终端窗口，执行：

```bash
export ROOT="$HOME/deer-flow"
export LAN_IP="$(hostname -I | awk '{print $1}')"

export PYTHONIOENCODING="utf-8"
export PYTHONUTF8="1"
export PYTHONPATH="."
export DEER_FLOW_PROJECT_ROOT="$ROOT"
export DEER_FLOW_HOME="$ROOT/backend/.deer-flow"
export DEER_FLOW_CONFIG_PATH="$ROOT/config.yaml"
export DEER_FLOW_EXTENSIONS_CONFIG_PATH="$ROOT/extensions_config.json"
export DATABASE_URL="postgresql://myuser:123456@127.0.0.1:55432/deerflow"
export DEER_FLOW_STREAM_BRIDGE_REDIS_URL="redis://127.0.0.1:6379/0"
export GATEWAY_CORS_ORIGINS="http://$LAN_IP:3000,http://$LAN_IP:2026,http://localhost:3000,http://127.0.0.1:3000"

cd "$ROOT/backend"
uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001
```

本机验证：

```bash
curl -i http://127.0.0.1:8001/health
```

局域网验证：

```bash
curl -i "http://$LAN_IP:8001/health"
```

期望返回：

```json
{"status":"healthy","service":"deer-flow-gateway"}
```

> 修改 `config.yaml` 的模型配置或 `database` 配置后，需要重启后端 Gateway。

## 5. 启动前端 Frontend（宿主机控制台）

再新开一个终端窗口，执行：

```bash
export ROOT="$HOME/deer-flow"
export LAN_IP="$(hostname -I | awk '{print $1}')"

# 标准局域网入口走 Nginx 当前域名 /api，不让浏览器直连 8001。
export NEXT_PUBLIC_BACKEND_BASE_URL=""
export NEXT_PUBLIC_LANGGRAPH_BASE_URL=""

# Next.js 服务端内部访问 Gateway。
export DEER_FLOW_INTERNAL_GATEWAY_BASE_URL="http://127.0.0.1:8001"
export DEER_FLOW_TRUSTED_ORIGINS="http://$LAN_IP:3000,http://$LAN_IP:2026,http://localhost:3000,http://localhost:2026"
export SKIP_ENV_VALIDATION="1"

cd "$ROOT"
python3 scripts/pnpm.py exec next dev --turbo --hostname 0.0.0.0 --port 3000
```

本机验证：

```bash
curl -I http://127.0.0.1:3000
```

局域网验证：

```bash
curl -I "http://$LAN_IP:3000"
```

返回 HTML 响应头即代表前端启动成功。

## 6. 启动 Nginx 中间件（Docker）

Nginx 运行在 Docker 中，但代理到宿主机的前端和后端。Linux 下 Docker 容器访问宿主机需要显式添加：

```text
--add-host=host.docker.internal:host-gateway
```

启动命令：

```bash
export ROOT="$HOME/deer-flow"
cd "$ROOT"

mkdir -p "$ROOT/logs"
NGINX_CONF="$ROOT/logs/nginx-host.conf"

sed \
  -e 's#error_log logs/nginx-error.log warn;#error_log /dev/stderr warn;#' \
  -e 's#pid logs/nginx.pid;#pid /tmp/nginx.pid;#' \
  -e 's#access_log logs/nginx-access.log;#access_log /dev/stdout;#' \
  -e 's#error_log logs/nginx-error.log;#error_log /dev/stderr;#' \
  -e 's#server 127.0.0.1:8001;#server host.docker.internal:8001;#' \
  -e 's#server 127.0.0.1:3000;#server host.docker.internal:3000;#' \
  "$ROOT/docker/nginx/nginx.local.conf" > "$NGINX_CONF"

if docker ps -a --filter "name=^/deer-flow-nginx-host$" --format "{{.Names}}" | grep -qx "deer-flow-nginx-host"; then
  docker rm -f deer-flow-nginx-host
fi

docker run -d \
  --name deer-flow-nginx-host \
  -p 0.0.0.0:2026:2026 \
  --add-host=host.docker.internal:host-gateway \
  -v "$NGINX_CONF:/etc/nginx/nginx.conf:ro" \
  --restart unless-stopped \
  nginx:latest
```

验证统一入口：

```bash
curl -i "http://$LAN_IP:2026/health"
curl -I "http://$LAN_IP:2026"
```

浏览器打开：

```text
http://<Linux局域网IP>:2026
```

首次部署或无管理员账号时，进入：

```text
http://<Linux局域网IP>:2026/setup
```

已有账号时，进入：

```text
http://<Linux局域网IP>:2026/login
```

本地测试账号和密码统一写为：

```text
账号：test123@user.com
密码：test123@user.com
```

如果是首次初始化管理员账号，在 `/setup` 页面也使用上面的账号和密码创建测试账号。

## 7. 放通 Linux 防火墙

如果局域网其他机器无法访问，先确认本机能访问：

```bash
curl -I http://127.0.0.1:2026
```

Ubuntu / Debian 使用 UFW 时：

```bash
sudo ufw allow 2026/tcp
sudo ufw allow 8001/tcp
sudo ufw reload
sudo ufw status
```

CentOS / Rocky Linux / AlmaLinux 使用 firewalld 时：

```bash
sudo firewall-cmd --add-port=2026/tcp --permanent
sudo firewall-cmd --add-port=8001/tcp --permanent
sudo firewall-cmd --reload
sudo firewall-cmd --list-ports
```

如果只希望用户通过 Nginx 入口访问，生产或多人环境建议只放通 `2026/tcp`，不要对局域网开放 `8001/tcp`。

如果你明确把 PostgreSQL 映射为 `0.0.0.0:55432:5432`，还需要额外放通 `55432/tcp`；否则保持默认的 `127.0.0.1:55432` 即可。

## 8. 查看当前运行状态

查看宿主机端口：

```bash
sudo ss -ltnp '( sport = :8001 or sport = :3000 or sport = :2026 or sport = :6379 or sport = :55432 )'
```

查看 DeerFlow 中间件容器：

```bash
docker ps --filter "name=deer" --format "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"
```

查看 Nginx 代理日志：

```bash
docker logs --tail 100 deer-flow-nginx-host
```

查看 PostgreSQL 日志：

```bash
docker logs --tail 100 deerflow-postgres
```

正常情况下：

- `8001`：宿主机 `uvicorn` / Python Gateway，监听 `0.0.0.0`
- `3000`：宿主机 Next.js，监听 `0.0.0.0`
- `2026`：Docker Nginx 映射端口，监听 `0.0.0.0`
- `6379`：Docker Redis 映射端口，默认只绑定 `127.0.0.1`
- `55432`：Docker PostgreSQL 映射端口，默认只绑定 `127.0.0.1`

## 9. 停止服务

如果后端和前端是控制台启动，优先在对应窗口按 `Ctrl+C`。

停止 Docker 中间件：

```bash
docker stop deer-flow-nginx-host deer-flow-redis deerflow-postgres
```

如需强制停止宿主机前后端，先确认端口进程属于本项目：

```bash
sudo ss -ltnp '( sport = :8001 or sport = :3000 )'
```

确认无误后再执行：

```bash
sudo fuser -k 8001/tcp 3000/tcp
```

## 10. 常见问题

### 10.1 为什么有三个 Docker 容器？

因为本方案只把中间件放进 Docker：

- `deerflow-postgres`：DeerFlow 持久化数据库。
- `deer-flow-redis`：Redis stream bridge 中间件。
- `deer-flow-nginx-host`：统一入口反向代理中间件。

前后端不在 Docker 内运行。

### 10.2 为什么 PostgreSQL 端口使用 `55432`？

避免和 Linux 宿主机可能已经安装的 PostgreSQL 默认端口 `5432` 冲突。容器内仍是 `5432`，宿主机通过 `127.0.0.1:55432` 访问。

### 10.3 Gateway 启动时报 `database.postgres_url is required` 怎么办？

说明 `config.yaml` 已配置：

```yaml
database:
  backend: postgres
```

但 Gateway 启动环境里没有 `DATABASE_URL`，或者 `config.yaml` 的 `postgres_url` 没有写正确。按本文方式启动 Gateway 时应包含：

```bash
export DATABASE_URL="postgresql://myuser:123456@127.0.0.1:55432/deerflow"
```

并确保 `config.yaml` 中有：

```yaml
database:
  backend: postgres
  postgres_url: $DATABASE_URL
  postgres_schema: deerflow
```

### 10.4 为什么 Nginx 配置要替换成 `host.docker.internal`？

Nginx 在 Docker 容器内运行，容器里的 `127.0.0.1` 指向容器自己，不是 Linux 宿主机。

启动容器时添加：

```text
--add-host=host.docker.internal:host-gateway
```

再把 nginx upstream 改成：

```nginx
server host.docker.internal:8001;
server host.docker.internal:3000;
```

容器才能代理到宿主机上的 Gateway 和 Next.js。

### 10.5 为什么不要让前端直连 `http://<Linux局域网IP>:8001`？

标准入口是 `http://<Linux局域网IP>:2026`。前端通过当前域名访问 `/api`，由 Nginx 转发到 Gateway，避免浏览器跨域和 CSRF 配置混乱。

需要临时从浏览器直连 `8001` 时，必须同步检查 `GATEWAY_CORS_ORIGINS` 是否包含前端页面的精确 origin。

### 10.6 修改模型配置后要重启什么？

只需要重启后端 Gateway。Redis、Nginx、前端通常不需要重启。

### 10.7 `config.yaml` 没有模型会怎样？

Gateway 可以启动，但聊天无法正常调用模型。日志会提示：

```text
No models are configured in config.yaml
```

需要在 `config.yaml` 的 `models:` 下配置至少一个模型。

### 10.8 局域网机器打不开 `2026` 怎么排查？

按顺序检查：

```bash
hostname -I
sudo ss -ltnp '( sport = :2026 )'
docker ps --filter "name=deer-flow-nginx-host"
curl -I http://127.0.0.1:2026
curl -I "http://$(hostname -I | awk '{print $1}'):2026"
```

如果本机能访问、局域网不能访问，通常是 Linux 防火墙、云服务器安全组、路由隔离或访问了错误网卡 IP。

### 10.9 局域网部署的安全边界是什么？

本文方式适合可信局域网开发和测试。`0.0.0.0` 会让同一网络中能访问该机器 IP 的设备连接服务；不要在公网机器上直接开放这些端口。公网部署应使用正式域名、HTTPS、访问控制和更严格的反向代理策略。
