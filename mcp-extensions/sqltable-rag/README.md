# sqltable-rag MCP Server

这个目录提供 TableRAG 的外部启动边界。核心检索实现暂时复用
`backend/packages/harness/table_rag`，但启动配置、传输方式和 Docker 生命周期已经
与 DeerFlow Gateway 分离，后续可以直接替换为独立的 TableRAG 服务实现。
