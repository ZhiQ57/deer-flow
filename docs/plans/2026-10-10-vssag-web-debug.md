# VSSAG 网页工具缺失排查

## 1、确认运行状态
- [X] 1.1 用户已修复 PostgreSQL 未启动问题；沿用当前需求分支，读取已打开会话与扩展配置。
- [X] 1.2 核对 Gateway 启动和本轮日志：VSSAG 发现阶段连接关闭，被跳过，缓存为 0 个 MCP 工具；独立复现确认 SDK 配置缺少 DASHSCOPE_API_KEY。

## 2、定位并修复
- [X] 2.1 复现缺少 DASHSCOPE_API_KEY；实际 JSON 显式转发两个已有 DashScope 变量，独立 MCP 和 DeerFlow 工具发现均得到三个工具。不修改捆绑 SDK。
- [X] 2.2 保持权限和工具流程；真实启动测试改用实际配置，1 passed，Ruff 通过；指南补充 stdio 环境转发及连接关闭排障。

## 3、网页验收
- [X] 3.1 实际 Gateway 运行成功；四次目录注入审计均为 injected，目录/搜索/读取的工具反馈均为 success。
- [X] 3.2 当前浏览器闭环已验证；文档索引只读计数为 0，不能验收有命中问答。Review 记录数据限制及已有回归失败，指南更新完成。
