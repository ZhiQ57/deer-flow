# 过程评测 - SQL Schema Recall 评测
仅使用端到端结果评分，难以区分意图理解、业务检索、SQL 生成等环节的错误。因此增加模块级评测和轨迹检查，以支持错误定位及组件优化。
> 初始阶段无法概括所有用例, 后续继续补充.
## 1.介绍

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