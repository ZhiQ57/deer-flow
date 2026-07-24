export interface SqlExecutionResult {
  version: number;
  ok: boolean;
  database_type: string;
  error_code?: string | null;
  error_category?: string | null;
  error_message?: string | null;
  retryable?: boolean | null;
  recommended_action?: string | null;
  duration_ms: number;
  sql_sha256?: string | null;
  validation_digest?: string | null;
  snapshot_id?: string | null;
  attempt?: number | null;
  max_attempts?: number | null;
  row_count?: number | null;
  returned_row_count?: number | null;
  columns: string[];
  rows: Record<string, unknown>[];
  truncated: boolean;
  empty: boolean;
}

export interface ExecuteSqlInput {
  agentName: string;
  sql: string;
  signal?: AbortSignal;
}
