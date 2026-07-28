import { expect, it } from "@rstest/core";
import { createElement, type ComponentType, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  buildSqlResultSidecarContext,
  SqlResultPanelView,
} from "@/components/workspace/sql-execution/sql-result-panel";
import { I18nProvider } from "@/core/i18n/context";

const TestI18nProvider = I18nProvider as ComponentType<{
  initialLocale: "en-US" | "zh-CN";
  children?: ReactNode;
}>;

it("renders successful SQL rows and execution metadata", () => {
  const html = renderToStaticMarkup(
    createElement(
      TestI18nProvider,
      { initialLocale: "zh-CN" },
      createElement(SqlResultPanelView, {
        sql: "SELECT region, total FROM orders",
        loading: false,
        error: null,
        result: {
          version: 1,
          ok: true,
          database_type: "postgresql",
          columns: ["region", "total"],
          rows: [{ region: "华东", total: 100 }],
          row_count: 1,
          returned_row_count: 1,
          truncated: true,
          empty: false,
          duration_ms: 12.5,
        },
        onClose: () => undefined,
        onRerun: () => undefined,
      }),
    ),
  );

  expect(html).toContain("SQL 执行结果");
  expect(html).toContain("华东");
  expect(html).toContain("100");
  expect(html).toContain("结果已截断");
  expect(html).toContain("12.5 ms");
});

it("renders Gateway and SQL execution errors", () => {
  const html = renderToStaticMarkup(
    createElement(
      TestI18nProvider,
      { initialLocale: "en-US" },
      createElement(SqlResultPanelView, {
        sql: "SELECT missing FROM orders",
        loading: false,
        error: "Gateway unavailable",
        result: null,
        onClose: () => undefined,
        onRerun: () => undefined,
      }),
    ),
  );

  expect(html).toContain("SQL Result");
  expect(html).toContain("Gateway unavailable");
});

it("renders the database primary error without replacing it", () => {
  const html = renderToStaticMarkup(
    createElement(
      TestI18nProvider,
      { initialLocale: "zh-CN" },
      createElement(SqlResultPanelView, {
        sql: "SELECT fd.missing_column FROM femalediagnosticinfo fd",
        loading: false,
        error: null,
        result: {
          version: 1,
          ok: false,
          database_type: "mysql",
          error_code: "SQL_EXECUTION_FAILED",
          error_category: "unknown_column",
          error_message: "Unknown column 'fd.missing_column' in 'field list'",
          retryable: true,
          recommended_action: "repair_sql",
          duration_ms: 8.5,
          columns: [],
          rows: [],
          truncated: false,
          empty: false,
        },
        onClose: () => undefined,
        onRerun: () => undefined,
      }),
    ),
  );

  expect(html).toContain("SQL_EXECUTION_FAILED");
  expect(html).toContain(
    "Unknown column &#x27;fd.missing_column&#x27; in &#x27;field list&#x27;",
  );
  expect(html).toContain('data-testid="sql-result-error-message"');
});

it("builds selected SQL result text as conversation context", () => {
  expect(
    buildSqlResultSidecarContext(
      " Unknown column 'fd.missing_column' in 'field list' ",
      "SQL 执行结果",
    ),
  ).toEqual({
    type: "referenced_message",
    label: "SQL 执行结果",
    role: "assistant",
    content: "Unknown column 'fd.missing_column' in 'field list'",
  });
  expect(buildSqlResultSidecarContext("  ", "SQL 执行结果")).toBeNull();
});
