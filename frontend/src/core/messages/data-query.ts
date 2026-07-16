import type { Message } from "@langchain/langgraph-sdk";

export type QueryLabelSource = "user" | "database" | "derived";

export type QueryIntentLabel = {
  label: string;
  value: string;
  source: QueryLabelSource;
  normalized?: string;
  evidence_refs: string[];
};

export type QueryIntentEvidence = {
  ref: string;
  kind: string;
  summary: string;
};

export type QueryIntentApproval = {
  version: 1;
  snapshot_id: string;
  status: "approved" | "awaiting_confirmation" | "cancelled";
  action: "execute" | "sql_only" | "cancel" | null;
  source: "model" | "human" | null;
};

export type QueryIntentArtifact = {
  version: 1;
  kind: "data_query_labels";
  service_name: "data_query";
  snapshot_id: string;
  data_source_id: string;
  turn_id: string;
  retrieval_digest: string;
  binding_fingerprint: string;
  intent: string;
  summary?: string | null;
  confidence?: number | null;
  ambiguities: string[];
  labels: QueryIntentLabel[];
  evidence: QueryIntentEvidence[];
  approval: QueryIntentApproval;
  human_input?: unknown;
};

export type QuerySqlResultArtifact = {
  version: 1;
  kind: "data_query_sql_result";
  service_name: "data_query";
  snapshot_id: string;
  data_source_id: string;
  validation: {
    valid: true;
    executable_sql: string;
    sql_sha256: string;
    validation_digest: string;
    snapshot_id: string;
    database_type: "postgresql" | "mysql";
    binding_fingerprint: string;
  };
  execution: {
    version: 1;
    ok: true;
    snapshot_id: string;
    validation_digest: string;
    columns: string[];
    rows: Record<string, unknown>[];
    row_count: number;
    returned_row_count: number;
    truncated: boolean;
    empty: boolean;
    duration_ms?: number;
  } | {
    version: 1;
    ok: false;
    snapshot_id: string;
    validation_digest: string;
    error_code: string;
    duration_ms?: number;
  } | null;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isBoundedString(value: unknown, max: number): value is string {
  return typeof value === "string" && value.trim().length > 0 && value.length <= max;
}

const SQL_EXECUTION_ERROR_CODES = new Set(["SQL_BINDING_MISMATCH", "SQL_DSN_MISSING", "SQL_EXECUTION_FAILED", "SQL_TIMEOUT", "SQL_CANCELLED", "SQL_EXECUTION_ALREADY_ATTEMPTED"]);

function parseLabel(value: unknown): QueryIntentLabel | null {
  if (!isRecord(value)) return null;
  if (!isBoundedString(value.label, 50) || !isBoundedString(value.value, 200)) return null;
  if (value.source !== "user" && value.source !== "database" && value.source !== "derived") return null;
  if (!Array.isArray(value.evidence_refs) || value.evidence_refs.length > 20 || value.evidence_refs.some((ref) => !isBoundedString(ref, 200))) return null;
  if (value.normalized !== undefined && value.normalized !== null && !isBoundedString(value.normalized, 200)) return null;
  return {
    label: value.label,
    value: value.value,
    source: value.source,
    evidence_refs: value.evidence_refs,
    ...(value.normalized ? { normalized: value.normalized } : {}),
  };
}

function parseEvidence(value: unknown): QueryIntentEvidence | null {
  if (!isRecord(value)) return null;
  if (!isBoundedString(value.ref, 200) || !isBoundedString(value.kind, 50) || !isBoundedString(value.summary, 500)) return null;
  return { ref: value.ref, kind: value.kind, summary: value.summary };
}

export function parseDataQueryLabelsArtifact(value: unknown): QueryIntentArtifact | null {
  if (!isRecord(value)) return null;
  if (
    value.version !== 1 ||
    value.kind !== "data_query_labels" ||
    value.service_name !== "data_query" ||
    !isBoundedString(value.snapshot_id, 200) ||
    !isBoundedString(value.data_source_id, 128) ||
    !isBoundedString(value.turn_id, 200) ||
    !isBoundedString(value.retrieval_digest, 200) ||
    !isBoundedString(value.binding_fingerprint, 200) ||
    !isBoundedString(value.intent, 100)
  ) {
    return null;
  }
  if (value.summary !== undefined && value.summary !== null && !isBoundedString(value.summary, 500)) return null;
  if (value.confidence !== undefined && value.confidence !== null && (typeof value.confidence !== "number" || !Number.isFinite(value.confidence) || value.confidence < 0 || value.confidence > 1)) return null;
  if (!Array.isArray(value.ambiguities) || value.ambiguities.length > 20 || value.ambiguities.some((item) => !isBoundedString(item, 200))) return null;
  if (!Array.isArray(value.labels) || value.labels.length === 0 || value.labels.length > 30) return null;
  if (!Array.isArray(value.evidence) || value.evidence.length > 30) return null;
  const labels = value.labels.map(parseLabel);
  const evidence = value.evidence.map(parseEvidence);
  if (labels.some((label) => label === null) || evidence.some((item) => item === null)) return null;
  if (!isRecord(value.approval) || value.approval.version !== 1 || value.approval.snapshot_id !== value.snapshot_id) return null;
  if (value.approval.status !== "approved" && value.approval.status !== "awaiting_confirmation" && value.approval.status !== "cancelled") return null;
  if (value.approval.action !== null && value.approval.action !== "execute" && value.approval.action !== "sql_only" && value.approval.action !== "cancel") return null;
  if (value.approval.source !== null && value.approval.source !== "model" && value.approval.source !== "human") return null;
  if (value.approval.status === "approved" && value.approval.action !== "execute" && value.approval.action !== "sql_only") return null;
  if (value.approval.status === "awaiting_confirmation" && (value.approval.action !== null || value.approval.source !== null)) return null;
  if (value.approval.status === "cancelled" && value.approval.action !== "cancel") return null;
  return {
    version: 1,
    kind: "data_query_labels",
    service_name: "data_query",
    snapshot_id: value.snapshot_id,
    data_source_id: value.data_source_id,
    turn_id: value.turn_id,
    retrieval_digest: value.retrieval_digest,
    binding_fingerprint: value.binding_fingerprint,
    intent: value.intent,
    summary: value.summary === undefined ? undefined : value.summary,
    confidence: value.confidence === undefined ? undefined : value.confidence,
    ambiguities: value.ambiguities,
    labels: labels as QueryIntentLabel[],
    evidence: evidence as QueryIntentEvidence[],
    approval: {
      version: 1,
      snapshot_id: value.approval.snapshot_id,
      status: value.approval.status,
      action: value.approval.action,
      source: value.approval.source,
    },
    ...(value.human_input !== undefined ? { human_input: value.human_input } : {}),
  };
}

export function extractDataQueryLabelsArtifact(message: Message): QueryIntentArtifact | null {
  if (message.type !== "tool" || message.name !== "publish_query_labels") return null;
  return parseDataQueryLabelsArtifact(Reflect.get(message, "artifact"));
}

export function isDataQueryLabelsToolMessage(message: Message): boolean {
  return extractDataQueryLabelsArtifact(message) !== null;
}

export function parseDataQuerySqlResultArtifact(value: unknown): QuerySqlResultArtifact | null {
  if (!isRecord(value)) return null;
  if (
    value.version !== 1 ||
    value.kind !== "data_query_sql_result" ||
    value.service_name !== "data_query" ||
    !isBoundedString(value.snapshot_id, 200) ||
    !isBoundedString(value.data_source_id, 128) ||
    !isRecord(value.validation) ||
    value.validation.valid !== true ||
    !isBoundedString(value.validation.executable_sql, 50_000) ||
    !isBoundedString(value.validation.sql_sha256, 200) ||
    !isBoundedString(value.validation.validation_digest, 200) ||
    value.validation.snapshot_id !== value.snapshot_id ||
    (value.validation.database_type !== "postgresql" && value.validation.database_type !== "mysql") ||
    !isBoundedString(value.validation.binding_fingerprint, 200)
  ) return null;

  let execution: QuerySqlResultArtifact["execution"] = null;
  if (value.execution !== undefined && value.execution !== null) {
    if (!isRecord(value.execution)) return null;
    if (
      value.execution.version !== 1 ||
      value.execution.snapshot_id !== value.snapshot_id ||
      value.execution.validation_digest !== value.validation.validation_digest
    ) return null;
    if (value.execution.ok === false && typeof value.execution.error_code === "string" && SQL_EXECUTION_ERROR_CODES.has(value.execution.error_code)) {
      if (value.execution.duration_ms !== undefined && (typeof value.execution.duration_ms !== "number" || !Number.isFinite(value.execution.duration_ms) || value.execution.duration_ms < 0)) return null;
      execution = {
        version: 1,
        ok: false,
        snapshot_id: value.execution.snapshot_id,
        validation_digest: value.execution.validation_digest,
        error_code: value.execution.error_code,
        ...(typeof value.execution.duration_ms === "number" ? { duration_ms: value.execution.duration_ms } : {}),
      };
    } else {
      if (value.execution.ok !== true) return null;
      if (!Array.isArray(value.execution.columns) || value.execution.columns.length > 200 || value.execution.columns.some((item) => !isBoundedString(item, 200))) return null;
      if (!Array.isArray(value.execution.rows) || value.execution.rows.length > 500 || value.execution.rows.some((row) => !isRecord(row))) return null;
      try {
        if (JSON.stringify(value.execution.rows).length > 100_000) return null;
      } catch {
        return null;
      }
      const rowCount = value.execution.row_count;
      const returnedRowCount = value.execution.returned_row_count;
      if (typeof rowCount !== "number" || !Number.isInteger(rowCount) || rowCount < 0) return null;
      if (typeof returnedRowCount !== "number" || !Number.isInteger(returnedRowCount) || returnedRowCount < 0) return null;
      if (typeof value.execution.truncated !== "boolean" || typeof value.execution.empty !== "boolean") return null;
      if (value.execution.duration_ms !== undefined && (typeof value.execution.duration_ms !== "number" || !Number.isFinite(value.execution.duration_ms) || value.execution.duration_ms < 0)) return null;
      if (returnedRowCount !== value.execution.rows.length || returnedRowCount > rowCount) return null;
      if (value.execution.empty !== (rowCount === 0)) return null;
      execution = {
        version: 1,
        ok: true,
        snapshot_id: value.execution.snapshot_id,
        validation_digest: value.execution.validation_digest,
        columns: value.execution.columns as string[],
        rows: value.execution.rows as Record<string, unknown>[],
        row_count: rowCount,
        returned_row_count: returnedRowCount,
        truncated: value.execution.truncated,
        empty: value.execution.empty,
        ...(typeof value.execution.duration_ms === "number" ? { duration_ms: value.execution.duration_ms } : {}),
      };
    }
  }
  return {
    version: 1,
    kind: "data_query_sql_result",
    service_name: "data_query",
    snapshot_id: value.snapshot_id,
    data_source_id: value.data_source_id,
    validation: {
      valid: true,
      executable_sql: value.validation.executable_sql,
      sql_sha256: value.validation.sql_sha256,
      validation_digest: value.validation.validation_digest,
      snapshot_id: value.validation.snapshot_id,
      database_type: value.validation.database_type,
      binding_fingerprint: value.validation.binding_fingerprint,
    },
    execution,
  };
}

export function extractDataQuerySqlResultArtifact(message: Message): QuerySqlResultArtifact | null {
  if (message.type !== "tool" || message.name !== "task") return null;
  return parseDataQuerySqlResultArtifact(Reflect.get(message, "artifact"));
}

export function isDataQuerySqlResultToolMessage(message: Message): boolean {
  return extractDataQuerySqlResultArtifact(message) !== null;
}
