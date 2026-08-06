import type { Message } from "@langchain/langgraph-sdk";

export type QueryLabelSource = "user" | "database" | "derived";

export type QueryIntentLabel = {
  label: string;
  value: string;
  source: QueryLabelSource;
  normalized?: string;
  evidence?: string;
};

export type QueryIntentEvidence = Record<string, unknown>;

export type QueryIntentApprovalPolicy = {
  flow_id: string;
  required: boolean;
  reason: string;
  next_tool: string | null;
};

export type QueryIntentArtifact = {
  intent: string;
  summary?: string | null;
  labels: QueryIntentLabel[];
  evidence: QueryIntentEvidence[];
  approval: QueryIntentApprovalPolicy;
};

export type QueryIntentApprovalArtifact = {
  version: 1;
  kind: "data_query_intent_approval";
  service_name: "data_query";
  flow_id: string;
  human_input: unknown;
  approval: {
    version: 1;
    flow_id: string;
    status: "awaiting_confirmation" | "approved" | "cancelled";
    action: "execute" | "sql_only" | "cancel" | null;
    source: "human" | null;
    request_id: string;
    tool_call_id: string;
  };
  approval_result?: Record<string, unknown>;
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

const DATA_QUERY_INTERNAL_KINDS = new Set([
  "data_query_labels",
  "data_query_review_response",
  "data_query_sql_request",
  "data_query_sql_response",
  "data_query_sql_result",
]);

const DATA_QUERY_INTERNAL_ERROR_LABELS: Record<string, string> = {
  SQL_STAGE_NOT_APPROVED: "SQL 阶段尚未获得查询意图确认",
  SQL_SUBAGENT_FAILED: "SQL 子任务执行失败",
  SQL_SUBAGENT_CONTRACT_INVALID: "SQL 子任务返回格式不符合 DataAgent 合同",
  SQL_SUBAGENT_TOOL_RESULT_INVALID: "SQL 子任务未产生有效的校验/执行结果",
  SQL_BINDING_MISMATCH: "SQL 绑定与当前查询快照不一致",
  SQL_DSN_MISSING: "数据库连接配置缺失",
  SQL_EXECUTION_FAILED: "SQL 执行失败",
  SQL_TIMEOUT: "SQL 执行超时",
  SQL_CANCELLED: "SQL 执行已取消",
  SQL_EXECUTION_ALREADY_ATTEMPTED: "SQL 已执行过，拒绝重复执行",
};

const QUERY_INTENT_LABELS: Record<string, string> = {
  aggregation: "聚合统计",
  ranking: "排序查询",
  trend: "趋势分析",
  detail: "明细查询",
  comparison: "对比分析",
  drilldown: "下钻分析",
};

function parseJsonRecordText(text: string): Record<string, unknown> | null {
  const trimmed = text.trim();
  if (!trimmed.startsWith("{") || !trimmed.endsWith("}")) return null;
  try {
    const parsed: unknown = JSON.parse(trimmed);
    return isRecord(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function looksLikeEntityExtractPayload(
  value: Record<string, unknown>,
): boolean {
  const queryContext = isRecord(value.query_context)
    ? value.query_context
    : value;
  return (
    typeof queryContext.original_query === "string" &&
    typeof queryContext.intent === "string" &&
    (Array.isArray(queryContext.entities) || Array.isArray(queryContext.labels))
  );
}

export function formatDataQueryInternalErrorCode(
  errorCode: string,
): string | null {
  if (!errorCode.startsWith("SQL_")) return null;
  const label = DATA_QUERY_INTERNAL_ERROR_LABELS[errorCode] ?? "SQL 子任务失败";
  return `${label}（${errorCode}）`;
}

export function summarizeDataQueryInternalPayloadText(
  text: string,
): string | null {
  const payload = parseJsonRecordText(text);
  if (!payload) return null;

  const errorCode = payload.error_code;
  if (
    payload.version === 1 &&
    payload.ok === false &&
    typeof errorCode === "string" &&
    errorCode.startsWith("SQL_")
  ) {
    return formatDataQueryInternalErrorCode(errorCode);
  }

  if (looksLikeEntityExtractPayload(payload)) {
    return "实体抽取结果已进入查询标签流程。";
  }

  if (
    typeof payload.ok === "boolean" &&
    ("error" in payload ||
      "intent" in payload ||
      "labels" in payload ||
      "query_context" in payload)
  ) {
    if (payload.ok === false) {
      const rawError = payload.error;
      if (typeof rawError === "string" && rawError.trim()) {
        const summary =
          formatDataQueryInternalErrorCode(rawError.trim()) ?? rawError.trim();
        return `内部工具执行失败，已隐藏协议内容。${summary ? `(${summary})` : ""}`;
      }
      return "内部工具执行失败，已隐藏协议内容。";
    }
    return "内部工具结果已转为结构化展示。";
  }

  const kind = payload.kind;
  if (
    payload.version !== 1 ||
    typeof kind !== "string" ||
    !DATA_QUERY_INTERNAL_KINDS.has(kind)
  ) {
    return null;
  }

  switch (kind) {
    case "data_query_labels":
      return "查询意图已转为标签卡片展示。";
    case "data_query_review_response":
      return "查询意图确认已记录。";
    case "data_query_sql_result":
      return "SQL 查询结果已转为结构化卡片展示。";
    case "data_query_sql_response":
      return "SQL 子任务返回了未完成的内部响应，已隐藏协议内容。";
    case "data_query_sql_request":
      return "SQL 子任务请求已提交，已隐藏内部协议内容。";
    default:
      return "DataAgent 内部协议内容已隐藏。";
  }
}

export function isDataQueryInternalPayloadText(text: string): boolean {
  return summarizeDataQueryInternalPayloadText(text) !== null;
}

export function formatQueryIntentLabel(intent: string): string {
  return QUERY_INTENT_LABELS[intent] ?? "查询意图";
}

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
    value.normalized !== undefined &&
    value.normalized !== null &&
    !isBoundedString(value.normalized, 200)
  )
    return null;
  if (
    value.evidence !== undefined &&
    value.evidence !== null &&
    !isBoundedString(value.evidence, 500)
  )
    return null;
  return {
    label: value.label,
    value: value.value,
    source: value.source,
    ...(value.normalized ? { normalized: value.normalized } : {}),
    ...(value.evidence ? { evidence: value.evidence } : {}),
  };
}

function parseEvidence(value: unknown): QueryIntentEvidence | null {
  return isRecord(value) ? value : null;
}

function parseApprovalPolicy(value: unknown): QueryIntentApprovalPolicy | null {
  if (!isRecord(value)) return null;
  if (
    !isBoundedString(value.flow_id, 200) ||
    typeof value.required !== "boolean" ||
    !isBoundedString(value.reason, 500) ||
    (value.next_tool !== null &&
      value.next_tool !== undefined &&
      !isBoundedString(value.next_tool, 100))
  ) {
    return null;
  }
  return {
    flow_id: value.flow_id,
    required: value.required,
    reason: value.reason,
    next_tool: typeof value.next_tool === "string" ? value.next_tool : null,
  };
}

export function parseDataQueryLabelsArtifact(
  value: unknown,
): QueryIntentArtifact | null {
  if (!isRecord(value)) return null;
  if (!isBoundedString(value.intent, 100)) {
    return null;
  }
  if (
    value.summary !== undefined &&
    value.summary !== null &&
    !isBoundedString(value.summary, 500)
  )
    return null;
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
  const approval = parseApprovalPolicy(value.approval);
  if (!approval) return null;
  return {
    intent: value.intent,
    summary: value.summary === undefined ? undefined : value.summary,
    labels: labels as QueryIntentLabel[],
    evidence: evidence as QueryIntentEvidence[],
    approval,
  };
}

/**
 * 解析 ask_intent_approval 工具返回的新审批 artifact。
 *
 * Args:
 *   value: ToolMessage.artifact 原始值。
 *
 * Returns:
 *   合法审批 artifact；格式不匹配时返回 null。
 */
export function parseDataQueryIntentApprovalArtifact(
  value: unknown,
): QueryIntentApprovalArtifact | null {
  if (
    !isRecord(value) ||
    value.version !== 1 ||
    value.kind !== "data_query_intent_approval" ||
    value.service_name !== "data_query" ||
    !isBoundedString(value.flow_id, 200) ||
    !isRecord(value.approval)
  ) {
    return null;
  }

  const approval = value.approval;
  if (
    approval.version !== 1 ||
    approval.flow_id !== value.flow_id ||
    (approval.status !== "awaiting_confirmation" &&
      approval.status !== "approved" &&
      approval.status !== "cancelled") ||
    (approval.action !== null &&
      approval.action !== "execute" &&
      approval.action !== "sql_only" &&
      approval.action !== "cancel") ||
    (approval.source !== null && approval.source !== "human") ||
    !isBoundedString(approval.request_id, 200) ||
    !isBoundedString(approval.tool_call_id, 200)
  ) {
    return null;
  }

  return {
    version: 1,
    kind: "data_query_intent_approval",
    service_name: "data_query",
    flow_id: value.flow_id,
    human_input: value.human_input,
    approval: {
      version: 1,
      flow_id: approval.flow_id,
      status: approval.status,
      action: approval.action,
      source: approval.source,
      request_id: approval.request_id,
      tool_call_id: approval.tool_call_id,
    },
    ...(isRecord(value.approval_result)
      ? { approval_result: value.approval_result }
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

export function extractDataQueryIntentApprovalArtifact(
  message: Message,
): QueryIntentApprovalArtifact | null {
  if (message.type !== "tool" || message.name !== "ask_intent_approval")
    return null;
  return parseDataQueryIntentApprovalArtifact(readMessageArtifact(message));
}

export function isDataQueryLabelsToolMessage(message: Message): boolean {
  return extractDataQueryLabelsArtifact(message) !== null;
}

export function isDataQueryIntentApprovalToolMessage(
  message: Message,
): boolean {
  return extractDataQueryIntentApprovalArtifact(message) !== null;
}

export function findLatestDataQueryIntentMessage(messages: Message[]) {
  return (
    [...messages]
      .reverse()
      .find((message) => extractDataQueryLabelsArtifact(message) !== null) ??
    null
  );
}

export function findLatestDataQueryIntentApprovalMessage(messages: Message[]) {
  return (
    [...messages]
      .reverse()
      .find(
        (message) => extractDataQueryIntentApprovalArtifact(message) !== null,
      ) ?? null
  );
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
