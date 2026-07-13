# VS Code Gateway 调试解释器修复计划

## 1、问题定位

- [X] 1.1 确认 VS Code 实际使用系统 Python 3.11，而不是 `backend\.venv`。
- [X] 1.2 检查 Python Debugger 日志中的解释器解析过程。
- [X] 1.3 确认 `python` 已解析为项目虚拟环境，但 Debug Adapter 与 Debug Launcher 仍回退到当前全局解释器。

## 2、配置修复

- [X] 2.1 从 `dev` 创建 `fix/vscode-gateway-debug-interpreter` 分支。
- [X] 2.2 显式指定 Debug Adapter 和 Debug Launcher 使用 `backend\.venv`。
- [X] 2.3 补充工作区默认 Python 解释器配置，统一编辑、终端与调试环境。
- [X] 2.4 更新 `.gitignore`，只共享必要的 VS Code 工作区配置。
- [X] 2.5 修复阻断 Gateway 导入的 SkillScan orchestrator 误删除回归，统一使用包入口的保守扫描实现。

## 3、文档与验证

- [X] 3.1 更新本地启动手册中的解释器排错说明。
- [X] 3.2 校验 VS Code 配置语法和解释器路径。
- [X] 3.3 验证 debugpy 使用项目虚拟环境启动。
- [X] 3.4 验证 Gateway 启动与健康检查。
- [X] 3.5 完成 Review 文档及最终检查。
