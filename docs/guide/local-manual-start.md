# DeerFlow 本地手动启动手册（Docker 仅部署中间件）

本文记录当前推荐的 Windows 本地启动方式：**前端和后端在宿主机运行，Docker 只运行中间件**。

- 宿主机后端：Gateway API，端口 `8001`
- 宿主机前端：Next.js，端口 `3000`
- Docker 中间件：Redis，端口 `6379`
- Docker 中间件：Nginx，统一入口端口 `2026`
- 浏览器入口：`http://localhost:2026`

> 注意：不要使用 `make docker-start` 启动本项目，否则会把 frontend/gateway 也放进 Docker 容器。本文方式只用 Docker 跑 Redis 和 Nginx。

## 1. 前置条件

查看系统是否残留服务没有关闭：

# 先查看服务
Get-NetTCPConnection -LocalPort 8001,3000,2026 -State Listen -ErrorAction SilentlyContinue |
  Select-Object LocalAddress,LocalPort,State,OwningProcess

# 停后端 8001、前端 3000、Nginx 2026
Get-NetTCPConnection -LocalPort 8001,3000,2026 -State Listen -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty OwningProcess -Unique |
  ForEach-Object { Stop-Process -Id $_ -Force }

需要已准备：

1. Docker Desktop 已启动。
2. `config.yaml` 已配置模型。
3. `extensions_config.json` 存在。
4. 后端依赖已安装：

```powershell
Set-Location "$Root\backend"
uv sync --all-packages --extra redis
```

5. 前端依赖已安装：

```powershell
Set-Location "$Root\frontend"
pnpm install
```

如果 `pnpm install` 提示 ignored builds，可执行：

```powershell
pnpm approve-builds --all
pnpm install
```

## 2. 启动 Redis 中间件（Docker）

```powershell
$Root = "D:\A-PythonWork\AOpenGithub\deer-flow"
Set-Location $Root

$RedisExists = docker ps -a --filter "name=^/deer-flow-redis$" --format "{{.Names}}"
if ($RedisExists -eq "deer-flow-redis") {
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
$Root = "D:\A-PythonWork\AOpenGithub\deer-flow"

$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$env:PYTHONPATH = "."
$env:DEER_FLOW_PROJECT_ROOT = $Root
$env:DEER_FLOW_HOME = "$Root\backend\.deer-flow"
$env:DEER_FLOW_CONFIG_PATH = "$Root\config.yaml"
$env:DEER_FLOW_EXTENSIONS_CONFIG_PATH = "$Root\extensions_config.json"
$env:DEER_FLOW_STREAM_BRIDGE_REDIS_URL = "redis://localhost:6379/0"
$env:GATEWAY_CORS_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"

Set-Location "$Root\backend"
uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001
```

验证：

```powershell
curl.exe http://localhost:8001/health
```

期望返回：

```json
{"status":"healthy","service":"deer-flow-gateway"}
```

> 修改 `config.yaml` 的模型配置后，需要重启后端 Gateway。

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
3. 选择 `DeerFlow: 调试 Gateway`。
4. 按 `F5` 启动。

调试器等价执行：

```powershell
Set-Location "$Root\backend"
uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001
```

其中 VS Code 会直接使用 `uv` 创建的
`backend\.venv\Scripts\python.exe` 启动 `uvicorn` 模块，以便断点进入
DeerFlow 内部代码。配置没有启用 `--reload`，避免热重载子进程影响断点稳定性；
修改代码后可停止调试并再次按 `F5`。

## 4. 启动前端 Frontend（宿主机控制台）

再新开一个 PowerShell 窗口，执行：

```powershell
$Root = "D:\A-PythonWork\AOpenGithub\deer-flow"

# 标准本地入口走 Nginx 当前域名 /api，不让浏览器直连 8001。
$env:NEXT_PUBLIC_BACKEND_BASE_URL = ""
$env:NEXT_PUBLIC_LANGGRAPH_BASE_URL = ""

# Next.js 服务端内部访问 Gateway。
$env:DEER_FLOW_INTERNAL_GATEWAY_BASE_URL = "http://localhost:8001"
$env:DEER_FLOW_TRUSTED_ORIGINS = "http://localhost:3000,http://localhost:2026"
$env:SKIP_ENV_VALIDATION = "1"

Set-Location "$Root\frontend"
corepack pnpm dev
```

验证：

```powershell
curl.exe http://localhost:3000
```

返回 HTML 即代表前端启动成功。

## 5. 启动 Nginx 中间件（Docker）

Nginx 运行在 Docker 中，但代理到宿主机的前端和后端。

```powershell
$Root = "D:\A-PythonWork\AOpenGithub\deer-flow"
Set-Location $Root

New-Item -ItemType Directory -Force -Path "$Root\logs" | Out-Null
$NginxConf = "$Root\logs\nginx-host.conf"

$Text = Get-Content "$Root\docker\nginx\nginx.local.conf" -Raw -Encoding UTF8
$Text = $Text.Replace("error_log logs/nginx-error.log warn;", "error_log /dev/stderr warn;")
$Text = $Text.Replace("pid logs/nginx.pid;", "pid /tmp/nginx.pid;")
$Text = $Text.Replace("access_log logs/nginx-access.log;", "access_log /dev/stdout;")
$Text = $Text.Replace("error_log logs/nginx-error.log;", "error_log /dev/stderr;")
$Text = $Text.Replace("server 127.0.0.1:8001;", "server host.docker.internal:8001;")
$Text = $Text.Replace("server 127.0.0.1:3000;", "server host.docker.internal:3000;")
Set-Content -Path $NginxConf -Value $Text -Encoding UTF8

$NginxExists = docker ps -a --filter "name=^/deer-flow-nginx-host$" --format "{{.Names}}"
if ($NginxExists -eq "deer-flow-nginx-host") {
  docker rm -f deer-flow-nginx-host
}

docker run -d `
  --name deer-flow-nginx-host `
  -p 2026:2026 `
  --add-host=host.docker.internal:host-gateway `
  -v "${NginxConf}:/etc/nginx/nginx.conf:ro" `
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

### 8.4 `config.yaml` 没有模型会怎样？

Gateway 可以启动，但聊天无法正常调用模型。日志会提示：

```text
No models are configured in config.yaml
```

需要在 `config.yaml` 的 `models:` 下配置至少一个模型。

### 8.5 不要绕过终端安全软件

如果安全软件隔离了某个源码文件，不要关闭安全软件或强行加白名单。应先人工审查代码来源与内容，再决定是否恢复。
