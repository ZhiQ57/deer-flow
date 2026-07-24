import { expect, test } from "@playwright/test";

import { mockLangGraphAPI, MOCK_THREAD_ID } from "./utils/mock-api";

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
