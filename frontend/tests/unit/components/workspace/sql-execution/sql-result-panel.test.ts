import { expect, it } from "@rstest/core";
import { createElement, type ComponentType, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { SqlResultPanelView } from "@/components/workspace/sql-execution/sql-result-panel";
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
