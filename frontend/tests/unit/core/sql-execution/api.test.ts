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
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          agent_name: "data-agent",
          sql: "SELECT region FROM orders",
          source: "manual_ui",
        }),
        signal: undefined,
      },
    );
  });

  test("never forwards internal execution identity or database secrets", async () => {
    mockedFetch.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          version: 1,
          ok: true,
          database_type: "postgresql",
          columns: [],
          rows: [],
          truncated: false,
          empty: true,
          duration_ms: 1,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    await executeSql("thread/internal boundary", {
      agentName: "data-agent",
      sql: "SELECT region FROM orders",
      run_id: "run-forged",
      snapshot_id: "snapshot-forged",
      dsn: "postgresql://readonly:secret@db.local/sales",
      source: "subagent",
      internal_auth: "forged",
    } as never);

    const [url, init] = mockedFetch.mock.calls[0]!;
    const bodyText = init?.body;
    expect(typeof bodyText).toBe("string");
    if (typeof bodyText !== "string") {
      throw new Error("Expected SQL execution request body to be JSON text");
    }
    const body = JSON.parse(bodyText) as Record<string, unknown>;

    expect(url).toBe("/api/threads/thread%2Finternal%20boundary/sql/execute");
    expect(init?.headers).toEqual({ "Content-Type": "application/json" });
    expect(body).toEqual({
      agent_name: "data-agent",
      sql: "SELECT region FROM orders",
      source: "manual_ui",
    });
    expect(body).not.toHaveProperty("run_id");
    expect(body).not.toHaveProperty("snapshot_id");
    expect(body).not.toHaveProperty("dsn");
    expect(body).not.toHaveProperty("internal_auth");
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
