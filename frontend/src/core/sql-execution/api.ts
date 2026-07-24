import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type { ExecuteSqlInput, SqlExecutionResult } from "./types";

export class SqlExecutionRequestError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
    this.name = "SqlExecutionRequestError";
  }
}

export async function executeSql(
  threadId: string,
  input: ExecuteSqlInput,
): Promise<SqlExecutionResult> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/sql/execute`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        agent_name: input.agentName,
        sql: input.sql,
        source: "manual_ui",
      }),
      signal: input.signal,
    },
  );
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as {
      detail?: unknown;
    };
    const detail =
      typeof payload.detail === "string"
        ? payload.detail
        : `SQL execution request failed: ${response.statusText || response.status}`;
    throw new SqlExecutionRequestError(detail, response.status);
  }
  return response.json() as Promise<SqlExecutionResult>;
}
