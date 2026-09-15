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
>
> 如果希望把前端和 Gateway 也交给 Docker，并映射服务器上的本地源码自动启动，请改用 [Linux Docker 源码映射部署手册](linux-docker-source-deploy.md)。

## 1. 前置条件

本文命令以 Bash 为例。请先把服务器实际路径和阶段配置写入仓库根目录
`.env`（Linux 测试/联调通常选择 `config.test.yaml`），再进入仓库根目录。
启动命令通过 `uv run --env-file` 或 Docker 的 `--env-file` 读取 `.env`，
不需要在当前 shell 中 `export` 临时变量：

```bash
cd /opt/deer-flow
```

如果你的仓库不在 `/opt/deer-flow`，只修改上面的 `cd` 路径，并把 `.env` 中的
`DEER_FLOW_PROJECT_ROOT`、`DEER_FLOW_HOME`、`DEER_FLOW_CONFIG_PATH`、
`DEER_FLOW_EXECUTE` 和 `TABLERAG_CONFIG` 改成同一台 Linux 主机上的绝对路径。
不要写 `$HOME/...` 或 `$DEER_FLOW_PROJECT_ROOT/...` 这种依赖 dotenv 插值的路径。
Linux 测试/联调使用 `DEER_FLOW_CONFIG_PATH=/opt/deer-flow/config.test.yaml`
和 `DEER_FLOW_CONFIG_FILE=config.test.yaml`；生产部署则成对改为
`config.prod.yaml`。两个变量只保留一组活动值。

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
3. `config.test.yaml` 已配置模型（Linux 测试/联调阶段配置）。
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
cd /opt/deer-flow/backend
uv python install 3.12
uv sync --all-packages
uv sync --all-packages --extra postgres --extra redis
```

前端依赖安装：

```bash
cd /opt/deer-flow
python3 scripts/pnpm.py install
```

如果 `pnpm install` 提示 ignored builds，可执行：

```bash
cd /opt/deer-flow
python3 scripts/pnpm.py approve-builds --all
python3 scripts/pnpm.py install
```

## 2. 启动 DeerFlow PostgreSQL 数据库（Docker）

PostgreSQL 用于 DeerFlow 持久化数据库。本文默认只绑定到 `127.0.0.1:55432`，让宿主机后端访问，不直接暴露给局域网。

```bash
cd /opt/deer-flow

if docker ps -a --filter "name=^/deerflow-postgres$" --format "{{.Names}}" | grep -qx "deerflow-postgres"; then
  docker start deerflow-postgres
else
  docker run -d \
    --name deerflow-postgres \
    --env-file .env \
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

请在 `.env` 中设置 `DATABASE_URL`，启动时由 `uv run --env-file` 自动读取。

如果 `config.test.yaml` 还没有切到 PostgreSQL，添加或修改下面配置：

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
  cd /opt/deer-flow

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
cd /opt/deer-flow/backend
uv run --env-file ../.env uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001 --loop asyncio:SelectorEventLoop
```

本机验证：

```bash
curl -i http://127.0.0.1:8001/health
```

局域网验证（将 `<Linux局域网IP>` 替换为实际地址）：

```bash
curl -i "http://<Linux局域网IP>:8001/health"
```

期望返回：

```json
{"status":"healthy","service":"deer-flow-gateway"}
```

> 修改 `config.test.yaml` 的模型配置或 `database` 配置后，需要重启后端 Gateway。

## 5. 启动前端 Frontend（宿主机控制台）

再新开一个终端窗口，执行：

```bash
cd /opt/deer-flow/frontend
uv run --env-file ../.env python ../scripts/pnpm.py dev --turbo --hostname 0.0.0.0 --port 3000
```

前端的 `NEXT_PUBLIC_*`、Gateway SSR 地址、可信来源和开发域名均从
`.env`/`frontend/.env` 读取；不要在启动命令中重新 `export`。

本机验证：

```bash
curl -I http://127.0.0.1:3000
```

局域网验证（将 `<Linux局域网IP>` 替换为实际地址）：

```bash
curl -I "http://<Linux局域网IP>:3000"
```

返回 HTML 响应头即代表前端启动成功。

## 6. 启动 Nginx 中间件（Docker）

Nginx 运行在 Docker 中，但代理到宿主机的前端和后端。Linux 下 Docker 容器访问宿主机需要显式添加：

```text
--add-host=host.docker.internal:host-gateway
```

启动命令：

```bash
cd /opt/deer-flow
mkdir -p logs

sed \
  -e 's#error_log logs/nginx-error.log warn;#error_log /dev/stderr warn;#' \
  -e 's#pid logs/nginx.pid;#pid /tmp/nginx.pid;#' \
  -e 's#access_log logs/nginx-access.log;#access_log /dev/stdout;#' \
  -e 's#error_log logs/nginx-error.log;#error_log /dev/stderr;#' \
  -e 's#server 127.0.0.1:8001;#server host.docker.internal:8001;#' \
  -e 's#server 127.0.0.1:3000;#server host.docker.internal:3000;#' \
  docker/nginx/nginx.local.conf > logs/nginx-host.conf

if docker ps -a --filter "name=^/deer-flow-nginx-host$" --format "{{.Names}}" | grep -qx "deer-flow-nginx-host"; then
  docker rm -f deer-flow-nginx-host
fi

docker run -d \
  --name deer-flow-nginx-host \
  -p 0.0.0.0:2026:2026 \
  --add-host=host.docker.internal:host-gateway \
  -v "$(pwd)/logs/nginx-host.conf:/etc/nginx/nginx.conf:ro" \
  --restart unless-stopped \
  nginx:latest
```

验证统一入口：

```bash
curl -i "http://<Linux局域网IP>:2026/health"
curl -I "http://<Linux局域网IP>:2026"
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

说明 `config.test.yaml` 已配置：

```yaml
database:
  backend: postgres
```

但 Gateway 启动环境里没有 `DATABASE_URL`，或者 `config.test.yaml` 的 `postgres_url` 没有写正确。
请修正 `.env` 中的 `DATABASE_URL`，然后重新启动 Gateway。

并确保 `config.test.yaml` 中有：

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

### 10.7 `config.test.yaml` 没有模型会怎样？

Gateway 可以启动，但聊天无法正常调用模型。日志会提示：

```text
No models are configured in config.test.yaml
```

需要在 `config.test.yaml` 的 `models:` 下配置至少一个模型。

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

### 10.9 `/setup` 页面一直显示 `Loading...` 怎么办？

先确认 setup 状态接口返回正常：

```bash
curl -sS "http://<Linux局域网IP>:2026/api/v1/auth/setup-status"
echo
```

正常首次部署应返回：

```json
{"needs_setup":true,"registration_enabled":true}
```

如果接口正常但页面仍卡在 `Loading...`，通常是 Next.js dev 模式拦截了局域网 origin 加载
`/_next/*`、字体或 HMR 资源，导致页面只拿到 SSR HTML，没有完成浏览器端 hydration。
请把 `DEER_FLOW_DEV_ALLOWED_ORIGINS` 写入 `.env`，然后重启前端。

然后重启前端：

```bash
cd /opt/deer-flow/frontend
uv run --env-file ../.env python ../scripts/pnpm.py exec next dev --turbo --hostname 0.0.0.0 --port 3000
```

如果浏览器控制台还有 `Immersive Translate`、翻译插件、广告拦截插件相关报错，请先用无痕窗口或禁用插件验证，避免插件脚本干扰页面初始化。

### 10.10 局域网部署的安全边界是什么？

本文方式适合可信局域网开发和测试。`0.0.0.0` 会让同一网络中能访问该机器 IP 的设备连接服务；不要在公网机器上直接开放这些端口。公网部署应使用正式域名、HTTPS、访问控制和更严格的反向代理策略。
