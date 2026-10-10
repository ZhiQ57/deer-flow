import { createInterface } from "node:readline";
import { Bash, parse } from "just-bash";

const MAX_REQUEST_BYTES = 256 * 1024 * 1024;
const DEFAULT_TIMEOUT_MS = 15_000;
const sessionScopes = new Map();

function writeResponse(response) {
  process.stdout.write(`${JSON.stringify(response)}\n`);
}

function literalWord(word) {
  if (!word || word.type !== "Word" || !Array.isArray(word.parts)) {
    throw new Error("only literal command words are supported");
  }
  return word.parts.map((part) => {
    if (part.type === "Literal" || part.type === "Escaped") {
      return String(part.value ?? "");
    }
    if (part.type === "SingleQuoted") {
      return String(part.value ?? "");
    }
    if (part.type === "DoubleQuoted" && Array.isArray(part.parts)) {
      return literalWord({ type: "Word", parts: part.parts });
    }
    throw new Error("shell expansions are not allowed");
  }).join("");
}

function parseOnly(request) {
  const id = typeof request?.id === "string" ? request.id : null;
  const command = typeof request?.command === "string" ? request.command : "";
  if (!id || !command.trim()) {
    return {
      id,
      ok: false,
      error: "id and command are required for parse requests",
      exit_code: 2,
    };
  }
  try {
    const script = parse(command);
    const statements = script.statements.map((statement) => {
      if (
        statement.type !== "Statement"
        || statement.background
        || !Array.isArray(statement.pipelines)
        || !Array.isArray(statement.operators)
      ) {
        throw new Error("background or compound commands are not allowed");
      }
      if (!statement.operators.every((operator) => operator === "&&" || operator === "||")) {
        throw new Error("unsupported statement operator");
      }
      return {
        operators: statement.operators,
        pipelines: statement.pipelines.map((pipeline) => {
          if (
            pipeline.type !== "Pipeline"
            || pipeline.negated
            || pipeline.timed
            || pipeline.timePosix
            || pipeline.pipeStderr?.some(Boolean)
          ) {
            throw new Error("unsupported pipeline syntax");
          }
          return pipeline.commands.map((entry) => {
            if (
              entry.type !== "SimpleCommand"
              || entry.assignments.length
              || entry.redirections.length
            ) {
              throw new Error("only simple read-only commands are supported");
            }
            return {
              name: literalWord(entry.name),
              args: entry.args.map(literalWord),
            };
          });
        }),
      };
    });
    return {
      id,
      ok: true,
      statements,
      exit_code: 0,
    };
  } catch (error) {
    return {
      id,
      ok: false,
      error: error instanceof Error ? error.message : String(error),
      exit_code: 2,
    };
  }
}

async function execute(request) {
  if (!request || typeof request !== "object") {
    return {
      id: null,
      ok: false,
      stdout: "",
      stderr: "request must be a JSON object",
      exit_code: 2,
      cwd: "/",
    };
  }

  const id = typeof request.id === "string" ? request.id : null;
  const sessionId = typeof request.session_id === "string" ? request.session_id : null;
  const command = typeof request.command === "string" ? request.command : "";
  const cwd = typeof request.cwd === "string" && request.cwd.startsWith("/")
    ? request.cwd
    : "/";
  const files = request.files && typeof request.files === "object"
    ? request.files
    : {};
  const scope = request.scope && typeof request.scope === "object"
    ? request.scope
    : null;
  if (!id || !sessionId || !command.trim() || !scope || typeof scope.user_id !== "string") {
    return {
      id,
      ok: false,
      stdout: "",
      stderr: "id, session_id, command and immutable scope are required",
      exit_code: 2,
      cwd,
      scope,
    };
  }
  const serializedScope = JSON.stringify(scope);
  const previousScope = sessionScopes.get(sessionId);
  if (previousScope && previousScope !== serializedScope) {
    return {
      id,
      ok: false,
      stdout: "",
      stderr: "immutable scope cannot change during a Worker session",
      exit_code: 126,
      cwd,
      scope,
    };
  }
  sessionScopes.set(sessionId, serializedScope);

  const timeoutMs = Number.isFinite(request.timeout_ms)
    ? Math.max(1, Math.min(Number(request.timeout_ms), 120_000))
    : DEFAULT_TIMEOUT_MS;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const bash = new Bash({
      files,
      executionLimitProfile: "hardened",
      executionLimits: {
        maxFileSystemBytes: 256 * 1024 * 1024,
        maxOutputSize: 32 * 1024 * 1024,
        maxExecutionTimeMs: timeoutMs,
      },
    });
    const result = await bash.exec(command, {
      cwd,
      stdin: typeof request.stdin === "string" ? request.stdin : undefined,
      signal: controller.signal,
    });
    const nextCwd = typeof result.env?.PWD === "string" ? result.env.PWD : cwd;
    return {
      id,
      ok: result.exitCode === 0,
      stdout: result.stdout ?? "",
      stderr: result.stderr ?? "",
      exit_code: Number.isInteger(result.exitCode) ? result.exitCode : 1,
      cwd: nextCwd,
      scope,
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const timedOut = controller.signal.aborted;
    return {
      id,
      ok: false,
      stdout: "",
      stderr: timedOut ? `just-bash worker timeout after ${timeoutMs}ms` : message,
      exit_code: timedOut ? 124 : 1,
      cwd,
      scope,
    };
  } finally {
    clearTimeout(timeout);
  }
}

const input = createInterface({
  input: process.stdin,
  crlfDelay: Infinity,
});

let pending = Promise.resolve();

input.on("line", (line) => {
  pending = pending.then(async () => {
  if (Buffer.byteLength(line, "utf8") > MAX_REQUEST_BYTES) {
    writeResponse({
      id: null,
      ok: false,
      stdout: "",
      stderr: "request exceeds worker size limit",
      exit_code: 2,
      cwd: "/",
    });
    return;
  }
  let request;
  try {
    request = JSON.parse(line);
  } catch (error) {
    writeResponse({
      id: null,
      ok: false,
      stdout: "",
      stderr: `invalid JSON request: ${error instanceof Error ? error.message : String(error)}`,
      exit_code: 2,
      cwd: "/",
    });
    return;
  }
  writeResponse(
    request?.operation === "parse"
      ? parseOnly(request)
      : await execute(request),
  );
  });
});

input.on("close", () => {
  pending.finally(() => {
    process.exitCode = 0;
  });
});
