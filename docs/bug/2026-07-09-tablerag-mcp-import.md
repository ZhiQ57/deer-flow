# TableRAG MCP No module named 'table_rag' 排查记录

## 现象

DeerFlow 启动 DataAgent 后，TableRAG MCP 未加载成功，日志出现 No module named 'table_rag'。

## 根因

本地 extensions_config.json 中：

`json
"command": "python"
`

在当前 Windows 环境解析到 Conda/System Python：D:\miniconda3\python.exe。该解释器无法导入 DeerFlow 后端虚拟环境中的 ackend/packages/harness/table_rag，因此 MCP 子进程启动失败。

## 修复

将实际运行配置改为后端虚拟环境 Python：

`json
"command": "D:\\A-PythonWork\\AOpenGithub\\deer-flow\\backend\\.venv\\Scripts\\python.exe"
`

同时清空未实现的示例 interceptor：

`json
"mcpInterceptors": []
`

## 验证

- ackend\.venv\Scripts\python.exe -c "import table_rag.mcp" 成功。
- ackend\.venv\Scripts\python.exe -m table_rag.mcp --help 成功。
- deerflow.mcp.tools.get_mcp_tools() 成功加载 10 个 	ablerag_* 工具。
