# Windows 本地启动：Docker Desktop 管理中间件

Gateway 和 Next.js 在 Windows 宿主机运行；Docker Desktop 的 Compose 项目 `deerflow-dev` 仅包含 PostgreSQL、Redis、Nginx 三个容器。项目名使用小写是 Docker Compose 的命名要求。本指南中的 PowerShell 命令均从仓库根目录执行。

| 服务 | 运行位置 | 宿主机地址 |
| --- | --- | --- |
| PostgreSQL 15 | Docker | `127.0.0.1:7432` |
| Redis 7 | Docker | `127.0.0.1:7379` |
| Nginx | Docker | `http://127.0.0.1:2026` |
| Gateway | Windows | `http://127.0.0.1:7001` |
| Next.js | Windows | `http://127.0.0.1:7000` |

浏览器统一从 `http://localhost:2026` 进入。Nginx 容器通过 Docker Desktop 的 `host.docker.internal` 访问宿主机上的 Gateway 和前端；因此两者需监听 `0.0.0.0`。Compose 仅将容器端口绑定到宿主机回环地址，不向局域网开放数据库、Redis 或 Nginx。此启动方式不使用 `make dev`、`make docker-start` 或 Linux 的 host network Compose：它们会另行启动 Nginx，或将前后端也放入容器。

## 1. 准备配置并检查端口

启动 Docker Desktop，在根目录 `.env` 设置 `POSTGRES_USER`、`POSTGRES_PASSWORD`、`POSTGRES_DB`，并指向本地配置文件 `DEER_FLOW_CONFIG_PATH`（Windows 下使用绝对路径和正斜杠）。`config.dev.yaml` 的数据库配置应使用 `$DATABASE_URL`。需要同时检查以下本地文件。根目录 `.env` 与 `config.dev.yaml` 在当前仓库是已跟踪文件，可能包含真实凭据；请勿将凭据或改动后的敏感连接串提交、推送到远端：

- 根目录 `.env`：`DATABASE_URL` 指向 `127.0.0.1:7432`，`DEER_FLOW_STREAM_BRIDGE_REDIS_URL` 指向 `redis://127.0.0.1:7379/0`，`DEER_FLOW_INTERNAL_GATEWAY_BASE_URL` 指向 `http://127.0.0.1:7001`。
- `config.dev.yaml`：若 `stream_bridge.redis_url` 写了固定地址，将端口改为 `7379`；显式 YAML 地址优先于环境变量。
- `frontend/.env`：`DEER_FLOW_INTERNAL_GATEWAY_BASE_URL` 指向 `http://localhost:7001`。浏览器仍通过 Nginx 同源 `/api` 访问 Gateway；需要直接从 `7000` 访问前端时，将 `http://localhost:7000` 加入可信来源和 Gateway CORS 来源。

检查五个端口是否被系统应用占用或排除：

```powershell
Set-Location (git rev-parse --show-toplevel)
Get-NetTCPConnection -LocalPort 7432,7379,2026,7001,7000 -State Listen -ErrorAction SilentlyContinue |
  Select-Object LocalAddress,LocalPort,OwningProcess
netsh interface ipv4 show excludedportrange protocol=tcp
netsh interface ipv6 show excludedportrange protocol=tcp
```

旧版独立 Nginx 容器可能占用 `2026`；先按下文迁移，再启动新项目。不要按端口盲目终止其他应用。首次准备依赖：在 `backend/` 执行 `uv sync --all-packages --extra postgres --extra redis`，在 `frontend/` 执行 `pnpm install`。

## 2. 首次迁移现有 PostgreSQL 数据

**已经迁移过的用户跳过本节**，直接进入第 3 节。旧 `deerflow-postgres` 同时挂载了两个卷：父目录 `/var/lib/postgresql` 的 `postgres_data`，以及数据目录 `/var/lib/postgresql/data` 的匿名卷。真正的数据库文件在后者。只复用 `postgres_data` 会启动空数据库。

先停止正在向旧数据库写入的 Gateway 和其他客户端，并记录旧集群的数据库/表数量。以下命令会正常停掉旧 PostgreSQL，然后将匿名数据卷冷复制到 `deerflow-dev-postgres-data`，保留旧卷供回退。复制 1 GB 以上数据需要一定时间；中途失败不要启动新 PostgreSQL：

```powershell
Set-Location (git rev-parse --show-toplevel)
$oldPg = (docker inspect deerflow-postgres | ConvertFrom-Json)[0]
$sourceVolume = ($oldPg.Mounts | Where-Object Destination -eq '/var/lib/postgresql/data').Name
if (-not $sourceVolume) { throw '未找到旧数据库的数据卷，停止迁移' }
if (@(docker volume ls --format '{{.Name}}') -contains 'deerflow-dev-postgres-data') {
  throw '目标数据卷已存在；先确认其内容，不要覆盖原有数据'
}

docker stop deerflow-postgres
if ($LASTEXITCODE -ne 0) { throw '停止旧数据库失败' }
docker volume create deerflow-dev-postgres-data
if ($LASTEXITCODE -ne 0) { throw '创建目标卷失败' }
docker run --rm --network none `
  -v "${sourceVolume}:/source:ro" `
  -v deerflow-dev-postgres-data:/target `
  nginx:latest cp -a /source/. /target/
if ($LASTEXITCODE -ne 0) { throw '数据库文件复制失败；旧数据卷仍保留' }

$sourceHash = (docker run --rm --network none -v "${sourceVolume}:/source:ro" `
  nginx:latest sh -c 'tar --sort=name -C /source -cf - . | sha256sum').Split(' ')[0]
$targetHash = (docker run --rm --network none -v deerflow-dev-postgres-data:/source:ro `
  nginx:latest sh -c 'tar --sort=name -C /source -cf - . | sha256sum').Split(' ')[0]
if ($sourceHash -ne $targetHash) { throw '数据卷校验失败；不要启动新数据库' }
```

检查通过后再移除旧容器以释放名称和旧端口；**不要**添加 `-v`，以保留原始匿名数据卷和 `postgres_data`：

```powershell
docker rm deerflow-postgres
docker stop deer-flow-redis deer-flow-nginx-host
docker rm deer-flow-redis deer-flow-nginx-host
```

Redis 使用原有命名卷 `deer-flow-redis-data`。如需全新安装且没有旧 PostgreSQL，可以直接 `docker volume create deerflow-dev-postgres-data`，再创建或确认 `deer-flow-redis-data`；首次初始化 PostgreSQL 时需要 `.env` 中的三个 `POSTGRES_*` 值。不要对旧卷执行 `docker volume rm` 或 `docker volume prune`。

## 3. 启动三个 Docker 容器

```powershell
Set-Location (git rev-parse --show-toplevel)
docker compose --env-file .env -f docker/docker-compose-windows-local.yaml up -d --wait
docker compose --env-file .env -f docker/docker-compose-windows-local.yaml ps
```

Docker Desktop 中应出现名为 `deerflow-dev` 的项目，下属 `deerflow-postgres`、`deer-flow-redis`、`deer-flow-nginx-host` 三个容器。检查服务和旧数据：

```powershell
docker exec deer-flow-redis redis-cli ping
docker exec deerflow-postgres pg_isready
$pgUser = (docker exec deerflow-postgres printenv POSTGRES_USER).Trim()
docker exec deerflow-postgres psql -U $pgUser -d postgres -Atc 'SELECT system_identifier FROM pg_control_system()'
Test-NetConnection 127.0.0.1 -Port 7432 -InformationLevel Quiet
Test-NetConnection 127.0.0.1 -Port 7379 -InformationLevel Quiet
Test-NetConnection 127.0.0.1 -Port 2026 -InformationLevel Quiet
```

`pg_control_system()` 的标识应与迁移前的旧数据库一致；还应核对业务数据库与表数量。`docker port` 只显示 Docker 记录的映射；如 Windows TCP 检查失败，应先看 `docker compose ... ps`、容器日志及 Docker Desktop 状态。

## 4. 在 Windows 启动 Gateway 和前端

在两个独立 PowerShell 窗口运行以下命令：

```powershell
Set-Location (git rev-parse --show-toplevel)
Set-Location .\backend
uv run --no-sync --env-file ../.env uvicorn app.gateway.app:app --host 0.0.0.0 --port 7001 --loop asyncio:SelectorEventLoop
```

```powershell
Set-Location (git rev-parse --show-toplevel)
Set-Location .\frontend
uv run --no-sync --env-file ../.env python ../scripts/pnpm.py dev -- --hostname 0.0.0.0 --port 7000
```

也可在 VS Code 的“运行和调试”中选择 `DeerFlow: Windows本地 Gateway + Frontend` 并按 `F5`。该复合配置仅启动宿主机进程；先按第 3 节启动容器。如果 Python 调试器仍选择全局 Python，重载 VS Code 窗口后重试；后端配置固定使用 `backend/.venv/Scripts/python.exe`。

检查用户可访问的入口：

```powershell
curl.exe -fsS http://127.0.0.1:7001/health/ready
curl.exe -fsS http://127.0.0.1:2026/health/ready
curl.exe -I http://127.0.0.1:7000/
curl.exe -I http://127.0.0.1:2026/
```

前端入口 `http://localhost:2026`；首次配置管理员时打开 `/setup`，已有用户打开 `/login`。启动时若入口暂时返回 502，先确认宿主机 Gateway 和前端已分别监听 7001、7000，再检查 Nginx 容器到 `host.docker.internal` 的连接。

## 5. 停止与故障排查

Gateway 和前端在各自终端按 `Ctrl+C` 停止；调试进程在 VS Code 停止调试。仅停止三个容器：

```powershell
Set-Location (git rev-parse --show-toplevel)
docker compose --env-file .env -f docker/docker-compose-windows-local.yaml stop
```

再次启动用第 3 节的 `up -d --wait`。**不要使用 `down -v`**，这会删除非外部数据卷；本配置将 PostgreSQL 和 Redis 指向明确的外部命名卷以防误删。

此编排仅用于 Windows Docker Desktop，本仓库常规 `make dev`、全栈 Docker Compose 和 Linux 本地部署仍使用各自的端口与启动方式。
