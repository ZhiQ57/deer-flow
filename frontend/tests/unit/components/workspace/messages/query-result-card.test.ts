import { expect, it } from "@rstest/core";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { QueryResultCard } from "@/components/workspace/messages/query-result-card";
import type { QuerySqlResultArtifact } from "@/core/messages/data-query";

it("renders validated SQL and successful rows", () => {
  const artifact: QuerySqlResultArtifact = {
    version: 1,
    kind: "data_query_sql_result",
    service_name: "data_query",
    snapshot_id: "sha256:snapshot",
    data_source_id: "sales-pg",
    validation: {
      valid: true,
      executable_sql:
        "SELECT region, SUM(order_amount) AS total FROM orders GROUP BY region LIMIT 500",
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
      columns: ["region", "total"],
      rows: [{ region: "华东", total: 100 }],
      row_count: 1,
      returned_row_count: 1,
      truncated: false,
      empty: false,
    },
  };

  const html = renderToStaticMarkup(
    createElement(QueryResultCard, { artifact }),
  );
  expect(html).toContain("查询结果");
  expect(html).toContain("执行成功");
  expect(html).toContain("SELECT region");
  expect(html).toContain("华东");
});

it("renders the database primary error from the SQL result artifact", () => {
  const artifact: QuerySqlResultArtifact = {
    version: 1,
    kind: "data_query_sql_result",
    service_name: "data_query",
    snapshot_id: "sha256:snapshot",
    data_source_id: "sales-mysql",
    validation: {
      valid: true,
      executable_sql:
        "SELECT fd.FemaleInfertilityDiagnosis FROM femalediagnosticinfo fd",
      sql_sha256: "sha256:sql",
      validation_digest: "sha256:validation",
      snapshot_id: "sha256:snapshot",
      database_type: "mysql",
      binding_fingerprint: "sha256:binding",
    },
    execution: {
      version: 1,
      ok: false,
      snapshot_id: "sha256:snapshot",
      validation_digest: "sha256:validation",
      error_code: "SQL_EXECUTION_FAILED",
      error_category: "unknown_column",
      error_message:
        "Unknown column 'fd.FemaleInfertilityDiagnosis' in 'where clause'",
      retryable: true,
      recommended_action: "repair_sql",
    },
  };

  const html = renderToStaticMarkup(
    createElement(QueryResultCard, { artifact }),
  );
  expect(html).toContain("SQL_EXECUTION_FAILED");
  expect(html).toContain(
    "Unknown column &#x27;fd.FemaleInfertilityDiagnosis&#x27; in &#x27;where clause&#x27;",
  );
  expect(html).toContain('data-testid="query-result-error-message"');
});
