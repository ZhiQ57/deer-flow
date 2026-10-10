# VSSAG MCP：stdio 接入

MCP 由 DeerFlow 在加载或调用工具时自动启动，不需要另开服务终端。Windows/Linux 使用相同安装指令；仅 Python 的绝对路径不同。DeerFlow 沿用你自己的启动方式，本文不启动其他服务。

## 1、安装服务及 Gateway 入口

在仓库根目录执行：

```text
cd mcp-extensions/vssag
uv sync --python 3.11 --extra server --extra postgres
cd ../..
uv pip install --python backend/.venv --no-deps --editable ./mcp-extensions/vssag
```

迁移目录后须重新执行，旧的可编辑安装仍指向旧目录。Gateway 保留项目现有 Python >=3.12 环境；服务使用独立 Python 3.11 环境。

## 2、配置路径和凭据

在 `mcp-extensions/vssag/` 准备 `config.dev.yaml` 和 `.env.dev`：

- `config.dev.yaml`：使用已复制的 SDK 配置，确认数据库、文档索引和模型设置。
- `.env.dev`：填齐 YAML 引用的环境变量；使用 DashScope 时必须包括 `DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL`。保留已有凭据，不提交真实值。

在仓库根 `.env` 设置下面两项；替换占位内容，使用绝对路径：

```dotenv
VSSAG_MCP_PYTHON=<MCP服务的Python绝对路径>
VSSAG_MCP_CONFIG=<mcp-extensions/vssag/config.dev.yaml的绝对路径>
```

服务 Python 路径：

| 系统 | 仓库绝对路径后追加 |
| --- | --- |
| Windows | `/mcp-extensions/vssag/.venv/Scripts/python.exe` |
| Linux | `/mcp-extensions/vssag/.venv/bin/python` |

stdio 不继承完整 Gateway 环境。凭据放服务 `.env.dev`；若实际 MCP `env` 已转发 `$DASHSCOPE_API_KEY` 等变量，这些变量也必须在 Gateway 环境内非空，否则删除转发项，使用服务 dotenv。

## 3、注册实际配置

编辑仓库根 `extensions_config.json`，合并以下三项，保留已有服务、技能和其他条目：

```json
{
  "middlewares": ["middlewares.vssag_directory:VssagDirectoryMiddleware"],
  "mcpInterceptors": ["vssag_mcp_server.interceptor:build_context_interceptor"],
  "mcpServers": {
    "vssag": {
      "enabled": true,
      "type": "stdio",
      "command": "$VSSAG_MCP_PYTHON",
      "args": ["-I", "-m", "vssag_mcp_server.server", "--config", "$VSSAG_MCP_CONFIG", "--root", "/home/mnt/knowledge"],
      "env": {"APP_ENV": "dev", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
      "session_init_timeout": 60,
      "tool_call_timeout": 45
    }
  }
}
```

如果主 YAML 已配置 `extensions.middlewares`，也要在那份列表加入上述 Middleware。虚拟根路径不是实体 Sandbox 挂载。

## 4、重启 Gateway 并验证

按你原来的方式重启 Gateway（例如 VS Code 调试），使新路径和可编辑安装生效。不需要手工启动 MCP。

网页新建会话，发送：“用 VSSAG 检索【已知文档关键词】，读取命中文档并给出依据，不要用 Bash。”

应看到 `vssag_search`，按需出现 `vssag_grep_file`；延迟工具可先出现 `tool_search`。后台目录注入应记录 `VSSAG directory context: injected`。

工具启动成功不代表知识库有数据：当前工具查询文档索引，需提前通过 SDK 导入文档并配置 owner/ACL；SQL 索引不能替代文档索引。`Connection closed` 或工具数量为 0 时，先检查服务配置、凭据和两个路径。
