# AGENTS.md

This file provides guidance to AI coding agents (Claude Code, Codex, and others) when working with code in this repository. It is the source of truth; the sibling `CLAUDE.md` imports it via `@AGENTS.md`.

It is the **monorepo orientation layer**: it maps the whole repo and points to the
module guides that own the depth. For anything inside a module, read that module's
guide rather than expecting full detail here:

- **[backend/AGENTS.md](backend/AGENTS.md)** — backend depth: harness/app split, agent &
  middleware chain, sandbox, MCP, skills, memory, IM channels, persistence/migrations,
  config system, test layout.
- **[frontend/AGENTS.md](frontend/AGENTS.md)** — frontend depth: Next.js App Router layout,
  thread/streaming data flow, code style, commands.

# 开发规定
DO NOT send optional commentary
## 开发环境

1. 开发系统环境：Windows-11 系统，必须使用 Windows PowerShell 指令执行命令;
2. Python 环境：在项目内 uv 创建环境，默认采用 python=3.11。
3. Git/Docker 环境：开发默认遵守 Git 规范，系统已安装 Docker Desktop；假设无法使用 Docker，请呼叫人类开启 Docker 环境，不要自己下载 Docker、不要陷入死循环持续执行 Docker 命令。
4. 始终遵守全文文件编码: UTF-8
5. 始终在任务执行前回复: "大哥"


## 注意事项

1. 新需求规范：当接收到新需求时，必须仔细阅读现有代码，思考新需求的可行性，先制定完成新需求的计划步骤 Todo。
2. 计划文档规范：先划分大步骤（1、2、...），再划分子步骤（1.1、1.2、...），打上 `- [ ]` 标记；每完成一步，则打上 `- [X]`。
3. 开发文档规范：所有文档写在 `docs/*`，例如：`docs/plan`、`docs/review`、`docs/bug`。
4. 开发顺序：需求 -> git 新分支 -> 开发 -> 测试 -> review -> debugger -> 测试 -> ... -> 测试通过 -> 合并 git 分支。
5. 修改目录结构、公共 API 或模块边界时，必须同步更新 `docs/guide/used-api.md` 和对应 `docs/plans/*`、`docs/reviews/*` 文档。
6. 公共 API 重构时不保留旧类名、旧函数、旧配置字段或旧导入路径兼容层；旧入口应直接删除，测试和文档同步迁移到当前目标 API。

## 注释规范

新开发的代码，代码注释全部采用中文。

- 函数定义：注释写出函数作用标题、描述作用、Args参数说明、Return返回值。
- 抽象基类：标明类作用。
- 实现类：写明具体实现类的作用和类参数、类方法等。
- 行内代码：写出关键步骤的代码注释。

## Git 规范

- 本项目 fork 仓库（origin）：`https://github.com/ZhiQ57/deer-flow.git`
- 原始上游仓库（upstream）：`https://github.com/bytedance/deer-flow.git`
- 本地开发目录：`D:\A-PythonWork\AOpenGithub\deer-flow`

分支模型：`main` 保持为追踪 `upstream/main` 的干净基线；`dev` 是长期二次开发集成分支并推送到 `origin/dev`；每个具体需求从最新 `dev` 新建 `feat|fix|docs|refactor|test|chore|build|ci/...` 分支。

- Git提交信息使用规范前缀：`feat|fix|docs|refactor|test|chore|build|ci|...`。

- 禁止使用 `codex/xx` 创建和提交分支名称.

- Git提交备注要采用中文, 说明“做了什么、为什么做、验证了什么”，方便后续查阅。

- 新功能/修改 BUG 必须从 `dev` 新建 `feat|fix|...` 分支，开发完成并测试通过后合并回 `dev`，再推送到 `origin/dev`。

- 同步上游更新时：先更新 `main`（合并或快进 `upstream/main`），再将 `main` 合并进 `dev` 并解决冲突；不要直接在 `main` 放二次开发代码。

- 不要把生成物、缓存、真实凭据、临时日志提交进仓库。


# What is DeerFlow

DeerFlow is a LangGraph-based AI super-agent system with a full-stack architecture. The
backend runs a "super agent" with sandboxed execution, persistent memory, subagent
delegation, and extensible tools (built-in, MCP, community), all per-thread isolated. The
frontend is a Next.js chat UI. External IM platforms (Feishu, Slack, Telegram, Discord,
DingTalk) bridge into the same agent through the Gateway.

## Service Topology

A single `make dev` / Docker stack runs four cooperating services:

| Service         | Port   | Role                                                                 |
| --------------- | ------ | ------------------------------------------------------------------- |
| **Nginx**       | `2026` | Unified reverse-proxy entry point — open this in the browser        |
| **Gateway API** | `8001` | FastAPI REST API + embedded LangGraph-compatible agent runtime      |
| **Frontend**    | `3000` | Next.js web interface                                               |
| **Provisioner** | `8002` | Optional — only when sandbox is configured for provisioner/K8s mode |

Nginx is the single public entry: it serves the frontend and proxies `/api/langgraph/*`
to the Gateway's LangGraph runtime, rewriting it to Gateway's native `/api/*` routes; all
other `/api/*` go straight to the Gateway REST routers. See
[backend/AGENTS.md](backend/AGENTS.md) for the runtime and router detail.

## Repository Map

```
deer-flow/
├── Makefile                        # Root orchestration: drives the full stack (dev/start/stop, docker, setup)
├── config.example.yaml             # Template → copy to config.yaml (gitignored) at repo root
├── extensions_config.example.json  # Template → copy to extensions_config.json (gitignored): MCP servers + skills
├── backend/                        # Python backend — see backend/AGENTS.md
│   ├── Makefile                    # Per-module backend commands (dev, gateway, test, lint, migrate-rev)
│   ├── packages/harness/           # deerflow-harness package (import: deerflow.*) — agent framework
│   └── app/                        # FastAPI Gateway + IM channels (import: app.*)
├── frontend/                       # Next.js frontend (pnpm) — see frontend/AGENTS.md
├── docker/                         # docker-compose files, nginx config, provisioner
├── skills/                         # Agent skills: public/ (committed), custom/ (gitignored)
├── contracts/                      # Cross-component JSON contracts (e.g. subagent status)
├── scripts/                        # Root orchestration scripts invoked by the Makefile (check, configure, doctor, support_bundle, serve, nginx, docker, deploy, setup_wizard)
├── tests/                          # Root-level tests (currently tests/skills/ — public skill tests)
└── docs/                           # Cross-cutting docs, plans, and design notes
```

Runtime config lives at the **repo root**: copy `config.example.yaml` → `config.yaml`
(main app config) and `extensions_config.example.json` → `extensions_config.json` (MCP
servers + skills). Both real files are gitignored and may be edited at runtime via the
Gateway API. Config schema and resolution order are documented in
[backend/AGENTS.md](backend/AGENTS.md).

Scheduled-task note:
- The scheduled-task MVP adds a workspace page at `/workspace/scheduled-tasks` plus a background scheduler service gated by `config.yaml -> scheduler.enabled`.
- Scheduled background runs are intentionally non-interactive: they execute through the normal run lifecycle, but the lead-agent toolset excludes `ask_clarification` when `context.non_interactive=true`. The key is honored only for internally-authenticated callers (the scheduler launch path); client-supplied `context.non_interactive` is dropped.

## Commands: Root vs. Module

**Root `make` targets drive the whole stack** (run from the repo root):

```bash
make setup       # Interactive setup wizard (recommended for new users)
make doctor      # Check configuration and system requirements
make support-bundle  # Generate redacted troubleshooting summary, AI issue draft, and optional zip
make config      # Generate local config files from the examples
make check       # Check that required tools are installed
make install     # Install all dependencies (frontend + backend + pre-commit hooks)
make dev         # Start all services with hot-reload (Gateway + Frontend + Nginx)
make start       # Start all services in production mode (local, optimized)
make stop        # Stop all running services
make up / down   # Build/stop the production Docker stack (browser at localhost:2026)
make docker-start / docker-stop / docker-logs   # Docker development environment
```

Run `make help` for the full list.

**Per-module commands drive a single module** (run inside that module):

```bash
# Backend (see backend/AGENTS.md for the full set)
cd backend && make dev        # Gateway API with reload (port 8001)
cd backend && make test       # Backend test suite
cd backend && make lint       # ruff check
cd backend && make format     # ruff format

# Frontend (see frontend/AGENTS.md for the full set)
cd frontend && pnpm dev       # Dev server with Turbopack (port 3000)
cd frontend && pnpm check     # Lint + type check (run before committing)
cd frontend && pnpm test      # Unit tests
```

Rule of thumb: **root `make` = the full application**; **`backend/Makefile` and `frontend/`
(`pnpm`) = per-module work.**

## Where to Go Next

- Backend work → **[backend/AGENTS.md](backend/AGENTS.md)**
- Frontend work → **[frontend/AGENTS.md](frontend/AGENTS.md)**
- Setup & install → **[Install.md](Install.md)**, **[CONTRIBUTING.md](CONTRIBUTING.md)**
- Project overview & usage → **[README.md](README.md)** (translations: `README_zh.md`,
  `README_ja.md`, `README_fr.md`, `README_ru.md`)
- Security policy → **[SECURITY.md](SECURITY.md)**
- Changes → **[CHANGELOG.md](CHANGELOG.md)**

## Cross-Cutting Conventions

These apply repo-wide; module guides own the module-specific detail.

- **Documentation update policy** — keep docs in sync with code: update `README.md` for
  user-facing changes and the relevant `AGENTS.md` for development/architecture changes in
  the same change set.
- **Test-driven development** — features and bug fixes ship with tests. Backend tests live
  in `backend/tests/` (TDD is mandatory there; see [backend/AGENTS.md](backend/AGENTS.md));
  frontend tests live in `frontend/tests/`.
- **Format before pushing** — run `make format` (backend) / `pnpm check` (frontend). Backend
  CI enforces `ruff format --check`, so formatting must be clean before a push.
