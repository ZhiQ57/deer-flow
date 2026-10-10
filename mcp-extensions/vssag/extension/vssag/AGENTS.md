# AGENTS.md

本文档为 `examples/deerflow-vssag-mcp/extension/vssag` 包的AI开发规范文档。

SDK 与 MCP 服务共置于 `examples/deerflow-vssag-mcp/` 独立开发目录；
服务 Python 包为 `vssag_mcp_server`，SDK 仍使用 `vssag` 导入名。
服务安装与启动使用该目录自己的虚拟环境，不向 DeerFlow Harness 添加业务依赖。

vssag 全称为虚拟系统搜索增强生成( Virtual System Search Augment Generate ), 字面意思为使用虚拟文件系统为智能体提供自主检索服务.

`vssag` 被定义为 "通用文档解析 + 通用文档检索" 能力SDK, 不关联任何应用层业务. **主要负责**:

- 基于 MinerU-Local 或者 MinerU-Server API 实现目标文档, 解析成标准化Markdown.
- 文档标准化、章节切分、通用内容单元生成, 还原原始文档效果.
- Embedding 生成.
- 关键词、全文、向量、混合检索.
- 引用关系和实体关系检索.
- 检索结果融合、重排序、出处信息
- LanceDB / pgvector 存储原始适配

## vssag 目录结构

```text
examples/deerflow-vssag-mcp/extension/
├── pyproject.toml              # vssag 独立依赖包
└── vssag/
    ├── __init__.py
    ├── client.py               # 外部使用 vssag sdk 封装入口
    ├── mcp.py                  # vssag-mcp 服务端
    ├── config.py               # 统一加载配置类
    ├── errors.py               # 错误信息枚举
    │
    ├── vssag_schemas/          # 内部统一通用数据模型(持续优化)
    │   ├── index/              # 索引相关
    │   ├── parse/              # 解析器相关
    │   ├── retriever/
    │   ├── graph/
    │   ├── .../
    │   └── vfs/
    │
    ├── providers/
    │   ├── embedder_prov.py    # OpenAI SDK Embedding 兼容适配
    │   ├── rerank_prov.py      # 百炼/火山等厂商原生重排序适配
    │   ├── llm_prov.py         # OpenAI SDK Chat 兼容适配
    │   └── sql_prov/           # PostgreSQL/MySQL/SQLite Schema 采集连接器
    │
    ├── parsing/
    │   ├── parse_pipeline.py   # 解析器内部统一入口
    │   ├── mineru/
    │   │   ├── schemas.py      # MinerU 解析结果响应模型
    │   │   ├── loaders/        # 加载待解析文档
    │   │   │   ├── markdown.py
    │   │   │   ├── pdf.py
    │   │   │   ├── excel.py
    │   │   │   ├── pptx.py
    │   │   │   ├── ...
    │   │   │   └── docx.py
    │   │   ├── server/         # 调用 MinerU 服务端方法
    │   │   │   └── client.py   # MinerU HTTP 客户端
    │   │   ├── local/          # 本地集成MinerU方法
    │   │   ├── utils/          # MinerU解析适配工具
    │   │   │   ├── content_list.py         # content_list 内容解析
    │   │   │   ├── heading_restore.py      # 标题还原
    │   │   │   └── markdown_normalize.py   # Markdown 文档重整(标准化)
    │   │   └── chunking/       # 文档切分依据
    │   │       ├── sections.py
    │   │       ├── title.py
    │   │       ├── paragraphs.py
    │   │       ├── image.py
    │   │       └── table.py
    │   └── 待加入其它解析器/
    │
    ├── indexing/                # 索引构建
     │   ├── index_enrichment/    # 索引增强策略/算法
    │   │   ├── prompts/
    │   │   │   ├── heading_correction.md
    │   │   │   ├── summary.md
    │   │   │   ├── keyword_extraction.md
    │   │   │   ├── reference_extraction.md
    │   │   │   └── entity_extraction.md
    │   │   ├── summaries.py         # 文档 / 章节 / 内容摘要
    │   │   ├── keywords.py          # 关键词生成
    │   │   ├── questions.py         # 关联问题生成
    │   │   ├── references.py        # 显式引用和引用候选
    │   │   └── entities.py          # 实体关系抽取
     │   ├── index_stores/        # 索引入库
     │   │   ├── lancedb_store.py
     │   │   ├── pgvector_store.py
     │   │   ├── postgres_graph_store.py
     │   │   ├── sql_schema_store.py
     │   │   ├── postgres_sql_schema_store.py
     │   │   └── memory_store.py
     │   ├── index_pipeline.py    # 通用索引数据模型构建流水线
     │   └── sql_pipeline.py      # SQL Schema 采集与索引流水线
    │
    ├── retrieval/
    │   ├── full_text/           # 全文检索包
    │   ├── vector/              # 向量检索包
    │   ├── graph/               # 图谱检索包
    │   ├── fusion.py            # RRF / 加权融合
    │   ├── rerank.py            # 重排序
    │   ├── citations.py         # 检索引用生成
    │   └── hybrid_pipeline.py   # 混合检索
    │
    ├── vfs/               # 虚拟文件目录（默认公开根 `/home/vssag`，可接收外部挂载根）
    │   ├── vfbuild/          # 构建用于检索的虚拟文件目录
    │   │   └── sql.py         # SQL Schema 虚拟目录
    │   └── vfsrestore/       # 两种检索还原：Tool-Use 与 Bash/Grep
    │       ├── sql.py        # SQL Schema Tool/Bash 还原
    │       └── sql_service.py
    │
    └── traces/                  # 集成 Langfuse + 文档解析入库 的全流程可视化观测
        ├── trace_models.py      # 轨迹日志模型
        └── tracing.py
```



## Review 规则

本 SDK 的配置和 Schema 改动必须遵守
[编码规范限制词 Review 规则](../../../../docs/全局编码规范文档-必须遵守.md)。
`vssag_schemas` 下每个文件只定义一个业务对象，配置只能通过 `VSSAGConfig.from_env()` 装配。

模型服务边界：

- OpenAI 及 OpenAI 兼容厂商的 Chat、Embedding 统一通过 `openai.AsyncOpenAI` 调用。
- 百炼、火山等厂商只有在原生 endpoint 或字段格式不同的地方保留适配代码。
- provider 不得使用 Hash 向量、空字符串响应或词法排序掩盖未配置和远端错误。
- 未配置真实模型服务时，启用对应能力必须快速失败；离线测试通过依赖注入测试 double 完成。
- LLM 扩展请求参数必须通过 `models.<provider>.extra_body` 配置传入，Provider 不得硬编码思考模式或其他厂商参数。
- 字段值 LLM 输出统一通过 `vssag.utils` 的 `json-repair` 适配层解析；格式或语义不完整只生成 pending，网络和超时错误才允许重试。

### KISS 与 helper 边界

- 先按检索、索引、VFS 安全和数据库事务的真实阶段理解代码，不以“超过 50 行”作为唯一拆分依据。
- 一次性字段投影、简单格式化和单调用转发优先内联；相同逻辑在至少三处复用，或内联会明显破坏复杂主流程时，才保留/新增 helper。
- SQL 表名脱敏、虚拟路径越界校验、模型输出校验、连接关闭和事务边界属于安全或资源生命周期，不得为了减少函数数量删除。

## 检索分工

### 向量数据库选型

```text
生产环境：pgvector
本地开发 / 轻量部署 / 离线测试：LanceDB
```

检索方式的分工:
```text
文本检索 / BM25
├── PostgreSQL FTS / LanceDB FTS

向量检索
├── pgvector / LanceDB

图谱检索
├── PostgreSQL 关系表
└── LanceDB 模式下使用 SQLite 或轻量关系存储
```


### 引用关联图谱

参考 `llm_wiki` 的一跳扩展方式：

```text
基础候选 = 关键词检索 + 向量检索
图谱候选 = 基础候选的一跳引用邻居
最终结果 = RRF / 加权融合
```

1. 图谱结果占最终结果的 15%～30%;
2. 每个结果保留 `related_to`、`outgoing_links`、`backlinks`;
3. 图谱以文档引用边和实体关系边分层保存；显式引用优先，LLM 只生成待确认候选。

## 检索方式落位

```text
Tool Use
└── vssag.vfs.vfbuild 负责映射出虚拟文件目录
    └── 虚拟文件目录输入到大模型智能体上下文
        └── harness 输出检索 Tool-Use
            └── harness -> vssag.client
                └── vssag.vfs.vfsrestore 提供结构化检索能力

Grep / Bash
└── vssag.vfs.vfbuild 负责映射出虚拟文件目录
    └── 虚拟文件目录输入到大模型智能体上下文
    └── harness 输出检索 Bash
    └── app -> Bash 拦截器(vssag.vfs.vfsrestore)
                └── just-bash Worker 只解析公开指令
                    └── vssag.vfs.vfsrestore 负责路径恢复、权限过滤和数据库查询
```

因此 `vfsrestore/` 同时包含结构化 Tool-Use 还原器和受限 Bash/Grep 还原器；它不执行真实文件系统写入，也不负责 Agent 决策。
VSSAG 的 Bash Worker 不接收用户标识、内部隔离标签或文件快照，模型始终只使用公开虚拟路径。

### 虚拟目录树路径契约

- 树首行必须保留完整公开目录路径，如 `/home/mnt/knowledge/vssag/`；
  不能只显示末级目录名，否则模型无法据此继续执行 Bash 检索。
- SQL 默认树从模块挂载根展开，已启用的 SQL 能力位于 `sql/` 分区。
  `sql_ability.enabled=false` 时不构建 SQL 文件视图或展示该分区。
- 显式 `tree /home/mnt/knowledge/vssag/sql` 保留完整 SQL 子目录根；
  模块根不能重定向为 SQL 子目录。深度参数相对于本次展示目录计算。
- 能力分区来自已启用能力的真实文件视图；不为尚未实现的图像、记忆能力
  生成可见占位目录。内部用户标签保持不可见，每目录展开上限与摘要仍生效。
- SQL `grep`/`rg` 使用树中同一份权限裁剪文件视图，返回可读取的完整公开路径和行号。

## SQL Schema Grounding 边界

SQL Schema 能力的设计依据为 [`docs/overview/项目设计7-SQL Schema召回.md`](../../../../docs/overview/项目设计7-SQL%20Schema召回.md)。该能力属于 VSSAG 的通用 Schema grounding 扩展，不等同于完整 Text2SQL Agent。

- SQL 表的业务说明、字段描述、主键、可为空、默认值、注释和已采集的枚举值统一放在 `desc.md`，每个字段只渲染一行。
- 模型查看表用途和字段语义时读取或搜索 `desc.md`；禁止生成、恢复独立 `columns.md`，也不得新增指向该文件的读取指令、注释或示例。
- `ddl.sql` 展示源表的 DDL 定义与精确结构，按现有表名保护配置使用真实或公开表名；字段语义说明仍由 `desc.md` 承载。
- 内部 `table.columns` 是采集、索引和权限裁剪需要的结构化元数据，不代表需要生成独立字段文件。

- VSSAG 负责 SQL 表结构、字段值、业务证据和表关系的采集、索引与检索。
- SQL DDL、描述、关键词、向量、图谱和 VFS 文件均服务于 SQL Schema 召回。
- SQL 生成、SQL 执行和业务决策由 App/Harness 负责，VSSAG 不执行 SQL。
- 物理外键关系直接采集；模型推断和线上交互关系按 candidate 保存，确认后可更新为 validated。
- `vssag.index.sql_ability.security.allow_real_table_names=false` 时，提供给模型的 DDL、描述、图谱和工具结果只使用随机表名。
- SQL 索引表使用 `docs/sql/` 中的 DDL 与 `scripts/sql/` 中的迁移脚本维护。

## SQL Schema 实现入口

SQL Schema 能力通过 `VSSAGClient` 的显式方法使用：

- `collect_sql_schema()`：调用配置中的 SQL 连接器，采集表、字段、外键、索引、采样值并写入 SQL 索引。
- `VSSAGClient.from_env(setting_overrides=...)`：从默认 YAML Setting 创建当前任务专用客户端，并应用一次性运行时覆盖。
- `python -m vssag.sql_collect` / `vssag-sql-collect`：独立执行一次 SQL Schema 采集；业务层 Celery 任务必须复用同一 Client 入口。
- `search_sql_schema()`：检索表结构、字段值、业务证据和关系图谱。
- `restore_sql_tool()` / `restore_sql_bash()`：还原 SQL 虚拟目录的 Tool Use 与受限 Bash/Grep。
- `record_sql_feedback()`：接收 Harness/App 提供的交互反馈，生成 candidate 关系。
- `update_sql_graph()`：将 candidate 关系更新为 validated 或 disabled。
- `indexing.index_enrichment` 提供提示词驱动的 SQL 实体、关键词和 QA 增强入口；
  提示词统一从 `vssag/prompts/` 加载。

SQL 连接器支持 PostgreSQL、MySQL 和 SQLite；SQLite 用于本地开发与单元测试。
SQL 索引存储支持 SQLite 和 PostgreSQL，向量索引按配置维度创建。

## 字段值自动索引

`target_sources.field_value_index.mode` 支持 `manual` 和 `auto` 两种模式。
`manual` 只采集 `value_columns` 指定的表字段；`auto` 先计算字段统计信息和
索引得分，再对通过阈值的候选字段采值。字段值必须经过敏感值和技术标识硬拒绝。
`manual` 不执行字段评分和 LLM 判断。三种 Provider 必须使用一致性只读扫描，
并执行 `query_timeout_seconds` 查询超时与 `page_interval_seconds` 分页节流。

`nl2sql_field_value_indexs` 严格只允许设计文档中的字段：
`id/database_id/schema_name/raw_value/raw_table_name/random_table_name/column_name/`
`aliases/retrieval_text/created_at/updated_at`。任务、统计、CDC、评分、归一化、
敏感标记和生命周期状态不得加入主表；已入库字段值的附属数据必须通过
`value_index_id` 外键关联。

一次采集运行命名为字段值采集任务，表名固定为
`nl2sql_field_value_collection_jobs`。禁止新增含义模糊的 `policy`、`profile`、
`handler`、`processor`、`manager` 等文件、类型、变量或函数名。完整交付必须
包含 PostgreSQL WAL、MySQL Binlog、Kafka、Redis Stream 和 LLM 语义标注，
不得以空壳或后续扩展替代。

