# 知识候选提交

仅在用户明确要求记录、修订、扩展术语或停用知识，或已授权当前学习流程时使用。内容还须经过用户明确确认；纠正本轮 SQL 不自动授权持久化。

## 准备依据

先读取现有 evidence/graph 与相关表字段，保存来源问题、确认原话和适用条件。SQL 执行成功、表达相似或模型置信度不能证明业务同义；同次用到两张表也不能证明存在关联。

修订、扩展和停用必须获得可信提供的**当前启用版本** evidence_id/edge_id。文件视图可能没有这些 ID 或审核状态，行号也不是版本 ID。无法核验时说明缺口，请用户或管理端提供，不能猜 ID、使用待审核版本或为绕过缺口重复新增同一知识。

## 选择提交类型

所有 request 均包含 `kind/request_id/source_query/supporting_text`；request_id 为稳定、非空且不超过 80 字符的字母/数字/下划线/冒号/点/连字符组合。

| kind | 内容 | 前提 |
| --- | --- | --- |
| triggers | evidence_id、非空 triggers 数组 | 语义完全相同，仅增加触发词，原正文保留 |
| evidence | content、非空 triggers；新增还需 bindings，修订还需 evidence_id | 用户确认了稳定规则或术语定义 |
| relation | relation.source/target，各含 schema_name/table_name/单个 columns 字段；修订还需 edge_id | 两端字段与等值关系明确，多跳逐边提交 |

bindings 使用公开表名，如 `{"schema_name":"sales","table_name":"t_orders","columns":["status"]}`。规则可绑定多个字段；关系每端恰好一个字段。relation 还可提供 cardinality=one_to_one/one_to_many/many_to_one/many_to_many/unknown，以及 direction=outgoing/incoming/bidirectional。

逻辑停用使用 kind=evidence 配 evidence_id，或 kind=relation 配 edge_id，设置 disable=true 并保留来源依据。学习不能改写物理外键，也不能直接删除、覆盖旧正文或审批。

## 提交示例

仅在用户明确要求保存下列规则、且实际结构已核验时，替换示例表字段和确认原话后调用：

```json
{
  "name": "sql_learn",
  "arguments": {
    "request": {
      "kind": "evidence",
      "request_id": "active-products-20261020-1",
      "source_query": "请记录在售商品口径",
      "supporting_text": "用户确认：商品 status 的原始值 active 表示在售。",
      "triggers": [
        "在售商品"
      ],
      "content": "商品 status 为 active 时属于在售商品。",
      "bindings": [
        {
          "schema_name": "sales",
          "table_name": "t_products",
          "columns": [
            "status"
          ]
        }
      ]
    }
  }
}
```

扩展既有规则的示例，42 仅演示字段类型，调用前必须替换为已核验的当前启用版本 ID：

```json
{
  "name": "sql_learn",
  "arguments": {
    "request": {
      "kind": "triggers",
      "request_id": "customer-term-20261020-1",
      "evidence_id": 42,
      "triggers": [
        "成熟客户"
      ],
      "source_query": "请把成熟客户记录为老客户的同义词",
      "supporting_text": "用户明确确认：成熟客户与现有老客户规则使用完全相同口径。"
    }
  }
}
```

## 回执与后续

- `ok=true/status=pending/applied=false`：候选已提交待审核，未成为正式知识。回执中的新 evidence_id/edge_id 不是当前启用版本的证明；同时核对 active。
- `duplicate=true`：同一请求重试；不重复创建新候选。超时后重试保持相同 request_id 与内容，内容改变则使用新 ID。
- `disabled/forbidden/rejected/error`：分别按学习关闭、权限不足、请求不适用或写入失败报告；不自行改配置或审批。状态不明确时不声称已保存。

审核通过才切换正式启用版本，旧版本保留。用户明确指定的新口径可用于本轮请求，并注明来自用户；候选待审核不能被描述为已生效的长期规则。
