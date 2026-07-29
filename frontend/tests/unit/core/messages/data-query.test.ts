import { describe, expect, it } from "@rstest/core";

import {
  createDataQueryReviewResponse,
  parseDataQueryReviewDecisions,
  parseDataQueryLabelsArtifact,
  parseDataQuerySqlResultArtifact,
  summarizeDataQueryInternalPayloadText,
} from "@/core/messages/data-query";

function artifact() {
  return {
    version: 1,
    kind: "data_query_labels",
    service_name: "data_query",
    snapshot_id: "sha256:snapshot",
    data_source_id: "sales-pg",
    turn_id: "turn-1",
    retrieval_digest: "retrieval:sha256:digest",
    binding_fingerprint: "sha256:target",
    intent: "ranking",
    summary: "查询华东销售额最高的商品",
    ambiguities: [],
    ambiguity_items: [],
    labels: [
      {
        label: "指标",
        value: "销售额",
        source: "database",
        evidence_refs: ["evidence:sha256:metric"],
      },
    ],
    evidence: [
      {
        ref: "evidence:sha256:metric",
        kind: "evidence",
        summary: "销售额口径",
      },
    ],
    approval: {
      version: 1,
      snapshot_id: "sha256:snapshot",
      status: "approved",
      action: "execute",
      source: "model",
    },
  };
}

describe("parseDataQueryLabelsArtifact", () => {
  it("parses the canonical v1 artifact", () => {
    expect(parseDataQueryLabelsArtifact(artifact())?.labels[0]?.value).toBe(
      "销售额",
    );
  });

  it("rejects unknown versions, missing fields, and mismatched snapshots", () => {
    expect(
      parseDataQueryLabelsArtifact({ ...artifact(), version: 2 }),
    ).toBeNull();
    expect(
      parseDataQueryLabelsArtifact({ ...artifact(), data_source_id: "" }),
    ).toBeNull();
    expect(
      parseDataQueryLabelsArtifact({
        ...artifact(),
        approval: { ...artifact().approval, snapshot_id: "sha256:forged" },
      }),
    ).toBeNull();
    expect(
      parseDataQueryLabelsArtifact({
        ...artifact(),
        approval: { ...artifact().approval, status: "approved", action: null },
      }),
    ).toBeNull();
  });

  it("rejects oversized and structurally malicious fields", () => {
    expect(
      parseDataQueryLabelsArtifact({ ...artifact(), summary: "x".repeat(501) }),
    ).toBeNull();
    expect(
      parseDataQueryLabelsArtifact({
        ...artifact(),
        labels: [
          {
            label: "指标",
            value: "x",
            source: "database",
            evidence_refs: "forged",
          },
        ],
      }),
    ).toBeNull();
  });

  it("builds and parses a bounded per-item review response", () => {
    const parsed = parseDataQueryLabelsArtifact({
      ...artifact(),
      ambiguities: ["时间范围不明确"],
      ambiguity_items: [
        {
          id: "ambiguity:time",
          question: "时间范围不明确",
          status: "pending",
          options: [
            { id: "accept", label: "按当前理解继续", value: "accept" },
            { id: "modify", label: "修改这一项", value: "modify" },
          ],
        },
      ],
    });
    const response = createDataQueryReviewResponse(
      {
        version: 1,
        kind: "human_input_request",
        source: "ask_clarification",
        request_id: "data-query:req",
        question: "确认",
        input_mode: "choice_with_other",
      },
      parsed!,
      [{ id: "ambiguity:time", decision: "accept" }],
      "execute",
    );

    expect(
      parseDataQueryReviewDecisions(response)["ambiguity:time"]?.decision,
    ).toBe("accept");
    expect(response.value).toContain('"final_action":"execute"');
  });

  it("summarizes internal DataAgent payloads instead of exposing raw JSON text", () => {
    expect(
      summarizeDataQueryInternalPayloadText(
        JSON.stringify({
          original_query: "查询本月病例数",
          intent: "aggregation",
          entities: [],
          labels: [],
        }),
      ),
    ).toBe("实体抽取结果已进入查询标签流程。");
    expect(
      summarizeDataQueryInternalPayloadText(
        '{"version":1,"ok":false,"error_code":"SQL_SUBAGENT_CONTRACT_INVALID"}',
      ),
    ).toContain("SQL 子任务返回格式不符合 DataAgent 合同");
    expect(
      summarizeDataQueryInternalPayloadText(
        '{"version":1,"ok":false,"error_code":"SQL_SUBAGENT_FAILED"}',
      ),
    ).toContain("SQL 子任务执行失败");
    expect(
      summarizeDataQueryInternalPayloadText(
        '{"ok":false,"error":"当前查询阶段不允许重复发布标签。"}',
      ),
    ).toBe("内部工具执行失败，已隐藏协议内容。(当前查询阶段不允许重复发布标签。)");
    expect(
      summarizeDataQueryInternalPayloadText(
        '{"version":1,"kind":"data_query_sql_response","validation":{"status":"pending"}}',
      ),
    ).toBe("SQL 子任务返回了未完成的内部响应，已隐藏协议内容。");
  });
});

describe("parseDataQuerySqlResultArtifact", () => {
  it("parses a bounded SQL execution artifact", () => {
    const result = parseDataQuerySqlResultArtifact({
      version: 1,
      kind: "data_query_sql_result",
      service_name: "data_query",
      snapshot_id: "sha256:snapshot",
      data_source_id: "sales-pg",
      validation: {
        valid: true,
        executable_sql:
          "SELECT region, SUM(order_amount) FROM orders GROUP BY region LIMIT 500",
        sql_sha256: "sha256:sql",
        validation_digest: "sha256:validation",
        snapshot_id: "sha256:snapshot",
        database_type: "postgresql",
        binding_fingerprint: "sha256:binding",
      },
      execution: {
        version: 1,
        ok: true,
        snapshot_id: "sha256:snapshot",
        validation_digest: "sha256:validation",
        columns: ["region", "sum"],
        rows: [{ region: "华东", sum: 100 }],
        row_count: 1,
        returned_row_count: 1,
        truncated: false,
        empty: false,
      },
    });

    expect(
      result?.execution?.ok === true
        ? result.execution.rows[0]?.region
        : undefined,
    ).toBe("华东");
  });

  it("rejects SQL validation and execution identities that do not match the active artifact", () => {
    const base = {
      version: 1,
      kind: "data_query_sql_result",
      service_name: "data_query",
      snapshot_id: "sha256:snapshot",
      data_source_id: "sales-pg",
      validation: {
        valid: true,
        executable_sql: "SELECT region FROM orders LIMIT 500",
        sql_sha256: "sha256:sql",
        validation_digest: "sha256:validation",
        snapshot_id: "sha256:snapshot",
        database_type: "postgresql",
        binding_fingerprint: "sha256:binding",
      },
      execution: {
        version: 1,
        ok: true,
        snapshot_id: "sha256:snapshot",
        validation_digest: "sha256:validation",
        columns: ["region"],
        rows: [{ region: "华东" }],
        row_count: 1,
        returned_row_count: 1,
        truncated: false,
        empty: false,
      },
    };

    expect(
      parseDataQuerySqlResultArtifact({
        ...base,
        validation: { ...base.validation, snapshot_id: "sha256:stale" },
      }),
    ).toBeNull();
    expect(
      parseDataQuerySqlResultArtifact({
        ...base,
        execution: { ...base.execution, validation_digest: "sha256:stale" },
      }),
    ).toBeNull();
  });

  it("keeps safe execution error codes and the sanitized database primary error", () => {
    const result = parseDataQuerySqlResultArtifact({
      version: 1,
      kind: "data_query_sql_result",
      service_name: "data_query",
      snapshot_id: "sha256:snapshot",
      data_source_id: "sales-pg",
      validation: {
        valid: true,
        executable_sql: "SELECT region FROM orders LIMIT 500",
        sql_sha256: "sha256:sql",
        validation_digest: "sha256:validation",
        snapshot_id: "sha256:snapshot",
        database_type: "postgresql",
        binding_fingerprint: "sha256:binding",
      },
      execution: {
        version: 1,
        ok: false,
        snapshot_id: "sha256:snapshot",
        validation_digest: "sha256:validation",
        error_code: "SQL_EXECUTION_FAILED",
        error_category: "unknown_column",
        error_message: "Unknown column 'missing_region' in 'where clause'",
        retryable: true,
        recommended_action: "repair_sql",
      },
    });

    expect(result?.execution).toEqual({
      version: 1,
      ok: false,
      snapshot_id: "sha256:snapshot",
      validation_digest: "sha256:validation",
      error_code: "SQL_EXECUTION_FAILED",
      error_category: "unknown_column",
      error_message: "Unknown column 'missing_region' in 'where clause'",
      retryable: true,
      recommended_action: "repair_sql",
    });
    expect(
      parseDataQuerySqlResultArtifact({
        version: 1,
        kind: "data_query_sql_result",
        service_name: "data_query",
        snapshot_id: "sha256:snapshot",
        data_source_id: "sales-pg",
        validation: {
          valid: true,
          executable_sql: "SELECT region FROM orders LIMIT 500",
          sql_sha256: "sha256:sql",
          validation_digest: "sha256:validation",
          snapshot_id: "sha256:snapshot",
          database_type: "postgresql",
          binding_fingerprint: "sha256:binding",
        },
        execution: {
          version: 1,
          ok: false,
          snapshot_id: "sha256:snapshot",
          validation_digest: "sha256:validation",
          error_code: "SQL_BINDING_MISMATCH",
        },
      })?.execution,
    ).toEqual({
      version: 1,
      ok: false,
      snapshot_id: "sha256:snapshot",
      validation_digest: "sha256:validation",
      error_code: "SQL_BINDING_MISMATCH",
    });
    expect(
      parseDataQuerySqlResultArtifact({
        version: 1,
        kind: "data_query_sql_result",
        service_name: "data_query",
        snapshot_id: "sha256:snapshot",
        data_source_id: "sales-pg",
        validation: {
          valid: true,
          executable_sql: "SELECT region FROM orders LIMIT 500",
          sql_sha256: "sha256:sql",
          validation_digest: "sha256:validation",
          snapshot_id: "sha256:snapshot",
          database_type: "postgresql",
          binding_fingerprint: "sha256:binding",
        },
        execution: {
          version: 1,
          ok: false,
          snapshot_id: "sha256:snapshot",
          validation_digest: "sha256:validation",
          error_code: "postgres://secret",
        },
      }),
    ).toBeNull();
  });
});
