# Qwen 显式提示词缓存真实探针记录

## 测试目标

验证当前 DeerFlow 使用的 DashScope OpenAI 兼容地址与 `qwen3.6-plus` 是否支持显式上下文缓存，并确认新增 `QwenChatModel` 后，非流式和流式调用都能获得缓存命中统计。

## 测试环境

- 日期：2026-07-23
- 模型：`qwen3.6-plus`
- 地址：`https://dashscope.aliyuncs.com/compatible-mode/v1`
- 凭据：复用本机 `DASHSCOPE_API_KEY`，测试输出未记录凭据
- 缓存 TTL：5 分钟
- 固定前缀：人工生成的无业务数据系统提示词
- 输入规模：约 3300 至 4000 Token，超过厂商最低 1024 Token 要求
- 输出限制：8 Token

## 原始接口探针

首次请求在系统消息文本块末尾添加：

```json
{
  "cache_control": {
    "type": "ephemeral"
  }
}
```

| 请求 | 状态码 | 输入 Token | 缓存创建 Token | 缓存命中 Token | 耗时 |
|---|---:|---:|---:|---:|---:|
| 显式缓存首次创建 | 200 | 3319 | 3304 | 0 | 1205 ms |
| 相同前缀再次请求 | 200 | 3319 | 0 | 3304 | 1135 ms |
| 无缓存标记对照一 | 200 | 3619 | 无该字段 | 无该字段 | 1018 ms |
| 无缓存标记对照二 | 200 | 3619 | 无该字段 | 无该字段 | 1188 ms |

结论：当前地址、凭据和模型支持 DashScope 显式缓存；无 `cache_control` 时不会自动产生相同缓存字段。

## DeerFlow Provider 探针

通过 `create_chat_model(name="Qwen3.6-plus", thinking_enabled=False)` 创建新增 Provider 后连续请求：

| 请求 | 输入 Token | 缓存创建 Token | 缓存命中 Token | DeerFlow `cache_read` | 耗时 |
|---|---:|---:|---:|---:|---:|
| Provider 首次创建 | 4019 | 4004 | 0 | 0 | 1442 ms |
| Provider 再次命中 | 4019 | 0 | 4004 | 4004 | 913 ms |

继续使用 `model.stream()` 发送同前缀请求，最终流式用量为：

```json
{
  "input_tokens": 4019,
  "output_tokens": 8,
  "total_tokens": 4027,
  "input_token_details": {
    "cache_read": 4004
  }
}
```

结论：新增 Provider 的缓存标记确实进入真实请求，且现有 LangChain 与 DeerFlow 用量统计链能够在流式响应中保留缓存命中 Token。

## 实现边界

- 缓存逻辑放在独立 `deerflow.models.qwen_provider:QwenChatModel` 中。
- Provider 继承 `PatchedChatDeepSeek`，继续复用多轮 `reasoning_content` 回放。
- 默认关闭缓存；仅当模型配置设置 `enable_prompt_caching: true` 时启用。
- 只标记最后一条系统消息的最后一个非空文本块。
- 不标记用户消息、记忆、工具结果、SQL结果或人工审批内容。
- 支持 `prompt_cache_ttl: "5m"` 与 `"1h"`。
- 不修改 Lead Agent 图、中间件链、DataAgent 状态或 Gateway 路由。

## 已知限制

- DashScope 只有在首次响应完整结束后才创建缓存，并按 TTL 自动过期。
- 相同缓存点之前的消息和工具定义必须保持一致，否则无法命中。
- 低于厂商最低 Token 要求的前缀不会创建缓存。
- DeerFlow 当前费用聚合已经区分缓存命中 Token，但尚未单独按缓存创建溢价计算成本；原始 `token_usage.prompt_tokens_details.cache_creation_input_tokens` 仍保留在模型响应元数据中。

## 代码审查结论

- Provider 通过模型反射路径接入，没有修改 `create_chat_model()`、Lead Agent 图或中间件顺序。
- `enable_prompt_caching` 与 `prompt_cache_ttl` 是 Provider 声明字段，不会被 LangChain 转移到 `model_kwargs` 后错误发送给接口。
- 默认关闭策略保证旧配置继续使用原始字符串系统消息；当前本机 `config.yaml` 已显式切换到 `QwenChatModel` 并启用五分钟缓存。
- 缓存断点只写入一个系统文本块，重复处理仍保持单个断点，并由配置统一决定 TTL。
- `DASHSCOPE_API_KEY` 已作为 LangChain secret 映射，序列化模型时不会暴露真实 Key。
- 继承的 `reasoning_content` 回放由新增测试锁定，没有因缓存消息转换而丢失。
- DataAgent 的日期、记忆、用户问题、标签审批、SQL和工具结果均位于系统消息缓存点之后，不会进入显式缓存块。

## 验证结果

- `backend/tests/test_qwen_provider.py`：10 passed。
- Qwen Provider、Patched DeepSeek、模型工厂、Token统计联合回归：115 passed，1 个既有测试故意触发的 unknown-kwarg warning。
- 新增 Python 文件 Ruff format/check：通过。
- `git diff --check`：通过。
- 全量 Ruff 检查发现两个与本需求无关的既有文件问题：
  - `backend/packages/harness/deerflow/skills/skillscan/__init__.py` 导入排序。
  - `backend/packages/harness/deerflow/subagents/registry.py` 格式差异。
- 全量 pytest 在收集 `test_skillscan_native.py` 时触发 Windows 安全软件对反向 Shell/密钥测试样本的隔离，报 `OSError: [Errno 9] Bad file descriptor`；测试前该文件无修改，已从当前提交恢复，未修改其内容。为避免再次被隔离，本轮以模型相关定向回归作为交付验证。

## 前端缓存用量展示

### 展示位置

严格复用 DeerFlow 原始聊天页顶部 `TokenUsageIndicator`：

- 顶部按钮在开启总量显示且存在缓存命中时，追加“缓存 + Token数”。
- 点击原有 Token 按钮后，在同一个下拉框中增加：
  - 缓存命中 Token。
  - 未缓存输入 Token。
  - 缓存命中率。
- 没有缓存命中时保持原始页面效果，不增加空卡片。
- 每轮 Token 汇总与调试 Token 明细保持原样，不在其他消息区域重复展示缓存数据。

### 数据链

1. `RunJournal` 已将厂商缓存命中保存到 `token_usage_by_model[*].cache_read_tokens`。
2. Memory/SQL RunStore 在现有 `aggregate_tokens_by_thread()` 中汇总为 `total_cache_read_tokens`。
3. `GET /api/threads/{thread_id}/token-usage` 返回该字段，不新增数据库列或迁移。
4. 前端 `threadTokenUsageToTokenUsage()` 映射累计缓存命中。
5. 原始 `TokenUsageIndicator` 计算未缓存输入和命中率并完成展示。

### 验证

- 后端 Qwen Provider、模型工厂、用量聚合与线程 API 定向回归：164 passed。
- 前端完整单元测试：626 passed。
- `pnpm check`：ESLint 与 TypeScript 检查通过。
- 新增 Playwright E2E 已被测试发现器识别，覆盖原始 Token 按钮与下拉框中的缓存数、未缓存输入和命中率。
- 本机 E2E 运行环境缺少 Playwright Chromium；改用系统 Chrome 时，正在运行的 Next 开发实例启用了登录且占用项目开发锁，未停止用户现有调试服务。该限制属于本地运行环境，不是断言失败；E2E 会在标准安装浏览器且以 `DEER_FLOW_AUTH_DISABLED=1` 启动的测试环境执行。
