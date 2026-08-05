# Data-Agent `publish_query_labels` 与 `tool_search` 问题诊断

## 1. 现象

测试 Data-Agent 时，模型调用 `tool_search` 查询 `publish_query_labels`，返回没有找到该工具。

## 2. 结论

当前问题由两个独立层次组成：

### 2.1 `tool_search` 不负责搜索普通内置工具

`tool_search` 的目录由 `build_deferred_tool_setup()` 组装，目录候选只保留 `is_mcp_tool(tool)` 为真的工具。

因此：

- `sqlrag_retrieve` 这类 MCP 工具可以进入 `tool_search` 目录；
- `publish_query_labels` 是普通 LangChain 内置工具，即使通过 `DataAgentServiceAbility.build_tools()` 加入最终工具列表，也不会进入 `tool_search` 目录；
- 用 `tool_search` 查找 `publish_query_labels` 返回 `No tools found matching` 是当前设计下的正常结果。

Data-Agent 应直接调用已经绑定到模型的 `publish_query_labels`，只在 `sqlrag_retrieve` 的 Schema 被延迟隐藏时，调用 `tool_search` 查询 `select:sqlrag_retrieve`。

### 2.2 当前分支的 `service_ability` 解析链路已经断裂

`DataAgentServiceAbility.build_tools()` 确实返回：

- `publish_query_labels`
- `ask_intent_approval`

但是当前 `HEAD` 的构建链路没有可靠地把它们装配到最终工具列表：

1. `lead_agent._make_lead_agent()` 将整个 `AgentConfig` 传给 `resolve_service_ability_safely()`；
2. 当前 `registry.py` 却读取 `app_config.service_name`；
3. `AgentConfig` 实际字段是 `service_ability`，不存在 `service_name`；
4. 当前 resolver 还对整个 `AgentConfig` 做 `DataAgentServiceAbilityConfig.model_validate(app_config)`，而不是解析 `app_config.service_ability`；
5. 因此 `service_ability` 通常解析为 `None`，后续 `service_ability.build_tools()` 不会执行，`publish_query_labels` 也不会进入 `final_tools`。

这解释了为什么模型可能看不到直接调用 `publish_query_labels` 的 Schema，转而尝试使用 `tool_search`。

### 2.3 当前分支还存在循环导入回归

`registry.py` 顶层导入 `AgentConfig`，而 `AgentConfig` 的导入链会进入 `runtime`、`thread_state`，`thread_state` 又反向导入 `registry.merge_service_states`。

当前分支执行：

```powershell
Set-Location "D:\A-PythonWork\AOpenGithub\deer-flow\backend"
uv run pytest tests\test_data_agent_service_ability.py -q
```

会在测试收集阶段失败，错误为：

```text
ImportError: cannot import name 'merge_service_states'
from partially initialized module 'deerflow.agents.service_agent.registry'
```

因此在修复工具发现问题前，必须先恢复 `service_agent` 的可导入性。

## 3. 与上一提交的对比

`HEAD~1` 的实现契约是：

```text
lead_agent -> resolve_service_ability_safely(agent_config.service_ability)
resolver    -> parse_service_ability(raw)
```

当前 `HEAD` 改成了：

```text
lead_agent -> resolve_service_ability_safely(agent_config)
resolver    -> 读取 service_name，并直接 model_validate(app_config)
```

这次重构没有同步更新 `AgentConfig.service_ability` 的真实数据结构，造成了解析入口不匹配。

## 4. 建议修复方向

1. 统一 resolver 契约：输入应为 `AgentConfig.service_ability` 原始字典，或明确让 resolver 接收 `AgentConfig` 并在内部读取 `.service_ability`，二者只能选一种。
2. 恢复 `registry.py` 的轻量导入边界，避免顶层导入 `AgentConfig` 触发 `registry -> agents_config -> runtime -> thread_state -> registry` 循环。
3. 保留 `publish_query_labels` 为 Data-Agent 专属普通工具，不要把它伪装成 MCP 或加入 `tool_search` 目录。
4. 增加装配测试，至少断言：
   - Data-Agent 最终工具列表包含 `publish_query_labels` 和 `ask_intent_approval`；
   - `tool_search` 目录包含 `sqlrag_retrieve`，不包含 `publish_query_labels`；
   - `resolve_service_ability_safely(AgentConfig(...))` 能按实际配置成功解析；
   - 相关测试可以正常收集，不发生循环导入。

## 5. 验证记录

- `python -m py_compile`：`registry.py` 和 `service_ability.py` 语法通过。
- `tests/test_harness_boundary.py`：通过。
- `tests/test_data_agent_service_ability.py`：当前分支在收集阶段因循环导入失败。
- `tests/test_tool_search.py`：当前分支在导入阶段因同一循环导入失败。
