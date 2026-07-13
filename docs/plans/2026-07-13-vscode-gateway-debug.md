# VS Code Gateway 调试配置计划

## 1、现状确认

- [X] 1.1 阅读根目录与后端开发规范。
- [X] 1.2 核对 `docs/guide/local-manual-start.md` 中的 Gateway 手动启动参数。
- [X] 1.3 确认仓库当前没有共享的 `.vscode/launch.json`。

## 2、调试配置开发

- [X] 2.1 从 `dev` 创建 `chore/vscode-gateway-debug` 分支。
- [X] 2.2 新增仅启动 Gateway 的 VS Code Python 调试配置。
- [X] 2.3 保持工作目录、环境变量、Uvicorn 模块与手动启动方式一致。
- [X] 2.4 调整忽略规则，仅共享 `.vscode/launch.json`，继续忽略其他本地 VS Code 配置。

## 3、文档与验证

- [X] 3.1 在本地手动启动手册中补充 VS Code 调试使用方式。
- [X] 3.2 校验 `launch.json` JSON 语法及引用路径。
- [X] 3.3 使用配置对应的 Python/Uvicorn 参数执行启动冒烟验证；Gateway 导入被当前工作区缺失的 `deerflow.skills.skillscan.orchestrator` 阻断，已记录为非本次配置问题。
- [X] 3.4 完成 Review 文档并检查最终变更范围。
