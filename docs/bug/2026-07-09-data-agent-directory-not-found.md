# DataAgent 运行时报 Agent directory not found 问题记录

## 1、现象

- 前端进入 `http://localhost:2026/workspace/agents/data-agent/chats/{thread_id}` 后可以打开页面。
- 点击发送后，后端报错：

```text
Agent directory not found: D:\A-PythonWork\AOpenGithub\deer-flow\backend\.deer-flow\users\e7b8e7c9-eaf6-4eed-b7bd-4eb6f636576e\agents\data-agent
```

## 2、根因

- 当前 Gateway 进程从 `backend/` 目录启动，`DEER_FLOW_HOME` / `DEER_FLOW_PROJECT_ROOT` 未显式指向仓库根目录时，运行时根目录解析为 `backend/.deer-flow`。
- 之前 DataAgent 文件写在仓库根目录 `.deer-flow/users/default/agents/data-agent/`，运行中的 Gateway 实际查找的是 `backend/.deer-flow/...`。
- 登录态用户的有效 `user_id` 是 `e7b8e7c9-eaf6-4eed-b7bd-4eb6f636576e`，custom-agent loader 会先查 `users/{user_id}/agents/{agent_name}`。
- 现有代码的共享回退路径是 legacy shared layout：`{DEER_FLOW_HOME}/agents/{agent_name}`，不是 `users/default/agents/{agent_name}`。

因此，`users/default` 只代表无登录/无用户上下文时的默认用户桶，不是所有登录用户的全局共享智能体目录。

## 3、临时处置

已把 DataAgent 复制到当前 Gateway 可见的共享路径：

```text
backend/.deer-flow/agents/data-agent/config.yaml
backend/.deer-flow/agents/data-agent/SOUL.md
```

验证结果：在模拟登录用户 `e7b8e7c9-eaf6-4eed-b7bd-4eb6f636576e` 上，`load_agent_config('data-agent')`、`load_agent_soul('data-agent')` 和 `list_custom_agents()` 均可读取 `data-agent`。

## 4、后续建议

- 若希望本地运行状态统一放在仓库根目录，应在启动 Gateway 前设置：

```powershell
$env:DEER_FLOW_PROJECT_ROOT="D:\A-PythonWork\AOpenGithub\deer-flow"
# 或直接指定：
$env:DEER_FLOW_HOME="D:\A-PythonWork\AOpenGithub\deer-flow\.deer-flow"
```

- 若希望 DataAgent 对所有登录用户可见，应把模板发布到 `{DEER_FLOW_HOME}/agents/data-agent/` 共享目录，或新增受控的“公共 custom-agent”配置层；不要把 `users/default` 当作登录用户共享目录。
