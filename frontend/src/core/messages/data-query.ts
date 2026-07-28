import type { Message } from "@langchain/langgraph-sdk";

import type { HumanInputRequest, HumanInputResponse } from "./human-input";

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

export type QueryIntentReviewItem = {
  id: string;
  question: string;
  status: "pending" | "accepted" | "modified";
  options: { id: "accept" | "modify"; label: string; value: string }[];
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
  ambiguity_items: QueryIntentReviewItem[];
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
  execution:
    | {
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
      }
    | {
        version: 1;
        ok: false;
        snapshot_id: string;
        validation_digest: string;
        error_code: string;
        error_category?: string;
        error_message?: string;
        retryable?: boolean;
        recommended_action?: string;
        duration_ms?: number;
      }
    | null;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function readMessageArtifact(message: Message): unknown {
  const direct = Reflect.get(message, "artifact");
  if (direct !== undefined) return direct;
  const additionalKwargs = message.additional_kwargs;
  return isRecord(additionalKwargs) ? additionalKwargs.artifact : undefined;
}

function isBoundedString(value: unknown, max: number): value is string {
  return (
    typeof value === "string" && value.trim().length > 0 && value.length <= max
  );
}

const SQL_EXECUTION_ERROR_CODES = new Set([
  "SQL_BINDING_MISMATCH",
  "SQL_DSN_MISSING",
  "SQL_EXECUTION_FAILED",
  "SQL_TIMEOUT",
  "SQL_CANCELLED",
  "SQL_EXECUTION_ALREADY_ATTEMPTED",
]);

function parseLabel(value: unknown): QueryIntentLabel | null {
  if (!isRecord(value)) return null;
  if (!isBoundedString(value.label, 50) || !isBoundedString(value.value, 200))
    return null;
  if (
    value.source !== "user" &&
    value.source !== "database" &&
    value.source !== "derived"
  )
    return null;
  if (
    !Array.isArray(value.evidence_refs) ||
    value.evidence_refs.length > 20 ||
    value.evidence_refs.some((ref) => !isBoundedString(ref, 200))
  )
    return null;
  if (
    value.normalized !== undefined &&
    value.normalized !== null &&
    !isBoundedString(value.normalized, 200)
  )
    return null;
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
  if (
    !isBoundedString(value.ref, 200) ||
    !isBoundedString(value.kind, 50) ||
    !isBoundedString(value.summary, 500)
  )
    return null;
  return { ref: value.ref, kind: value.kind, summary: value.summary };
}

function parseReviewItems(
  value: unknown,
  ambiguities: string[],
): QueryIntentReviewItem[] | null {
  if (value === undefined) {
    return ambiguities.map((question, index) => ({
      id: `legacy-ambiguity-${index}`,
      question,
      status: "pending",
      options: [
        { id: "accept", label: "按当前理解继续", value: "accept" },
        { id: "modify", label: "修改这一项", value: "modify" },
      ],
    }));
  }
  if (!Array.isArray(value) || value.length > 20) return null;
  const items: QueryIntentReviewItem[] = [];
  for (const item of value) {
    if (
      !isRecord(item) ||
      !isBoundedString(item.id, 200) ||
      !isBoundedString(item.question, 500)
    )
      return null;
    if (
      item.status !== "pending" &&
      item.status !== "accepted" &&
      item.status !== "modified"
    )
      return null;
    if (
      !Array.isArray(item.options) ||
      item.options.length < 2 ||
      item.options.length > 4
    )
      return null;
    const options = item.options.map((option) => {
      if (
        !isRecord(option) ||
        (option.id !== "accept" && option.id !== "modify") ||
        !isBoundedString(option.label, 100) ||
        !isBoundedString(option.value, 100)
      )
        return null;
      return { id: option.id, label: option.label, value: option.value };
    });
    if (options.some((option) => option === null)) return null;
    items.push({
      id: item.id,
      question: item.question,
      status: item.status,
      options: options as QueryIntentReviewItem["options"],
    });
  }
  return items;
}

export function parseDataQueryLabelsArtifact(
  value: unknown,
): QueryIntentArtifact | null {
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
  if (
    value.summary !== undefined &&
    value.summary !== null &&
    !isBoundedString(value.summary, 500)
  )
    return null;
  if (
    value.confidence !== undefined &&
    value.confidence !== null &&
    (typeof value.confidence !== "number" ||
      !Number.isFinite(value.confidence) ||
      value.confidence < 0 ||
      value.confidence > 1)
  )
    return null;
  if (
    !Array.isArray(value.ambiguities) ||
    value.ambiguities.length > 20 ||
    value.ambiguities.some((item) => !isBoundedString(item, 200))
  )
    return null;
  const ambiguityItems = parseReviewItems(
    value.ambiguity_items,
    value.ambiguities,
  );
  if (ambiguityItems?.length !== value.ambiguities.length) return null;
  if (
    !Array.isArray(value.labels) ||
    value.labels.length === 0 ||
    value.labels.length > 30
  )
    return null;
  if (!Array.isArray(value.evidence) || value.evidence.length > 30) return null;
  const labels = value.labels.map(parseLabel);
  const evidence = value.evidence.map(parseEvidence);
  if (
    labels.some((label) => label === null) ||
    evidence.some((item) => item === null)
  )
    return null;
  if (
    !isRecord(value.approval) ||
    value.approval.version !== 1 ||
    value.approval.snapshot_id !== value.snapshot_id
  )
    return null;
  if (
    value.approval.status !== "approved" &&
    value.approval.status !== "awaiting_confirmation" &&
    value.approval.status !== "cancelled"
  )
    return null;
  if (
    value.approval.action !== null &&
    value.approval.action !== "execute" &&
    value.approval.action !== "sql_only" &&
    value.approval.action !== "cancel"
  )
    return null;
  if (
    value.approval.source !== null &&
    value.approval.source !== "model" &&
    value.approval.source !== "human"
  )
    return null;
  if (
    value.approval.status === "approved" &&
    value.approval.action !== "execute" &&
    value.approval.action !== "sql_only"
  )
    return null;
  if (
    value.approval.status === "awaiting_confirmation" &&
    (value.approval.action !== null || value.approval.source !== null)
  )
    return null;
  if (
    value.approval.status === "cancelled" &&
    value.approval.action !== "cancel"
  )
    return null;
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
    ambiguity_items: ambiguityItems,
    labels: labels as QueryIntentLabel[],
    evidence: evidence as QueryIntentEvidence[],
    approval: {
      version: 1,
      snapshot_id: value.approval.snapshot_id,
      status: value.approval.status,
      action: value.approval.action,
      source: value.approval.source,
    },
    ...(value.human_input !== undefined
      ? { human_input: value.human_input }
      : {}),
  };
}

export function extractDataQueryLabelsArtifact(
  message: Message,
): QueryIntentArtifact | null {
  if (message.type !== "tool" || message.name !== "publish_query_labels")
    return null;
  return parseDataQueryLabelsArtifact(readMessageArtifact(message));
}

export function isDataQueryLabelsToolMessage(message: Message): boolean {
  return extractDataQueryLabelsArtifact(message) !== null;
}

export function parseDataQuerySqlResultArtifact(
  value: unknown,
): QuerySqlResultArtifact | null {
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
    (value.validation.database_type !== "postgresql" &&
      value.validation.database_type !== "mysql") ||
    !isBoundedString(value.validation.binding_fingerprint, 200)
  )
    return null;

  let execution: QuerySqlResultArtifact["execution"] = null;
  if (value.execution !== undefined && value.execution !== null) {
    if (!isRecord(value.execution)) return null;
    if (
      value.execution.version !== 1 ||
      value.execution.snapshot_id !== value.snapshot_id ||
      value.execution.validation_digest !== value.validation.validation_digest
    )
      return null;
    if (
      value.execution.ok === false &&
      typeof value.execution.error_code === "string" &&
      SQL_EXECUTION_ERROR_CODES.has(value.execution.error_code)
    ) {
      if (
        value.execution.duration_ms !== undefined &&
        (typeof value.execution.duration_ms !== "number" ||
          !Number.isFinite(value.execution.duration_ms) ||
          value.execution.duration_ms < 0)
      )
        return null;
      if (
        value.execution.error_category !== undefined &&
        value.execution.error_category !== null &&
        !isBoundedString(value.execution.error_category, 100)
      )
        return null;
      if (
        value.execution.error_message !== undefined &&
        value.execution.error_message !== null &&
        !isBoundedString(value.execution.error_message, 500)
      )
        return null;
      if (
        value.execution.retryable !== undefined &&
        typeof value.execution.retryable !== "boolean"
      )
        return null;
      if (
        value.execution.recommended_action !== undefined &&
        value.execution.recommended_action !== null &&
        !isBoundedString(value.execution.recommended_action, 100)
      )
        return null;
      execution = {
        version: 1,
        ok: false,
        snapshot_id: value.execution.snapshot_id,
        validation_digest: value.execution.validation_digest,
        error_code: value.execution.error_code,
        ...(typeof value.execution.error_category === "string"
          ? { error_category: value.execution.error_category }
          : {}),
        ...(typeof value.execution.error_message === "string"
          ? { error_message: value.execution.error_message }
          : {}),
        ...(typeof value.execution.retryable === "boolean"
          ? { retryable: value.execution.retryable }
          : {}),
        ...(typeof value.execution.recommended_action === "string"
          ? { recommended_action: value.execution.recommended_action }
          : {}),
        ...(typeof value.execution.duration_ms === "number"
          ? { duration_ms: value.execution.duration_ms }
          : {}),
      };
    } else {
      if (value.execution.ok !== true) return null;
      if (
        !Array.isArray(value.execution.columns) ||
        value.execution.columns.length > 200 ||
        value.execution.columns.some((item) => !isBoundedString(item, 200))
      )
        return null;
      if (
        !Array.isArray(value.execution.rows) ||
        value.execution.rows.length > 500 ||
        value.execution.rows.some((row) => !isRecord(row))
      )
        return null;
      try {
        if (JSON.stringify(value.execution.rows).length > 100_000) return null;
      } catch {
        return null;
      }
      const rowCount = value.execution.row_count;
      const returnedRowCount = value.execution.returned_row_count;
      if (
        typeof rowCount !== "number" ||
        !Number.isInteger(rowCount) ||
        rowCount < 0
      )
        return null;
      if (
        typeof returnedRowCount !== "number" ||
        !Number.isInteger(returnedRowCount) ||
        returnedRowCount < 0
      )
        return null;
      if (
        typeof value.execution.truncated !== "boolean" ||
        typeof value.execution.empty !== "boolean"
      )
        return null;
      if (
        value.execution.duration_ms !== undefined &&
        (typeof value.execution.duration_ms !== "number" ||
          !Number.isFinite(value.execution.duration_ms) ||
          value.execution.duration_ms < 0)
      )
        return null;
      if (
        returnedRowCount !== value.execution.rows.length ||
        returnedRowCount > rowCount
      )
        return null;
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
        ...(typeof value.execution.duration_ms === "number"
          ? { duration_ms: value.execution.duration_ms }
          : {}),
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

export function extractDataQuerySqlResultArtifact(
  message: Message,
): QuerySqlResultArtifact | null {
  if (message.type !== "tool" || message.name !== "task") return null;
  return parseDataQuerySqlResultArtifact(readMessageArtifact(message));
}

export function isDataQuerySqlResultToolMessage(message: Message): boolean {
  return extractDataQuerySqlResultArtifact(message) !== null;
}

export type QueryIntentReviewDecision = {
  id: string;
  decision: "accept" | "modify";
  value?: string;
};

export function createDataQueryReviewResponse(
  request: HumanInputRequest,
  artifact: QueryIntentArtifact,
  decisions: QueryIntentReviewDecision[],
  finalAction: "execute" | "sql_only" | "cancel",
): HumanInputResponse {
  return {
    version: 1,
    kind: "human_input_response",
    source: request.source,
    request_id: request.request_id,
    response_kind: "text",
    value: JSON.stringify(
      {
        kind: "data_query_review_response",
        snapshot_id: artifact.snapshot_id,
        final_action: finalAction,
        items: decisions,
      },
      null,
      0,
    ),
  };
}

export function parseDataQueryReviewFinalAction(
  response: HumanInputResponse | null,
): "execute" | "sql_only" | "cancel" | "modify" | null {
  if (!response) return null;
  if (response.response_kind === "option") {
    return response.option_id === "execute" ||
      response.option_id === "sql_only" ||
      response.option_id === "cancel"
      ? response.option_id
      : null;
  }
  try {
    const value: unknown = JSON.parse(response.value);
    if (
      isRecord(value) &&
      value.kind === "data_query_review_response" &&
      (value.final_action === "execute" ||
        value.final_action === "sql_only" ||
        value.final_action === "cancel")
    )
      return value.final_action;
  } catch {
    return "modify";
  }
  return "modify";
}

export function parseDataQueryReviewDecisions(
  response: HumanInputResponse | null,
): Record<string, QueryIntentReviewDecision> {
  if (response?.response_kind !== "text") return {};
  try {
    const value: unknown = JSON.parse(response.value);
    if (
      !isRecord(value) ||
      value.kind !== "data_query_review_response" ||
      !Array.isArray(value.items)
    )
      return {};
    return Object.fromEntries(
      value.items
        .filter(
          (item): item is Record<string, unknown> =>
            isRecord(item) &&
            typeof item.id === "string" &&
            (item.decision === "accept" || item.decision === "modify"),
        )
        .map((item) => [
          item.id,
          {
            id: item.id as string,
            decision: item.decision as "accept" | "modify",
            ...(typeof item.value === "string" ? { value: item.value } : {}),
          },
        ]),
    );
  } catch {
    return {};
  }
}
