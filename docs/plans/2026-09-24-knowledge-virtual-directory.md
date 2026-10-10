# DeerFlow 知识库虚拟目录与 Bash 检索实施计划

> **已被替代（2026-10-09）**：本页是历史方案，不再作为开发/部署依据。旧知识库 Harness 模块、目录注入和 Bash 拦截已移除。当前只通过外置 VSSAG MCP 检索，Bash/文件工具沿用默认实现，见 [MCP-only 计划](2026-10-09-vssag-mcp-only.md) 和 [部署指南](../guide/vssag-mcp.md)。下文完成项仅为历史记录，不代表旧入口仍可用。

## 目标与边界

- [X] 1.1 只改 DeerFlow 对接层；不读取、复用或修改 ownmindforge 的 `blockfs` 业务代码。
- [X] 1.2 把外部知识库 SDK 视为可选扩展依赖，Harness 未安装该 SDK 时仍可正常导入和运行其他功能。
- [X] 1.3 约定 SDK 公共调用边界：
  - `from vssag import VSSAGAccessContext, VSSAGClient`；
  - `VSSAGClient.from_env()` 创建 client；
  - `await client.build_vfs(access_context, mount_root=root)` 返回 tree 文本；
  - `await client.restore_bash(command, access_context, mount_root=root)` 接收完整命令并返回终端响应。
- [X] 1.4 DeerFlow 只保存 operator 配置的逻辑根 `/home/mnt/knowledge`，不拼接 SDK 自己管理的子目录。
- [X] 1.5 不新增 Sandbox mount、`PathMapping`、`ThreadState` 字段、跨请求 cwd 状态或额外的运行状态模型。

## 目录注入

- [X] 2.1 在 `deerflow.knowledge.directory` 增加通用 `KnowledgeService`，通过插件配置读取 `root`、`tree_max_chars` 和 `timeout_seconds`。
- [X] 2.2 仅在插件启用时惰性导入 `vssag`；不把 SDK 加入 `deerflow-harness` 必选依赖。
- [X] 2.3 使用 `KnowledgeDirectoryMiddleware` 注册到 `Placement.MODEL_PHYSICAL`，同时覆盖 Lead 和 Subagent。
- [X] 2.4 每次物理模型调用前读取当前 runtime 身份，调用 `build_vfs`，并把返回 tree 作为隐藏临时 `HumanMessage` 注入当前 `ModelRequest`。
- [X] 2.5 注入前移除本 middleware 上一次生成的临时消息，避免多轮模型调用重复累积。
- [X] 2.6 目录消息只存在于本次模型请求，不写入状态、checkpoint 或用户可见历史。
- [X] 2.7 复用现有非可信内容标签清洗；目录树超过上限时只截断文本，不在 DeerFlow 重新枚举目录。
- [X] 2.8 异步路径等待 SDK 方法，同步路径使用现有同步异步工具桥接；DeerFlow 同步初始化在线程执行。真实 SDK 内部 I/O 与跨 loop 资源仍需补齐，见第 8 节。
- [X] 2.9 不修改 `DynamicContextMiddleware`、Uploads middleware 或 Lead/Subagent 主循环。

## 身份与权限

- [X] 3.1 访问上下文只从服务端 runtime 读取 `user_id`、`user_role`、`authz_attributes`、`thread_id`、`run_id`。
- [X] 3.2 `user_id` 使用现有 `resolve_runtime_user_id`，角色缺省复用现有授权配置的 `default_role`。
- [X] 3.3 客户端消息、Bash 参数和普通配置不能覆盖 operator 根目录或服务端身份字段。
- [X] 3.4 每次目录构建和每次 Bash 查询重新构造访问上下文；tree 不是权限证明。
- [X] 3.5 由外部 SDK 根据访问上下文生成用户可见目录并执行最终 ACL；DeerFlow 不按 `user_id` 拼接目录，也不复制一套知识库 ACL。
- [X] 3.6 缺少 `thread_id` 或 `run_id` 时当前调用失败并向模型返回短错误，不使用共享默认 cwd 或跨用户状态。

## Bash 路由

- [X] 4.1 使用 `KnowledgeCommandMiddleware` 注册到 `Placement.TOOL_RAW`，在真实 Bash callable 前短路。
- [X] 4.2 只识别工具名 `bash` 和配置 root 的路径边界，不做 Shell 词法解析、管道拆分、重写、路径转换或 SQL 拼接。
- [X] 4.3 命中 root 后将完整原始 `command` 原样传给 SDK，包括引号、管道、`&&`、参数顺序和重定向文本。
- [X] 4.4 命中 root 的命令即使被 SDK 拒绝，也不回退真实 Sandbox；未命中的命令继续走原有 Bash。
- [X] 4.5 不修改 `bash_tool`、Sandbox provider、Sandbox 授权入口或物理路径映射。
- [X] 4.6 DeerFlow 同步与异步 hook 都覆盖，多个 tool call 保留各自 command 和 `tool_call_id`；真实 SDK 的跨 loop 与并行 cwd 不在该 hook 覆盖结论内。
- [X] 4.7 依赖完整绝对路径或同一 command 内自带 `cd`；跨工具调用 cwd 不由 DeerFlow 保存。

## 结果反馈

- [X] 5.1 将 SDK 终端响应转换为普通 `ToolMessage`，沿用原 `tool_call_id`。
- [X] 5.2 成功结果把终端文本放入 `ToolMessage.content`；拒绝结果使用 `status="error"`，不泄露内部异常细节。
- [X] 5.3 保留有限的路由状态、模式和路径元数据到现有 `additional_kwargs`，不新增前端事件协议。
- [X] 5.4 SDK 异常、超时和权限失败只影响当前工具调用，外层现有错误、预算、进度、收据和 SSE 链继续处理。
- [X] 5.5 LangGraph 将 `ToolMessage` 写回当前消息链，下一次模型调用按普通工具反馈读取结果。

## 测试与文档

- [X] 6.1 使用测试内存模块模拟 `vssag` 公共入口，不在 DeerFlow 仓库添加假的 SDK 包。
- [X] 6.2 覆盖 root 边界、原始 command、root 原样传递、用户上下文、临时消息去重、同步/异步调用、错误不回退和并行 tool call。
- [X] 6.3 覆盖 Lead/Subagent 语义位置和旧 `client_factory` 配置不再接受。
- [X] 6.4 更新 `config.example.yaml`、`README.md` 和 `backend/AGENTS.md` 的配置与架构说明。
- [X] 6.5 运行知识库专项、blocking-I/O、backend 默认套件和 lint；完整套件未全通过，结果与已有失败记录在评审文档 9.5。
- [ ] 6.6 真实 SDK 与全部必要验证通过后等待用户安排提交、合并；当前保留需求分支中的可评审改动。

## 明确不做

- [X] 7.1 不在 DeerFlow 中实现 SDK 的目录生成、文档读取、grep 解析或 ACL 规则。
- [X] 7.2 不把知识库目录挂载到 Local/AIO/E2B/OpenSandbox。
- [X] 7.3 不把 provider 名称写入 DeerFlow 自己的 middleware、service、配置字段或函数命名；唯一的 provider 导入只出现在可选 SDK 调用边界。
- [X] 7.4 不新增第二套消息反馈、前端协议、目录缓存或持久化 cwd。

## 8. 2026-10-08 真实 SDK 接入核验

- [X] 8.1 核验复制 SDK 的公共入口、根目录、tree、授权字段和终端响应契约。
- [X] 8.2 使用真实 SDK 与内存索引验证目录注入、权限裁剪、完整指令传递和结果反馈；Bash 成功恢复仅替代缺失的语法 Worker。
- [X] 8.3 补齐 DeerFlow 可独立完成的配置、内容边界和回归测试。
- [X] 8.4 复现缺失 Worker、阶段配置、并行 cwd 和 grep -F 问题；分析事件循环与大规模检索限制，记录源码位置和 SDK 修改要求。
- [X] 8.5 更新评审与公共入口文档，记录已通过测试与尚未完成的真实链路。

第 8 节已完成的是核验和 DeerFlow 可独立开发的部分；真实 Worker、跨 loop SDK 生命周期、大规模查询与 SDK 语义缺口尚未完成，不能宣称整个知识库功能已验收。
