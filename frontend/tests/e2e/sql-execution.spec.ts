import { expect, test, type Page } from "@playwright/test";

import { mockLangGraphAPI, MOCK_THREAD_ID } from "./utils/mock-api";

/**
 * 在 SQL Result Panel 中选择文本并触发结果区域的选择事件。
 *
 * @param page 当前 Playwright 页面。
 * @param targetText 需要选择的结果文本。
 */
async function selectSqlResultText(page: Page, targetText: string) {
  await page.evaluate((text) => {
    const root = document.querySelector('[data-testid="sql-result-panel"]');
    if (!root) throw new Error("SQL Result Panel not found");
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let node = walker.nextNode();
    while (node) {
      const value = node.textContent ?? "";
      const start = value.indexOf(text);
      if (start >= 0) {
        const range = document.createRange();
        range.setStart(node, start);
        range.setEnd(node, start + text.length);
        const selection = window.getSelection();
        selection?.removeAllRanges();
        selection?.addRange(range);
        if (selection?.toString() !== text) {
          throw new Error(
            `SQL result selection mismatch: ${selection?.toString() ?? ""}`,
          );
        }
        node.parentElement?.dispatchEvent(
          new MouseEvent("mouseup", { bubbles: true }),
        );
        return;
      }
      node = walker.nextNode();
    }
    throw new Error(`Unable to find SQL result text: ${text}`);
  }, targetText);
}

test("executes a DataAgent SQL code block and opens the result panel", async ({
  page,
}) => {
  mockLangGraphAPI(page, {
    agents: [
      {
        name: "data-agent",
        description: "DataAgent",
        service_ability: {
          type: "data_query",
          version: 1,
          sql_execution_enabled: true,
          database_type: "postgresql",
        },
      },
    ],
    threads: [
      {
        thread_id: MOCK_THREAD_ID,
        title: "SQL query",
        agent_name: "data-agent",
        messages: [
          {
            type: "human",
            id: "msg-human-sql",
            content: "Show sales by region",
          },
          {
            type: "ai",
            id: "msg-ai-sql",
            content: [
              "```sql",
              "SELECT orders.region FROM public.orders",
              "```",
            ].join("\n"),
          },
        ],
      },
    ],
  });

  let requestBody: Record<string, unknown> | undefined;
  await page.route(
    `**/api/threads/${MOCK_THREAD_ID}/sql/execute`,
    async (route) => {
      requestBody = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          version: 1,
          ok: true,
          database_type: "postgresql",
          columns: ["region"],
          rows: [{ region: "East China" }],
          row_count: 1,
          returned_row_count: 1,
          truncated: false,
          empty: false,
          duration_ms: 9.4,
        }),
      });
    },
  );

  await page.goto(`/workspace/agents/data-agent/chats/${MOCK_THREAD_ID}`);

  const executeButton = page.getByTestId("sql-execute-button");
  await expect(executeButton).toBeVisible({ timeout: 15_000 });
  await executeButton.click();

  await expect
    .poll(() => requestBody)
    .toMatchObject({
      agent_name: "data-agent",
      sql: "SELECT orders.region FROM public.orders\n",
      source: "manual_ui",
    });
  const resultPanel = page.getByTestId("sql-result-panel");
  await expect(resultPanel).toBeVisible();
  await expect(resultPanel).toContainText("SQL Result");
  await expect(resultPanel).toContainText("East China");
  await expect(resultPanel).toContainText("9.4 ms");
});

test("shows the database primary error and adds selected error text to conversation", async ({
  page,
}) => {
  mockLangGraphAPI(page, {
    agents: [
      {
        name: "data-agent",
        description: "DataAgent",
        service_ability: {
          type: "data_query",
          version: 1,
          sql_execution_enabled: true,
          database_type: "mysql",
        },
      },
    ],
    threads: [
      {
        thread_id: MOCK_THREAD_ID,
        title: "SQL query",
        agent_name: "data-agent",
        messages: [
          {
            type: "human",
            id: "msg-human-sql-error",
            content: "Count unexplained cases",
          },
          {
            type: "ai",
            id: "msg-ai-sql-error",
            content: [
              "```sql",
              "SELECT fd.FemaleInfertilityDiagnosis FROM femalediagnosticinfo fd",
              "```",
            ].join("\n"),
          },
        ],
      },
    ],
  });

  await page.route(
    `**/api/threads/${MOCK_THREAD_ID}/sql/execute`,
    async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          version: 1,
          ok: false,
          database_type: "mysql",
          error_code: "SQL_EXECUTION_FAILED",
          error_category: "unknown_column",
          error_message:
            "Unknown column 'fd.FemaleInfertilityDiagnosis' in 'where clause'",
          retryable: true,
          recommended_action: "repair_sql",
          duration_ms: 6.2,
          columns: [],
          rows: [],
          truncated: false,
          empty: false,
        }),
      });
    },
  );

  await page.goto(`/workspace/agents/data-agent/chats/${MOCK_THREAD_ID}`);
  await page.getByTestId("sql-execute-button").click();

  const resultPanel = page.getByTestId("sql-result-panel");
  await expect(resultPanel).toContainText(
    "Unknown column 'fd.FemaleInfertilityDiagnosis' in 'where clause'",
  );
  await selectSqlResultText(
    page,
    "Unknown column 'fd.FemaleInfertilityDiagnosis' in 'where clause'",
  );
  const toolbar = page.locator("[data-sql-result-selection-toolbar]");
  await expect(toolbar).toBeVisible();
  await toolbar.getByRole("button", { name: /add to conversation/i }).click();
  await expect(page.getByTestId("conversation-quote-attachment")).toBeVisible();
});
