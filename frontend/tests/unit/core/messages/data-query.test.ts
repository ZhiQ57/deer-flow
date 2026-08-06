import { describe, expect, it } from "@rstest/core";

import {
  parseDataQueryIntentApprovalArtifact,
  parseDataQueryLabelsArtifact,
  parseDataQuerySqlResultArtifact,
  summarizeDataQueryInternalPayloadText,
} from "@/core/messages/data-query";

function artifact() {
  return {
    intent: "ranking",
    summary: "查询华东销售额最高的商品",
    labels: [
      {
        label: "指标",
        value: "销售额",
        source: "database",
        evidence: "销售额口径",
      },
    ],
    evidence: [],
    approval: {
      flow_id: "ABCDEFGH",
      required: true,
      reason: "需要人工审批",
      next_tool: "ask_intent_approval",
    },
  };
}

describe("parseDataQueryLabelsArtifact", () => {
  it("parses the new publish_query_labels artifact", () => {
    expect(parseDataQueryLabelsArtifact(artifact())?.labels[0]?.value).toBe(
      "销售额",
    );
  });

  it("rejects missing labels and malformed approval flow ids", () => {
    expect(
      parseDataQueryLabelsArtifact({ ...artifact(), labels: [] }),
    ).toBeNull();
    expect(
      parseDataQueryLabelsArtifact({
        ...artifact(),
        approval: { ...artifact().approval, flow_id: "" },
      }),
    ).toBeNull();
    expect(
      parseDataQueryLabelsArtifact({
        ...artifact(),
        approval: { ...artifact().approval, required: "yes" },
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
            evidence: { forged: true },
          },
        ],
      }),
    ).toBeNull();
  });

  it("parses the flow-bound ask_intent_approval artifact", () => {
    expect(
      parseDataQueryIntentApprovalArtifact({
        version: 1,
        kind: "data_query_intent_approval",
        service_name: "data_query",
        flow_id: "ABCDEFGH",
        human_input: { kind: "human_input_request" },
        approval: {
          version: 1,
          flow_id: "ABCDEFGH",
          status: "awaiting_confirmation",
          action: null,
          source: null,
          request_id: "request-1",
          tool_call_id: "call-1",
        },
      })?.flow_id,
    ).toBe("ABCDEFGH");
    expect(
      parseDataQueryIntentApprovalArtifact({
        version: 1,
        kind: "data_query_intent_approval",
        service_name: "data_query",
        flow_id: "ABCDEFGH",
        human_input: {},
        approval: {
          version: 1,
          flow_id: "DIFFERENT",
          status: "awaiting_confirmation",
          action: null,
          source: null,
          request_id: "request-1",
          tool_call_id: "call-1",
        },
      }),
    ).toBeNull();
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
    ).toBe(
      "内部工具执行失败，已隐藏协议内容。(当前查询阶段不允许重复发布标签。)",
    );
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
