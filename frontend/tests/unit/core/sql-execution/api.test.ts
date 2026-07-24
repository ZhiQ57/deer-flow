import { beforeEach, describe, expect, test, rs } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
}));

rs.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
}));

import { fetch as fetcher } from "@/core/api/fetcher";
import { executeSql } from "@/core/sql-execution/api";

const mockedFetch = rs.mocked(fetcher);

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("executeSql", () => {
  test("posts manual SQL to the current thread Gateway endpoint", async () => {
    mockedFetch.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          version: 1,
          ok: true,
          database_type: "postgresql",
          columns: ["region"],
          rows: [{ region: "华东" }],
          row_count: 1,
          returned_row_count: 1,
          truncated: false,
          empty: false,
          duration_ms: 8.5,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    const result = await executeSql("thread-1", {
      agentName: "data-agent",
      sql: "SELECT region FROM orders",
    });

    expect(result.rows).toEqual([{ region: "华东" }]);
    expect(mockedFetch).toHaveBeenCalledWith(
      "/api/threads/thread-1/sql/execute",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          agent_name: "data-agent",
          sql: "SELECT region FROM orders",
          source: "manual_ui",
        }),
      }),
    );
  });

  test("preserves safe Gateway failure details", async () => {
    mockedFetch.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          detail: "Agent does not enable data_query SQL execution",
        }),
        {
          status: 403,
          headers: { "Content-Type": "application/json" },
        },
      ),
    );

    await expect(
      executeSql("thread-1", {
        agentName: "lead-agent",
        sql: "SELECT 1",
      }),
    ).rejects.toThrow("Agent does not enable data_query SQL execution");
  });
});
