import type { Message } from "@langchain/langgraph-sdk";

export type HumanInputMode =
  | "free_text"
  | "single_choice"
  | "choice_with_other"
  | "multi_question_choice"
  | "form";

export type HumanInputOption = {
  id: string;
  label: string;
  value: string;
};

export type HumanInputQuestion = {
  id: string;
  question: string;
  options: HumanInputOption[];
};

export type HumanInputAnswer = {
  question_id: string;
  option_id: string;
  value: string;
};

export type IntentApprovalAnswersPayload = {
  kind: "intent_approval_answers";
  flow_id: string;
  answers: HumanInputAnswer[];
  final_action: "execute" | "sql_only" | "cancel";
};

export type HumanInputFieldType =
  | "text"
  | "textarea"
  | "number"
  | "select"
  | "multi_select"
  | "checkbox"
  | "date";

export type HumanInputField = {
  name: string;
  label: string;
  type: HumanInputFieldType;
  required: boolean;
  placeholder?: string;
  options?: HumanInputOption[];
};

export type HumanInputFormValue = string | number | boolean | string[];

export type HumanInputRequest = {
  version: 1 | 2;
  kind: "human_input_request";
  source: "ask_clarification" | string;
  request_id: string;
  flow_id?: string;
  tool_call_id?: string;
  snapshot_id?: string;
  clarification_type?: string;
  title?: string;
  question?: string;
  context?: string | null;
  input_mode: HumanInputMode;
  options?: HumanInputOption[];
  questions?: HumanInputQuestion[];
  fields?: HumanInputField[];
};

export type HumanInputResponse =
  | {
      version: 1;
      kind: "human_input_response";
      source: string;
      request_id: string;
      flow_id?: string;
      response_kind: "option";
      option_id: string;
      value: string;
    }
  | {
      version: 1;
      kind: "human_input_response";
      source: string;
      request_id: string;
      flow_id?: string;
      response_kind: "text";
      value: string;
    };

export type HumanInputThreadState = {
  answeredResponses: Map<string, HumanInputResponse>;
  latestOpenRequestId: string | null;
};

export function shouldClearPendingHumanInputOnThreadError({
  currentError,
  pendingRequestCount,
  previousError,
}: {
  currentError: unknown;
  pendingRequestCount: number;
  previousError: unknown;
}) {
  return (
    pendingRequestCount > 0 &&
    currentError != null &&
    !Object.is(currentError, previousError)
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isHumanInputMode(value: unknown): value is HumanInputMode {
  return (
    value === "free_text" ||
    value === "single_choice" ||
    value === "choice_with_other" ||
    value === "multi_question_choice" ||
    value === "form"
  );
}

// Field names that collide with JavaScript Object.prototype members. Form
// values live in a plain object keyed by field name, so these would resolve
// to inherited properties instead of user input.
const RESERVED_FIELD_NAMES = new Set([
  "__proto__",
  "constructor",
  "prototype",
  "toString",
  "toLocaleString",
  "valueOf",
  "hasOwnProperty",
  "isPrototypeOf",
  "propertyIsEnumerable",
  "__defineGetter__",
  "__defineSetter__",
  "__lookupGetter__",
  "__lookupSetter__",
]);

export function readHumanInputFormValue(
  values: Record<string, HumanInputFormValue>,
  name: string,
): HumanInputFormValue | undefined {
  return Object.prototype.hasOwnProperty.call(values, name)
    ? values[name]
    : undefined;
}

export function buildInitialHumanInputFormValues(
  fields: HumanInputField[],
): Record<string, HumanInputFormValue> {
  // Checkboxes are booleans that default to an explicit "no" — without the
  // seed an untouched checkbox would be indistinguishable from an unanswered
  // field and silently vanish from the submitted summary.
  const values: Record<string, HumanInputFormValue> = {};
  for (const field of fields) {
    if (field.type === "checkbox") {
      values[field.name] = false;
    }
  }
  return values;
}

function isHumanInputFieldType(value: unknown): value is HumanInputFieldType {
  return (
    value === "text" ||
    value === "textarea" ||
    value === "number" ||
    value === "select" ||
    value === "multi_select" ||
    value === "checkbox" ||
    value === "date"
  );
}

function readOptionalString(value: unknown) {
  return typeof value === "string" ? value : undefined;
}

function parseOptions(value: unknown): HumanInputOption[] | undefined {
  if (value === undefined) {
    return undefined;
  }
  if (!Array.isArray(value)) {
    return undefined;
  }

  const options: HumanInputOption[] = [];
  const seenIds = new Set<string>();
  const seenValues = new Set<string>();
  for (const option of value) {
    if (!isRecord(option)) {
      return undefined;
    }
    const id = option.id;
    const label = option.label;
    const optionValue = option.value;
    if (
      !isNonEmptyString(id) ||
      !isNonEmptyString(label) ||
      // An empty option value crashes Radix <SelectItem value="">; a
      // malformed/replayed artifact must fall back to plain text instead.
      !isNonEmptyString(optionValue) ||
      seenIds.has(id) ||
      seenValues.has(optionValue)
    ) {
      return undefined;
    }
    seenIds.add(id);
    seenValues.add(optionValue);
    options.push({ id, label, value: optionValue });
  }
  return options;
}

/**
 * 解析多问题选择请求。
 *
 * Args:
 *   value: 后端 human_input.questions 原始值。
 *
 * Returns:
 *   校验后的问题数组；格式无效时返回 undefined。
 */
function parseQuestions(value: unknown): HumanInputQuestion[] | undefined {
  if (!Array.isArray(value) || value.length === 0) {
    return undefined;
  }

  const questions: HumanInputQuestion[] = [];
  const questionIds = new Set<string>();
  for (const item of value) {
    if (!isRecord(item)) {
      return undefined;
    }
    const id = item.id;
    const question = item.question;
    const options = parseOptions(item.options);
    if (
      !isNonEmptyString(id) ||
      questionIds.has(id) ||
      !isNonEmptyString(question) ||
      !options ||
      options.length === 0
    ) {
      return undefined;
    }

    const optionIds = new Set<string>();
    for (const option of options) {
      if (optionIds.has(option.id)) {
        return undefined;
      }
      optionIds.add(option.id);
    }

    questionIds.add(id);
    questions.push({ id, question, options });
  }
  return questions;
}

function parseFields(value: unknown): HumanInputField[] | undefined {
  if (value === undefined) {
    return undefined;
  }
  if (!Array.isArray(value)) {
    return undefined;
  }

  const fields: HumanInputField[] = [];
  const seenNames = new Set<string>();
  for (const field of value) {
    if (!isRecord(field)) {
      return undefined;
    }
    const name = field.name;
    if (typeof name === "string" && seenNames.has(name)) {
      return undefined;
    }
    if (typeof name === "string") {
      seenNames.add(name);
    }
    const label = field.label;
    const type = field.type;
    const required = field.required;
    if (
      !isNonEmptyString(name) ||
      RESERVED_FIELD_NAMES.has(name) ||
      !isNonEmptyString(label) ||
      !isHumanInputFieldType(type) ||
      (required !== undefined && typeof required !== "boolean")
    ) {
      return undefined;
    }
    const options = parseOptions(field.options);
    if (field.options !== undefined && options === undefined) {
      return undefined;
    }
    if (
      (type === "select" || type === "multi_select") &&
      (!options || options.length === 0)
    ) {
      return undefined;
    }
    fields.push({
      name,
      label,
      type,
      required: required === true,
      ...(readOptionalString(field.placeholder)
        ? { placeholder: readOptionalString(field.placeholder) }
        : {}),
      ...(options ? { options } : {}),
    });
  }
  return fields;
}

export function parseHumanInputRequest(
  value: unknown,
): HumanInputRequest | null {
  if (!isRecord(value)) {
    return null;
  }
  if (
    (value.version !== 1 && value.version !== 2) ||
    value.kind !== "human_input_request" ||
    !isNonEmptyString(value.source) ||
    !isNonEmptyString(value.request_id) ||
    !isHumanInputMode(value.input_mode)
  ) {
    return null;
  }

  const options = parseOptions(value.options);
  if (value.options !== undefined && options === undefined) {
    return null;
  }
  if (
    (value.input_mode === "single_choice" ||
      value.input_mode === "choice_with_other") &&
    (!options || options.length === 0)
  ) {
    return null;
  }

  const questions = parseQuestions(value.questions);
  if (
    value.input_mode === "multi_question_choice" &&
    (!isNonEmptyString(value.flow_id) || !questions)
  ) {
    return null;
  }
  if (
    value.input_mode !== "multi_question_choice" &&
    !isNonEmptyString(value.question)
  ) {
    return null;
  }
  if (
    value.questions !== undefined &&
    value.input_mode !== "multi_question_choice"
  ) {
    return null;
  }

  const fields = parseFields(value.fields);
  if (value.fields !== undefined && fields === undefined) {
    return null;
  }
  if (value.input_mode === "form" && (!fields || fields.length === 0)) {
    return null;
  }
  // Version/mode binding: `form` is a v2 construct and v2 defines nothing
  // else — a mismatched pair is a malformed payload, not a variant.
  if ((value.input_mode === "form") !== (value.version === 2)) {
    return null;
  }

  const context = value.context;
  if (
    context !== undefined &&
    context !== null &&
    typeof context !== "string"
  ) {
    return null;
  }

  return {
    version: value.version,
    kind: "human_input_request",
    source: value.source,
    request_id: value.request_id,
    ...(readOptionalString(value.flow_id)
      ? { flow_id: readOptionalString(value.flow_id) }
      : {}),
    ...(readOptionalString(value.tool_call_id)
      ? { tool_call_id: readOptionalString(value.tool_call_id) }
      : {}),
    ...(readOptionalString(value.snapshot_id)
      ? { snapshot_id: readOptionalString(value.snapshot_id) }
      : {}),
    ...(readOptionalString(value.clarification_type)
      ? { clarification_type: readOptionalString(value.clarification_type) }
      : {}),
    ...(readOptionalString(value.title)
      ? { title: readOptionalString(value.title) }
      : {}),
    ...(readOptionalString(value.question)
      ? { question: readOptionalString(value.question) }
      : {}),
    ...(context !== undefined ? { context } : {}),
    input_mode: value.input_mode,
    ...(options ? { options } : {}),
    ...(questions ? { questions } : {}),
    ...(fields ? { fields } : {}),
  };
}

export function parseHumanInputResponse(
  value: unknown,
): HumanInputResponse | null {
  if (!isRecord(value)) {
    return null;
  }
  if (
    value.version !== 1 ||
    value.kind !== "human_input_response" ||
    !isNonEmptyString(value.source) ||
    !isNonEmptyString(value.request_id) ||
    !isNonEmptyString(value.value)
  ) {
    return null;
  }

  const flowId = readOptionalString(value.flow_id);

  if (value.response_kind === "option") {
    if (!isNonEmptyString(value.option_id)) {
      return null;
    }
    return {
      version: 1,
      kind: "human_input_response",
      source: value.source,
      request_id: value.request_id,
      ...(flowId ? { flow_id: flowId } : {}),
      response_kind: "option",
      option_id: value.option_id,
      value: value.value,
    };
  }

  if (value.response_kind === "text") {
    return {
      version: 1,
      kind: "human_input_response",
      source: value.source,
      request_id: value.request_id,
      ...(flowId ? { flow_id: flowId } : {}),
      response_kind: "text",
      value: value.value,
    };
  }

  return null;
}

export function extractHumanInputRequest(
  message: Message,
): HumanInputRequest | null {
  if (message.type !== "tool") {
    return null;
  }
  const directArtifact = Reflect.get(message, "artifact");
  const additionalKwargs = message.additional_kwargs;
  const artifact =
    directArtifact ??
    (isRecord(additionalKwargs) ? additionalKwargs.artifact : undefined);
  if (!isRecord(artifact)) {
    return null;
  }
  return parseHumanInputRequest(artifact.human_input);
}

export function extractHumanInputResponse(
  message: Message,
): HumanInputResponse | null {
  if (message.type !== "human") {
    return null;
  }
  const additionalKwargs = message.additional_kwargs;
  if (!isRecord(additionalKwargs)) {
    return null;
  }
  return parseHumanInputResponse(additionalKwargs.human_input_response);
}

function extractPlainMessageText(message: Message): string {
  const content: unknown = message.content;
  if (typeof content === "string") {
    return content.trim();
  }
  if (Array.isArray(content)) {
    return content
      .map((part) =>
        isRecord(part) && part.type === "text" && typeof part.text === "string"
          ? part.text
          : "",
      )
      .join("")
      .trim();
  }
  return "";
}

export function deriveHumanInputThreadState(
  messages: Message[],
  isVisibleMessage: (message: Message) => boolean = (message) =>
    message.additional_kwargs?.hide_from_ui !== true,
): HumanInputThreadState {
  const answeredResponses = new Map<string, HumanInputResponse>();
  const seenRequests = new Map<string, HumanInputRequest>();
  const requestFlowIds = new Map<string, string>();
  const requestOrder: string[] = [];

  for (const message of messages) {
    if (isVisibleMessage(message)) {
      const request = extractHumanInputRequest(message);
      if (request) {
        seenRequests.set(request.request_id, request);
        if (request.flow_id) {
          requestFlowIds.set(request.request_id, request.flow_id);
        }
        requestOrder.push(request.request_id);
      }
    }

    const response = extractHumanInputResponse(message);
    if (
      response &&
      seenRequests.has(response.request_id) &&
      (!requestFlowIds.has(response.request_id) ||
        requestFlowIds.get(response.request_id) === response.flow_id) &&
      !answeredResponses.has(response.request_id)
    ) {
      answeredResponses.set(response.request_id, response);
      continue;
    }

    // Legacy-frontend fallback: a v1-only frontend renders a v2 request as
    // plain text and the user answers through the normal composer, so the
    // reply carries no human_input_response metadata. The reply closes only
    // the LATEST unanswered request — the one the user was presumably
    // answering. Nothing guarantees at most one outstanding request across
    // runs, and closing them all would silently swallow older decisions with
    // the same text; an older request left open simply becomes the active
    // card again.
    if (message.type === "human" && isVisibleMessage(message) && !response) {
      const latestUnansweredId = [...requestOrder]
        .reverse()
        .find((requestId) => !answeredResponses.has(requestId));
      const request =
        latestUnansweredId === undefined
          ? undefined
          : seenRequests.get(latestUnansweredId);
      if (latestUnansweredId !== undefined && request) {
        answeredResponses.set(latestUnansweredId, {
          version: 1,
          kind: "human_input_response",
          source: request.source,
          request_id: latestUnansweredId,
          response_kind: "text",
          value: extractPlainMessageText(message) || "-",
        });
      }
    }
  }

  const latestOpenRequestId =
    [...requestOrder]
      .reverse()
      .find((requestId) => !answeredResponses.has(requestId)) ?? null;

  return { answeredResponses, latestOpenRequestId };
}

export function hasOpenHumanInputRequest(
  messages: Message[],
  isVisibleMessage?: (message: Message) => boolean,
) {
  return (
    deriveHumanInputThreadState(messages, isVisibleMessage)
      .latestOpenRequestId !== null
  );
}

export function createHumanInputOptionResponse(
  request: HumanInputRequest,
  option: HumanInputOption,
): HumanInputResponse {
  return {
    version: 1,
    kind: "human_input_response",
    source: request.source,
    request_id: request.request_id,
    ...(request.flow_id ? { flow_id: request.flow_id } : {}),
    response_kind: "option",
    option_id: option.id,
    value: option.value,
  };
}

function formatFormValue(value: HumanInputFormValue) {
  if (Array.isArray(value)) {
    return value.join(", ");
  }
  if (typeof value === "boolean") {
    return value ? "yes" : "no";
  }
  return String(value);
}

function isEmptyFormValue(value: HumanInputFormValue | undefined) {
  if (value === undefined) {
    return true;
  }
  if (typeof value === "string") {
    return value.trim().length === 0;
  }
  if (Array.isArray(value)) {
    return value.length === 0;
  }
  return false;
}

export function buildHumanInputFormSummary(
  request: HumanInputRequest,
  values: Record<string, HumanInputFormValue>,
) {
  const fields = request.fields ?? [];
  const parts: string[] = [];
  for (const field of fields) {
    const value = readHumanInputFormValue(values, field.name);
    if (isEmptyFormValue(value)) {
      continue;
    }
    parts.push(`${field.label}: ${formatFormValue(value!)}`);
  }
  return parts.join("; ");
}

export function buildHumanInputFormSubmissionValue(
  request: HumanInputRequest,
  values: Record<string, HumanInputFormValue>,
) {
  // The readable summary alone is ambiguous ("a: x; B: y" could come from
  // several field mappings), so the submitted value appends the full record
  // as one JSON block keyed by stable field names — labels/names may contain
  // the separators themselves, so only whole-record JSON is collision-free.
  const record: Record<string, HumanInputFormValue> = {};
  for (const field of request.fields ?? []) {
    const value = readHumanInputFormValue(values, field.name);
    if (isEmptyFormValue(value)) {
      continue;
    }
    record[field.name] = value!;
  }
  return `${buildHumanInputFormSummary(request, values)} [values: ${JSON.stringify(record)}]`;
}

export function createHumanInputTextResponse(
  request: HumanInputRequest,
  value: string,
): HumanInputResponse {
  return {
    version: 1,
    kind: "human_input_response",
    source: request.source,
    request_id: request.request_id,
    ...(request.flow_id ? { flow_id: request.flow_id } : {}),
    response_kind: "text",
    value,
  };
}

/**
 * 构造多问题意图审批响应。
 *
 * Args:
 *   request: 当前多问题审批请求。
 *   answers: 每个问题对应的选项答案。
 *   finalAction: 审批完成后的动作。
 *
 * Returns:
 *   可通过 hidden human_input_response 提交的文本响应。
 */
export function createMultiQuestionChoiceResponse(
  request: HumanInputRequest,
  answers: HumanInputAnswer[],
  finalAction: IntentApprovalAnswersPayload["final_action"] = "execute",
): HumanInputResponse {
  if (!request.flow_id) {
    throw new Error("Multi-question approval requires flow_id.");
  }
  return createHumanInputTextResponse(
    request,
    JSON.stringify({
      kind: "intent_approval_answers",
      flow_id: request.flow_id,
      answers,
      final_action: finalAction,
    } satisfies IntentApprovalAnswersPayload),
  );
}

/**
 * 解析 response.value 中保存的多问题意图审批答案。
 *
 * Args:
 *   response: 人机输入响应。
 *
 * Returns:
 *   合法的意图审批答案；其他响应返回 null。
 */
export function parseIntentApprovalAnswers(
  response: HumanInputResponse | null | undefined,
): IntentApprovalAnswersPayload | null {
  if (response?.response_kind !== "text") {
    return null;
  }

  try {
    const value: unknown = JSON.parse(response.value);
    if (
      !isRecord(value) ||
      value.kind !== "intent_approval_answers" ||
      !isNonEmptyString(value.flow_id) ||
      (response.flow_id !== undefined && response.flow_id !== value.flow_id) ||
      (value.final_action !== "execute" &&
        value.final_action !== "sql_only" &&
        value.final_action !== "cancel") ||
      !Array.isArray(value.answers)
    ) {
      return null;
    }

    const answers: HumanInputAnswer[] = [];
    for (const answer of value.answers) {
      if (
        !isRecord(answer) ||
        !isNonEmptyString(answer.question_id) ||
        !isNonEmptyString(answer.option_id) ||
        typeof answer.value !== "string"
      ) {
        return null;
      }
      answers.push({
        question_id: answer.question_id,
        option_id: answer.option_id,
        value: answer.value,
      });
    }

    return {
      kind: "intent_approval_answers",
      flow_id: value.flow_id,
      answers,
      final_action: value.final_action,
    };
  } catch {
    return null;
  }
}

export function buildHumanInputResponseText(
  request: HumanInputRequest,
  response: HumanInputResponse,
) {
  const intentAnswers = parseIntentApprovalAnswers(response);
  if (intentAnswers) {
    const values = intentAnswers.answers
      .map((answer) => answer.value)
      .join("；");
    return `意图审批答案：${values}`;
  }
  return `For your clarification "${request.question ?? request.title ?? ""}", my answer is: ${response.value}`;
}
