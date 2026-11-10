# 过程评测-SQL Schema Recall 评测
端到端评测只能测试业务执行效果, 无法定位到具体优化点, 为了评估 DataAgent 具体模块的性能, 从研发角度增加过程评测.

按照 DataAgent 执行过程中涉及模块划分, 总分类如下:
- SQL Schema Recall 评测
- 意图评测
- 工具调用评测
- Skill评测
- 服务稳定性评测
- ...
> 初始阶段无法概括所有用例, 后续继续补充.

## 1.SQL Schema Recall 评测

SQL Schema Recall MCP Server 作为一个独立的检索组件，需要建立单独的 Benchmark 评测体系，用于衡量 Schema 检索能力，并在 DataAgent 端到端评测性能不佳时，区分检索器、Agent 工具使用和 SQL 生成环节的问题。

**(1) 工具说明**

MCP Server 开放 `fast_search(query: String)` 作为统一检索入口，Agent 通过传入关键词、业务指标名称或自然语言问题，获取相关数据库表结构、字段信息和表关联关系.

注意: 用于评估的结构化结果从轨迹日志或Debug记录获取, 工具执行响应结果是给 Agent 看的文字描述版.

该工具采用多路混合检索方案，内部包含 BM25 关键词检索、pg_trgm 模糊匹配、向量检索、字段值检索、RRF 多路排序融合以及 Join Graph Expansion 关联图扩展等能力，最终返回经过融合的候选 Schema。

**(2) 评估策略**

**A.单体检索评测**

直接调用 MCP Server 的  fast_search(query: String) 工具，不经过 DataAgent 和其他 Agent 推理环节，以此隔离 Agent 的问题，单独评估 MCP Server 的检索能力。

测试样本集需要覆盖多种检索场景，分别评估不同业务表达和 Schema 关联结构下的召回表现。

测试样本建议包含以下类型：

```csv
测试类型,测试目标,样本比例,困难等级
表/字段注释直接召回,业务概念与表/字段定义匹配程度,20%,easy
字段值召回,业务值与数据库存储值不一致,25%,medium
多表关联召回,关联图扩展能力,25%,medium
长尾表达召回,术语化/缩写/英文混合,10%,hard
歧义消解测试,同义词/多义词分辨,10%,hard
负样本测试,空结果正确返回,10%,easy
```
困难等级解释：
```csv
困难等级,评判标准
easy,表名/注释与 query 完全匹配
medium,需要字段值映射或单层 Join 扩展
hard,需要深层图扩展 + 字段值联合
```

注：上述比例为初始样本分配建议。

**测试样本集数据结构（统一格式）**：
- query: 查询条件 query、业务指标、口语表达。
- target_schema：必要 Schema，生成正确 SQL 必须用到的表、字段和关联关系。
- relevant_schema：候选 Schema，虽然不是生成 SQL 的必要条件，但对理解业务、探索数据有帮助。

```json
{
  "id": "sr-001",
  "query": "治疗周期",
  "target_schema": {
    "tables": [
      {
        "name": "treatment_cycle", "comment": "治疗记录表"
      },
      {
        "name": "patient", "comment": "患者信息表"
      }
    ],
    "columns": [
      {
         "table": "treatment_cycle", "column": "patient_id", "comment": "患者ID"
      },
      {
         "table": "patient", "column": "id", "comment": "序号ID"
      },
      {
         "table": "patient", "column": "name", "comment": "患者姓名"
      }
    ],
    "joins": [
      {
         "table": "treatment_cycle",
         "column": "patient_id"
         "join": [
           {
             "table": "patient",
             "column": "id"
           },
           // ... 主表关联更多表
         ]
      }
    ],
    "value": [
      {
        "raw": "治疗周期",
        "target": ""
      }
    ]
  },
  "relevant_schema": {
    "tables": [],
    "columns": [],
    "joins": []
  }
}
```
其中：
- Recall 以 required_schema 为标准。
- Precision 以 relevant_schema 为标准。


**样本集质量验收标准**

a.覆盖率:
- 每个物理表至少出现在 2 个样本中
- 每个字段至少出现在 1 个样本中
- 每对关联表路径至少出现在 1 个样本中

b.分布均衡:
- easy/medium/hard 比例 ≈ 30%/40%/30%
- 单类测试类型 ≥ 10 条样本

c.标注一致性:
- 由≥2 人独立标注 target_schema
- Cohen's κ > 0.8 才采纳

d.负样本验证:
- 负样本需经人工复核无匹配可能
- 占总量 10%-15%

e.最小样本量：≥100 条

--- 

**B.对比消融实验**

除了评估 MCP Server 的最终检索结果，也要保留每个召回模块的中间结果，以便分析不同检索策略对最终召回效果的贡献。

例如：同一条问题最终漏掉了 `treatment_cycle.patient_id`，应该要追溯：

- BM25 有没有找到？
- pg_trgm 有没有找到？
- 字段值检索有没有提供有效线索？
- RRF 融合时有没有将其过滤？
- Join Graph 扩展时有没有补充？

各模块的中间结果通过服务端 Trace 日志或评测专用 Debug 模式记录，不使用工具响应结果作为评估目标。

以下是消融实验组：

```csv
实验组,检索配置,验证目标
A,BM25,关键词检索基准
B,pg_trgm,模糊匹配能力
C,向量检索,语义匹配能力
D,BM25 + pg_trgm + 向量检索，不使用 RRF,多路检索基础效果
E,D + RRF,多路排序融合增益
F,E + 字段值检索,业务值与数据库字段映射增益
G,F + Join Graph Expansion(完整方案),关联关系扩展增益
```
另外需要消融实验：
1. 图扩展深度控制: Join Graph 扩展 1 层 vs 2 层 vs 3 层的 Recall-Precision 权衡？
2. RRF vs 分数归一化: RRF 是否真的优于简单的线性加权融合？

注：D 组采用候选集合合并及统一分数排序，E 组使用 RRF，保持检索通道相同，以便单独比较 RRF 的贡献。如果实际生产环境没有向量检索，则删除 C 组，并从 D、E 组中移除向量检索。
通过消融实验，可以回答：
- pg_trgm 相比 BM25 究竟提高了多少召回率？
- 向量检索是否带来了关键词匹配之外的语义召回增益？
- RRF 是否改善了多路结果融合效果？
- 字段值召回是否解决了传统 Schema 检索遗漏的问题？
- Join Graph Expansion 是否增加了必要关联字段？
- 图扩展是否造成了过多无效 Schema？

**(3) 评估指标**

针对 `fast_search(query)` 唯一工具输出的最终结果，分别评估召回完整性和检索精确性。

**A.召回完整性**

```csv
指标,计算对象,评价目的
Table Recall@K,必要表集合,衡量表级召回能力
Column Recall@K,必要字段集合,衡量字段级召回能力
Join Recall,必要关联关系集合,衡量表关联召回能力
Value-Column Recall,业务值对应的必要字段,衡量字段值本身匹配准确率 (如口语"住院"→DB 值"HOSPITALIZATION")
nDCG@K,排序质量指标,综合排名质量的指标
MRR (Mean Reciprocal Rank),排序质量指标,首个必要 Schema 的平均排名倒数
```

Table Recall@K 和 Column Recall@K 分别统计候选表、候选字段在指定排名范围内的召回率。

例如：
- Table Recall@3、Table Recall@5、Table Recall@10
- Column Recall@K，根据字段候选数量及 Token 预算确定 K。
其计算方式：

$$
Recall@K=\frac{|R_K\cap G|}{|G|}
$$

其中，(R_K) 为排名前 K 的召回结果，(G) 为必要 Schema 集合。

> TODO: 
> 1. nDCG@K 公式补充
> 2. MRR 公式补充

> TODO:
> 3. Value-Column Recall计算方法未定义
> 核心问题:
> - 如何判定一个"业务值→DB 值"的映射是正确还是错误?
> - 如果一个 query 涉及 3 个字段值映射，2 个正确 1 个错误，准确率是 66.7% 还是 0%?

**B. 检索精确性**

```csv
指标,计算对象,评价目的
Table Precision,返回的候选表,衡量表召回噪声
Column Precision,返回的候选字段,衡量字段召回噪声
Value Match Accuracy,字段值匹配结果,衡量数据库真实存储值映射准确性
```

Precision 根据 relevant_schema 计算，以避免将有价值但非必要的辅助 Schema 误判为噪声。

$$Precision=\frac{|R\cap Relevant|}{|R|}$$


**C.稳定性能指标**

```csv
指标类型,具体指标,建议阈值,开展必要性
时间性能,P50/P95/P99延迟,<200ms(P95),必要
稳定性,连续 100 次调用错误率,<0.1%,必要
资源消耗,CPU/内存峰值,<500MB,非必要
可扩展性,Schema 规模×10 的 Recall 衰减,<10%,非必要
```

> 如果召回率高但延迟 2s+,在 Agent 实时交互中会超时.

---

## 补充：基于 RAGAS 的正式评测接入方案（2026-11-06）

本节补充前文 SQL Schema Recall 的框架选型、数据规范和实施要求。正式评测全面采用 RAGAS；移除前期自行编写的独立评测引擎，后续数据集按本节规范设计和调整。前文正文保留，涉及落地格式、指标实现和评测入口的细节，以本节补充为准。

本节记录后续迁移方案，不代表现有代码已经完成 RAGAS 接入。前期合成测试结果只用于验证原有实现，不能作为 RAGAS 正式 Benchmark 的结果。

### 1、框架与版本约定

1. 以已核对的 RAGAS `v0.4.3` 作为首期接入基线，实施时固定依赖及锁文件，报告记录实际版本。后续升级需要重新验证 API、指标和数据兼容性，不自动跟随浮动版本。
2. 使用 RAGAS `@experiment` 组织正式实验，通过框架执行原生指标和按其扩展接口注册的领域指标。停止维护 RAGAS 之外的第二套评分引擎，不采用“原有脚本先计算，再把分数包装成 RAGAS 结果”的接入方式。
3. 表、字段等具有明确身份的对象优先使用原生 `IDBasedContextRecall`、`IDBasedContextPrecision`。它们直接比较 ID，无须 LLM 裁判；字段含义相近不能代替实际字段命中。
4. `v0.4.3` 的 ID 指标仍提供 `ragas.metrics` 下的 `SingleTurnSample` 接口；新实验通过少量适配调用这些真实指标，不假设它们已经迁移到 `ragas.metrics.collections`。领域扩展采用该版本正式支持的指标接口，例如 `@numeric_metric` 或 collections 的 `BaseMetric/MetricResult`，按实际需要选择，不重复实现两种扩展。
5. 本次评价检索组件，不要求生成答案。Faithfulness、Answer Relevancy 等依赖答案的指标不纳入本阶段主指标，也不为满足接口而伪造 `response` 或标准答案。

“采用 RAGAS”说明执行框架和通用指标来源；SQL Schema 专用标注与扩展指标仍属于本项目方法，报告必须公开其定义，不将其描述为 RAGAS 内置或官方认证指标。

### 2、替换范围与保留能力

| 范围 | 迁移要求 |
| --- | --- |
| 独立评分代码 | 移除原 `recall_metrics.py` 的独立评分路径；通用指标调用 RAGAS 原生实现，必要的领域算法迁入框架注册指标 |
| 原评测编排与入口 | 替换原 `recall_runner.py`、`run.py` 以及配套配置、数据加载和依赖中的旧引擎逻辑；不保留可选的旧评分后端 |
| 项目适配代码 | 仅承担 MCP 调用、SDK 日志读取与校验、身份归一化、实验配置和 CI 参数接入；由 RAGAS 实验执行评分并保存实验结果 |
| SDK 与 MCP | 保留 `fast_search` 多路召回、纯文本响应、`vfssql.logs` 配置及结构化终态日志，不因更换评测框架而移除产品能力 |
| 证据与生命周期 | 保留独占日志、调用关联、证据冻结、文件哈希、只读索引和本次子服务进程清理要求 |
| 主副本与分发副本 | 保留 `backend/evaluate/vfssql-recall-cicd/` 为主副本、`backend/mcp-extensions/vfssql-sdk-server/evals/` 为运行副本，二者同步替换，不留下旧引擎副本 |
| 测试及使用文档 | 替换针对旧评分引擎的测试和命令说明；保留并适配 SDK、MCP、日志与证据有效性的行为回归 |

RAGAS 依赖放在评测侧，不引入 SDK/MCP 生产检索路径；独立复制评测目录时安装其明确锁定的评测依赖。必要的领域指标实现属于框架扩展，不再另建独立的指标调度、评分注册或实验存储体系。

### 3、标准数据集与 Schema 标注

#### 3.1 原生字段与领域扩展

采用 UTF-8 JSONL 作为标准交换格式，每行一条原始评测样本，加载为 RAGAS 实验数据集。格式由 **RAGAS 原生字段 + 显式 Schema 领域扩展字段** 组成；RAGAS 本身没有统一规定 SQL Schema 的必要/辅助项、JOIN 和值映射格式。

| 字段 | 来源与含义 |
| --- | --- |
| `id` | 项目扩展：数据集内唯一的样本标识；重复问题允许存在，但 ID 不重复 |
| `user_input` | RAGAS 原生：问题文本，替代旧数据中的 `query`；调用 MCP 时仍映射到工具参数 `query` |
| `reference_context_ids` | RAGAS 原生：人工标注的必要表、字段 ID；分别生成表级、字段级评分视图 |
| `reference_contexts` | RAGAS 原生可选字段：确有文本类评价需要时提供对应标准上下文，本期 ID 评分不强制要求 |
| `schema_annotations.relevant_context_ids` | 项目扩展：辅助相关表、字段 ID，保留前文 `relevant_schema` 的业务语义 |
| `schema_annotations.joins` | 项目扩展：必要 JOIN 的两个字段端点，使用明确的字段 ID |
| `schema_annotations.values` | 项目扩展：必要业务值映射，每项含 `raw`、`column_id`、`target`，其中 target 保留原始 JSON 标量类型 |
| `metadata` | 项目扩展：题型、难度、标注版本等分组信息 |
| `retrieved_context_ids` / `retrieved_contexts` | RAGAS 原生预测字段，由实际执行后的 SDK 日志生成，不作为人工预填的检索结果 |

领域扩展由实验样本模型显式接收和保留；构造原生 `SingleTurnSample` 时只传入其支持的字段，不能因模型忽略未知字段而丢失 JOIN、值映射或辅助标注。表、字段、JOIN 和值映射分别构造评分视图，不把它们混成一个比例后替代各项指标。

原始数据集只保存标注，运行产物保存预测。旧 `query/target_schema/relevant_schema` 数据需要按新规范转换并验收；前文示例保留为概念说明，不继续作为正式评测入口的机器输入协议。迁移完成后不在正式入口长期维护两套格式的隐式回退。

#### 3.2 身份与标注规则

- 表 ID 至少包含对象类型、数据库标识、Schema 和原始表名；字段 ID 再包含字段名。以原始身份评分，公开表别名由 SDK 日志 catalog 还原，不让随机别名变化影响结果。
- ID 采用规范化 JSON 元组字符串等无歧义编码；固定编码版本，区分大小写，不用未经转义的分隔符拼接名称。人工标注与日志适配使用同一身份规则。
- 必要项与辅助项分别保留。Recall 的参考集合为必要项；Precision 的可接受集合为必要项与辅助项的并集，避免把正确命中的必要项算成噪声。
- JOIN 端点必须指向实际字段；按无向端点对规范化去重，保留预测关系的 physical/validated/candidate 属性以便追溯。
- 业务值必须定位到具体字段。`raw` 表示问题中的业务表达，`target` 表示数据库原始值；字符串、数值、布尔值严格区分，不把 `"1"`、`1`、`true` 视为同一值。
- 无标准匹配项的负样本必须显式标注空集合；缺标注、越权对象、不存在的字段或身份歧义不能当作负样本或普通漏召回。

以下为人工合成的单行样本结构示意；落盘时序列化为 JSONL，不代表真实业务题目：

```json
{
  "id": "synthetic-schema-001",
  "user_input": "查找已付款订单的状态字段",
  "reference_context_ids": [
    "[\"table\",1,\"sales\",\"orders\"]",
    "[\"column\",1,\"sales\",\"orders\",\"status\"]"
  ],
  "schema_annotations": {
    "relevant_context_ids": [],
    "joins": [],
    "values": [
      {
        "raw": "已付款",
        "column_id": "[\"column\",1,\"sales\",\"orders\",\"status\"]",
        "target": "paid"
      }
    ]
  },
  "metadata": {
    "case_type": "value",
    "difficulty": "medium"
  }
}
```

前文关于覆盖率、样本量、分布、独立标注及一致性的质量要求继续适用。通过 RAGAS 数据加载或格式校验不等于数据集已满足这些人工质量要求。

### 4、指标映射与计算口径

| 对外报告指标 | RAGAS 实现方式 | 输入约定 |
| --- | --- | --- |
| Table Recall@K | 原生 `IDBasedContextRecall` | 前 K 个实际展示表 ID，对照必要表 ID |
| Column Recall@K | 原生 `IDBasedContextRecall` | 前 K 个独立排名字段 ID，对照必要字段 ID |
| Table / Column Precision | 原生 `IDBasedContextPrecision` | 分别对照必要项与辅助项的并集 |
| Join Recall | 优先将关系规范化为 ID，调用原生 ID Recall | 必要 JOIN 与最终实际展示 JOIN；两端字段均须实际可见 |
| Value-Column Recall | 优先将业务表达与字段绑定规范化为 ID，调用原生 ID Recall | 逐项核对 raw 与目标字段的绑定，不要求这一项已匹配正确数据库值 |
| Value Match Accuracy | 注册确定性领域指标 | 同一 raw、同一字段且原始值与 target 严格相等才算正确 |
| Table / Column nDCG@K、MRR | 注册确定性排序指标 | 保留独立表/字段排名及必要、辅助两级相关度 |
| 负样本空返回正确率 | 注册确定性领域指标 | 调用成功且表、字段、值、图关系均为空才记正确 |
| P50/P95/P99、错误率 | 实验记录与运行统计 | 从实际 MCP 调用账本采集，不以评分耗时替代服务延迟 |

RAGAS 的一般 `ContextPrecision` 包含相关性判断及排名影响，不等同于本方案的集合 Precision。本期明确使用 `IDBasedContextPrecision`；若后续增加文本相关性或 LLM 裁判，作为独立实验列出，不能静默替换主指标。

各项补充定义如下：

1. 默认 K 为 3、5、10；先按最终展示顺序去重，再截取 K 项。表级与字段级独立排序，不因返回一张表就推断该表全部字段已召回。
2. Precision 和 Recall 为同一问题生成不同参考集合的评分视图；不能用唯一的 `reference_context_ids` 同时充当必要项集合与可接受集合。不同视图复用同一次检索证据，不重复调用工具。
3. Value-Column Recall 的分母为必要的 raw→字段映射数量；Value Match Accuracy 的分母为必要的完整值映射数量。三个必要映射正确两个时均按对应口径计为 2/3，不采用全有或全无，也不以“只对已返回项评分”隐藏遗漏。
4. nDCG@K 中必要项相关度为 2，辅助项为 1，其余为 0；gain 为 `2^rel - 1`，discount 为 `log2(rank + 1)`。DCG 除以理想排序的 IDCG；必要与辅助重叠时取必要等级。MRR 取首个必要项的排名倒数，未命中记 0，跨样本按适用样本求平均。
5. Recall 的必要集合为空，或 Precision 没有预测项时，遵循原生指标的不可计算语义，在输出中规范为 N/A 或 null；必要集合非空但召回为空时 Recall 为 0，有预测但可接受集合为空时 Precision 为 0。负样本正确性由专门指标评价，不将空 Gold 的 Recall 伪造为 100%。
6. 调用失败与指标不适用分别记录。失败调用必须进入调用数和错误率分母，不能被框架默认的 NaN 跳过逻辑隐藏；适用的 Recall、排序质量、值映射与负样本指标记失败零分。报告同时给出每项指标的适用数、有效数和失败数。

所有指标都进入 RAGAS 实验结果。报告明确区分原生指标和项目注册指标，记录名称、公式、参数、版本及适用范围，不把自定义 nDCG、JOIN 或值映射语义冒称为框架内置标准。

### 5、执行链路与日志证据

正式链路为：

```text
RAGAS 数据集与实验配置
  → MCP fast_search(query=user_input)
  → SDK 结构化终态日志
  → 日志关联、身份还原和证据校验
  → 表 / 字段 / JOIN / 值映射评分视图
  → RAGAS 原生指标与注册领域指标
  → RAGAS 实验结果、聚合报告与 CI 门禁
```

1. 模型只接收 fast_search 的文本响应；工具响应不返回评测对象、Trace ID 或通道中间结果。评分只从 SDK 日志读取，不能解析响应文本或 structuredContent 补分。
2. 保留 `vfssql.logs.enabled/path` 配置。每轮使用独占日志；本地托管模式实际向子服务应用绝对日志路径和消融配置，连接已有服务时必须确认日志可读且配置已由服务端应用。
3. 每次实际工具调用记录样本 ID、实验变体、调用序号、日志字节范围与耗时，再关联唯一终态。相同问题的重复调用也要独立关联，不能只按 query 匹配日志。
4. 缺失、多条、串题、重复 Trace ID、截断或损坏日志，以及要求的召回通道没有实际启用，均标记证据或环境错误，不能当作一次成功空召回。
5. 只对最终实际展示的表、字段、值和关联评分；各通道中间候选单独保留用于诊断和消融，不能混入最终召回成绩。
6. 保存数据集、原始轨迹、调用账本、配置哈希、RAGAS 与指标版本、逐样本结果和汇总报告。离线重算从冻结原始证据再次运行同一 RAGAS 指标，不能直接读取已保存分数代替计算。
7. 配置生成保持只读索引、不自动建表；结束或异常时关闭本次创建的服务进程，外部已有服务保持由其维护者管理。

### 6、消融、CI 与报告

- 前文 A–G、图深度 1/2/3、RRF 与线性融合对比继续适用，作为 RAGAS 的独立实验或带明确变体标识的实验结果。每组实际应用 SDK 配置，使用同一冻结数据集和相同评分口径。
- 未配置向量时，按前文要求移除相应通道并显式记录 C 不适用；对其余缺失能力报出环境错误，不把未执行组混入成功平均。
- 连续 100 次稳定性测试按实际调用计数；一条调用生成多个指标视图时，错误率和延迟不能重复计数。实验并发需固定并记录，单调用延迟不包含 RAGAS 事后评分时间。
- 保持一条本地命令或手动工作流启动完整实验。项目启动入口只处理显式配置与调用 RAGAS，不保留旧评分后端；正式命令和依赖安装步骤随实现更新到运行说明。
- 日常 PR CI 安装锁定的评测依赖，运行真实 RAGAS 指标的合成回归及主副本一致性检查，不用 mock 掉框架调用后宣称正式接入通过。
- 正式 Benchmark 继续由维护者准备数据、OAuth、索引及可达服务后显式启动。手动工作流调用相同新入口；本地验证不能替代远程 CI，也不自动配置平台凭据或权限。
- 报告提供总体、题型、难度和实验组结果；分别检查各实验阈值，保留失败与不可计算状态。质量或稳定性阈值不满足时返回非零退出码，配置、数据、证据或环境错误也须失败退出。
- CI 仅上传聚合报告，完整问题、Gold、原始轨迹和可能含凭据的配置不公开上传。数据、标签质量、参数和扩展公式可审计、结果可复现，是框架接入之外的验收依据。

### 7、迁移步骤与验收条件

以下为后续实施清单；本次文档补充不将代码迁移标记为已完成。

- [ ] 1、固定 RAGAS 版本并验证正式 API。
  - [ ] 1.1 锁定评测依赖，验证 `@experiment`、原生 ID 指标及确定性领域指标的组合调用。
  - [ ] 1.2 用人工可计算的合成样本验证重复 ID、空集合、无命中、辅助项、不同 K、值类型和失败状态。
- [ ] 2、落实新数据集规范。
  - [ ] 2.1 实现原生字段与领域扩展校验，固定对象身份编码，完成必要项/辅助项/值映射的评分视图。
  - [ ] 2.2 转换数据并复核语义，按前文覆盖率、分布和独立标注标准验收，不能只做字段改名。
- [ ] 3、用 RAGAS 替换旧独立评测引擎。
  - [ ] 3.1 移除旧评分与编排路径，接入 MCP/日志适配、实验执行、注册指标和实验结果保存。
  - [ ] 3.2 保留并验证 SDK 文本/日志边界、证据关联、失败分母、离线重算和子服务清理。
  - [ ] 3.3 同步替换主副本与 Server/evals，确认独立复制后可安装和运行；SDK 与 MCP 业务回归继续通过。
- [ ] 4、切换 CI、文档和正式验收。
  - [ ] 4.1 更新本地入口、手动工作流、运行说明及相关 AGENTS；删除仍指向旧引擎的命令和测试依赖。
  - [ ] 4.2 用真实回环 MCP、临时索引和合成数据完成“日志 → RAGAS → 报告 → 离线重算”闭环，并验证失败会阻断 CI。
  - [ ] 4.3 正式数据验收后显式运行 Benchmark，发布包含框架版本、原生/扩展指标说明和消融结果的报告。

### 8、官方依据

- [RAGAS v0.4.3 发布记录](https://github.com/vibrantlabsai/ragas/releases/tag/v0.4.3)
- [v0.3 → v0.4 迁移：实验架构与指标接口](https://github.com/vibrantlabsai/ragas/blob/v0.4.3/docs/howtos/migrations/migrate_from_v03_to_v04.md)
- [Context Recall 与原生 IDBasedContextRecall](https://github.com/vibrantlabsai/ragas/blob/v0.4.3/docs/concepts/metrics/available_metrics/context_recall.md)
- [Context Precision 与原生 IDBasedContextPrecision](https://github.com/vibrantlabsai/ragas/blob/v0.4.3/docs/concepts/metrics/available_metrics/context_precision.md)
- [指标类型与自定义指标接口](https://github.com/vibrantlabsai/ragas/blob/v0.4.3/docs/concepts/metrics/overview/index.md)
- [SingleTurnSample 字段与 EvaluationDataset 源码](https://github.com/vibrantlabsai/ragas/blob/v0.4.3/src/ragas/dataset_schema.py)

### 9、实施进展（2026-11-06）

RAGAS 框架接入、新 JSONL 校验、SDK 日志适配、原生与领域指标、Docker 镜像及 CI 入口已实现，执行状态与验证记录统一见 [迁移计划](../plan/2026-11-06-vfssql-ragas-migration.md) 和 [迁移审查记录](../review/2026-11-06-vfssql-ragas-migration.md)。上节清单保留为方案阶段的验收要求，正式数据转换与独立标注验收、正式 Benchmark 仍待单独开展；合成测试不能替代这两项。
