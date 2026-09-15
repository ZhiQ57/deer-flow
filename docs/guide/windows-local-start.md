# DeerFlow 本地手动启动手册（Docker 仅部署中间件）

本文记录当前推荐的 Windows 本地启动方式：**前端和后端在宿主机运行，Docker 只运行中间件**。

本文约定先在 DeerFlow 仓库根目录执行命令；启动参数统一从根目录 `.env` 和
`frontend/.env` 读取，不在 PowerShell 中逐项设置 `$env:` 临时变量。
Windows 的 `.env` 路径请填写绝对路径并使用正斜杠（例如
`D:/A-PythonWork/AOpenGithub/deer-flow/config.dev.yaml`）。不要写
`$DEER_FLOW_PROJECT_ROOT\config.dev.yaml`，VS Code 的 `envFile` 不会可靠展开这种
变量拼接。
切换测试或生产配置时，同时修改 `.env` 中的
`DEER_FLOW_CONFIG_PATH`（对应绝对路径）和 `DEER_FLOW_CONFIG_FILE`，并确保只保留
一组未注释的阶段配置。

- 宿主机后端：Gateway API，端口 `8001`
- 宿主机前端：Next.js，端口 `3000`
- Docker 中间件：Redis，端口 `6379`
- Docker 中间件：Nginx，统一入口端口 `2026`
- 浏览器入口：`http://localhost:2026`

> 注意：不要使用 `make docker-start` 启动本项目，否则会把 frontend/gateway 也放进 Docker 容器。本文方式只用 Docker 跑 Redis 和 Nginx。

## 1. 前置条件

查看系统是否残留服务没有关闭：

# 先查看服务
```powershell
Get-NetTCPConnection -LocalPort 8001,3000,2026 -State Listen -ErrorAction SilentlyContinue |
  Select-Object LocalAddress,LocalPort,State,OwningProcess
```

# 停后端 8001、前端 3000、Nginx 2026
```powershell
Get-NetTCPConnection -LocalPort 8001,3000,2026 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force }
```
需要已准备：

1. Docker Desktop 已启动。
2. `config.dev.yaml` 已配置模型（Windows 本地开发阶段配置）。
3. `extensions_config.json` 存在。
4. 后端依赖已安装：

```powershell
Set-Location (git rev-parse --show-toplevel)
Set-Location .\backend
uv sync --all-packages
uv sync --all-packages --extra postgres
```

5. 前端依赖已安装：

```powershell
Set-Location (git rev-parse --show-toplevel)
Set-Location .\frontend
pnpm install
```

如果 `pnpm install` 提示 ignored builds，可执行：

```powershell
pnpm approve-builds --all
pnpm install
```

## 2. 启动 Redis 中间件（Docker）

```powershell
Set-Location (git rev-parse --show-toplevel)
if (docker ps -a --filter "name=^/deer-flow-redis$" --format "{{.Names}}" |
    Select-String -Quiet "^deer-flow-redis$") {
  docker start deer-flow-redis
} else {
  docker run -d `
    --name deer-flow-redis `
    -p 6379:6379 `
    -v deer-flow-redis-data:/data `
    --restart unless-stopped `
    redis:7-alpine redis-server --appendonly yes
}

docker exec deer-flow-redis redis-cli ping
```

期望输出：

```text
PONG
```

## 3. 启动后端 Gateway（宿主机控制台）

新开一个 PowerShell 窗口，执行：

```powershell
Set-Location (git rev-parse --show-toplevel)
Set-Location .\backend
uv run --env-file ../.env uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001 --loop asyncio:SelectorEventLoop
```

`uv run --env-file ../.env` 会把 `.env` 中选定的 `config.dev.yaml`、数据库、Redis、
TableRAG 和 CORS 配置传给 Gateway。

验证：

```powershell
curl.exe http://localhost:8001/health
```

期望返回：

```json
{"status":"healthy","service":"deer-flow-gateway"}
```

MySQL数据库容器: 
```text
Host: 127.0.0.1
Port: 3308
User: root
Password: root@123456
数据库名：text2sql
```

DeerFlow Postgresql数据库容器:
```powershell
Set-Location (git rev-parse --show-toplevel)
docker run -d --name deerflow-postgres --env-file .env `
  -p 127.0.0.1:55432:5432 `
  -v postgres_data:/var/lib/postgresql/data `
  --restart unless-stopped `
  postgres:15
```

`POSTGRES_USER`、`POSTGRES_PASSWORD` 和 `POSTGRES_DB` 请写在根目录 `.env`；
不要在 `docker run` 命令中重复写数据库凭据。

> 修改 `config.dev.yaml` 的模型配置后，需要重启后端 Gateway。

### 3.1 使用 VS Code 调试 Gateway

仓库提供 `.vscode/launch.json`，其工作目录、环境变量和 Uvicorn 参数与上面的
PowerShell 手动启动方式一致。该配置只启动宿主机 Gateway，不会启动前端或
任何 Docker 服务。

使用前确认：

1. 使用 VS Code 打开 DeerFlow 仓库根目录，而不是只打开 `backend` 目录。
2. 已安装 VS Code Python 调试扩展。
3. 已执行 `uv sync --all-packages --extra redis`，并存在
   `backend\.venv\Scripts\python.exe`。
4. Redis、前端和 Nginx 是否启动由开发者自行决定；Gateway 调试配置不会管理它们。

启动调试：

1. 在需要调试的 DeerFlow Python 源码中设置断点。
2. 打开 VS Code“运行和调试”面板。
3. 选择 `DeerFlow: Windows本地调试 Gateway`。
4. 按 `F5` 启动。

调试器等价执行：

```powershell
Set-Location (git rev-parse --show-toplevel)
Set-Location .\backend
uv run --env-file ../.env uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001 --loop asyncio:SelectorEventLoop
```

其中 VS Code 会直接使用 `uv` 创建的
`backend\.venv\Scripts\python.exe` 启动 `uvicorn` 模块，以便断点进入
DeerFlow 内部代码。配置没有启用 `--reload`，避免热重载子进程影响断点稳定性；
修改代码后可停止调试并再次按 `F5`。

如果调试终端的第一段命令仍显示系统解释器，例如：

```text
C:\Users\<用户名>\AppData\Local\Programs\Python\Python311\python.exe
```

说明 VS Code Python 扩展仍在使用之前缓存的全局解释器。仓库配置已经同时固定
目标进程、Debug Adapter 和 Debug Launcher 使用
`backend\.venv\Scripts\python.exe`。停止当前调试后，执行一次：

1. `Ctrl+Shift+P` 打开命令面板。
2. 执行 `Developer: Reload Window`。
3. 重新选择 `DeerFlow: Windows本地调试 Gateway` 并按 `F5`。

正常情况下，调试终端命令开头应为：

```text
D:\A-PythonWork\AOpenGithub\deer-flow\backend\.venv\Scripts\python.exe
```

## 4. 启动前端 Frontend（宿主机控制台）

再新开一个 PowerShell 窗口，执行：

```powershell
Set-Location (git rev-parse --show-toplevel)
Set-Location .\frontend
uv run --env-file ../.env python ../scripts/pnpm.py dev
```

前端启动参数由 `frontend/.env` 和根目录 `.env` 提供；需要调整入口、可信来源或
开发域名时，修改对应 `.env` 后重启前端。

验证：

```powershell
curl.exe http://localhost:3000
```

返回 HTML 即代表前端启动成功。

## 5. 启动 Nginx 中间件（Docker）

Nginx 运行在 Docker 中，但代理到宿主机的前端和后端。

```powershell
Set-Location (git rev-parse --show-toplevel)
New-Item -ItemType Directory -Force -Path ".\logs" | Out-Null
((Get-Content ".\docker\nginx\nginx.local.conf" -Raw -Encoding UTF8).
  Replace("error_log logs/nginx-error.log warn;", "error_log /dev/stderr warn;").
  Replace("pid logs/nginx.pid;", "pid /tmp/nginx.pid;").
  Replace("access_log logs/nginx-access.log;", "access_log /dev/stdout;").
  Replace("error_log logs/nginx-error.log;", "error_log /dev/stderr;").
  Replace("server 127.0.0.1:8001;", "server host.docker.internal:8001;").
  Replace("server 127.0.0.1:3000;", "server host.docker.internal:3000;")) |
  Set-Content -Path ".\logs\nginx-host.conf" -Encoding UTF8

docker rm -f deer-flow-nginx-host 2>$null

docker run -d `
  --name deer-flow-nginx-host `
  -p 2026:2026 `
  --add-host=host.docker.internal:host-gateway `
  -v "${PWD}\logs\nginx-host.conf:/etc/nginx/nginx.conf:ro" `
  --restart unless-stopped `
  nginx:latest
```

验证统一入口：

```powershell
curl.exe http://localhost:2026/health
curl.exe http://localhost:2026
```

浏览器打开：

```text
http://localhost:2026
```

首次部署或无管理员账号时，进入：

```text
http://localhost:2026/setup
```

已有账号时，进入：

```text
http://localhost:2026/login
```

本地测试账号和密码统一写为：

```text
账号：test123@user.com
密码：test123@user.com
```

如果是首次初始化管理员账号，在 `/setup` 页面也使用上面的账号和密码创建测试账号。

## 6. 查看当前运行状态

```powershell
# 查看宿主机端口进程
foreach ($port in 8001,3000,2026,6379) {
  $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
  if ($conns) {
    foreach ($c in $conns) {
      $p = Get-CimInstance Win32_Process -Filter "ProcessId = $($c.OwningProcess)" -ErrorAction SilentlyContinue
      "PORT $port PID=$($c.OwningProcess) NAME=$($p.Name) CMD=$($p.CommandLine)"
    }
  } else {
    "PORT $port no Windows listener"
  }
}

# 查看 DeerFlow 中间件容器
docker ps --filter "name=deer-flow" --format "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"
```

正常情况下：

- `8001`：宿主机 `python.exe` / `uvicorn`
- `3000`：宿主机 `node.exe` / Next.js
- `2026`：Docker Nginx 映射端口
- `6379`：Docker Redis 映射端口

## 7. 停止服务

如果后端和前端是控制台启动，优先在对应窗口按 `Ctrl+C`。

停止 Docker 中间件：

```powershell
docker stop deer-flow-nginx-host deer-flow-redis
```

如需强制停止宿主机前后端，先确认端口进程属于本项目，再执行：

```powershell
foreach ($port in 8001,3000) {
  $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
  foreach ($c in $conns) {
    $p = Get-CimInstance Win32_Process -Filter "ProcessId = $($c.OwningProcess)" -ErrorAction SilentlyContinue
    if ($p.CommandLine -like "*D:\A-PythonWork\AOpenGithub\deer-flow*") {
      Stop-Process -Id $c.OwningProcess -Force
    }
  }
}
```

## 8. 常见问题

### 8.1 为什么有两个 Docker 容器？

因为本方案只把中间件放进 Docker：

- `deer-flow-redis`：Redis stream bridge 中间件。
- `deer-flow-nginx-host`：统一入口反向代理中间件。

前后端不在 Docker 内运行。

### 8.2 为什么不要让前端直连 `localhost:8001`？

标准入口是 `http://localhost:2026`。前端通过当前域名访问 `/api`，由 Nginx 转发到 Gateway，避免浏览器跨域和 CSRF 配置混乱。

### 8.3 修改模型配置后要重启什么？

只需要重启后端 Gateway。Redis、Nginx、前端通常不需要重启。

### 8.4 `config.dev.yaml` 没有模型会怎样？

Gateway 可以启动，但聊天无法正常调用模型。日志会提示：

```text
No models are configured in config.dev.yaml
```

需要在 `config.dev.yaml` 的 `models:` 下配置至少一个模型。

### 8.5 不要绕过终端安全软件

如果安全软件隔离了某个源码文件，不要关闭安全软件或强行加白名单。应先人工审查代码来源与内容，再决定是否恢复。
