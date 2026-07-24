"use client";

import {
  CheckCircle2Icon,
  DatabaseIcon,
  Loader2Icon,
  PlayIcon,
  TriangleAlertIcon,
  XIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/core/i18n/hooks";
import type { SqlExecutionResult } from "@/core/sql-execution/types";

import { useSqlExecution } from "./context";

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean" ||
    typeof value === "bigint"
  ) {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return "[unsupported value]";
  }
}

export function SqlResultPanelView({
  sql,
  loading,
  result,
  error,
  onClose,
  onRerun,
}: {
  sql: string;
  loading: boolean;
  result: SqlExecutionResult | null;
  error: string | null;
  onClose: () => void;
  onRerun: () => void;
}) {
  const { t } = useI18n();
  const failed = Boolean(error) || Boolean(result && !result.ok);

  return (
    <section
      className="bg-background flex size-full min-h-0 flex-col overflow-hidden rounded-lg border"
      data-testid="sql-result-panel"
    >
      <header className="flex shrink-0 items-center justify-between gap-3 border-b px-4 py-3">
        <div className="flex min-w-0 items-center gap-2">
          <DatabaseIcon className="text-primary size-4 shrink-0" />
          <h2 className="truncate text-sm font-medium">
            {t.sqlExecution.title}
          </h2>
          {loading ? (
            <Badge variant="outline">
              <Loader2Icon className="mr-1 size-3 animate-spin" />
              {t.sqlExecution.executing}
            </Badge>
          ) : failed ? (
            <Badge variant="destructive">
              <TriangleAlertIcon className="mr-1 size-3" />
              {t.sqlExecution.failed}
            </Badge>
          ) : result?.ok ? (
            <Badge variant="outline">
              <CheckCircle2Icon className="mr-1 size-3" />
              {t.sqlExecution.success}
            </Badge>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            aria-label={t.sqlExecution.rerun}
            disabled={loading || !sql}
            onClick={onRerun}
            size="icon-sm"
            title={t.sqlExecution.rerun}
            variant="ghost"
          >
            {loading ? <Loader2Icon className="animate-spin" /> : <PlayIcon />}
          </Button>
          <Button
            aria-label={t.sqlExecution.close}
            onClick={onClose}
            size="icon-sm"
            title={t.sqlExecution.close}
            variant="ghost"
          >
            <XIcon />
          </Button>
        </div>
      </header>

      <div className="min-h-0 flex-1 space-y-4 overflow-auto p-4">
        {sql ? (
          <pre className="bg-muted max-h-48 overflow-auto rounded-md p-3 text-xs whitespace-pre-wrap">
            {sql}
          </pre>
        ) : null}

        {loading ? (
          <div className="text-muted-foreground flex items-center gap-2 text-sm">
            <Loader2Icon className="size-4 animate-spin" />
            {t.sqlExecution.loading}
          </div>
        ) : error ? (
          <div className="border-destructive/30 bg-destructive/5 text-destructive rounded-md border p-3 text-sm">
            {error}
          </div>
        ) : result && !result.ok ? (
          <div className="border-destructive/30 bg-destructive/5 space-y-1 rounded-md border p-3 text-sm">
            <p className="text-destructive font-medium">
              {result.error_code ?? t.sqlExecution.failed}
            </p>
            {result.error_message ? (
              <p className="text-muted-foreground break-words">
                {result.error_message}
              </p>
            ) : null}
          </div>
        ) : result?.ok ? (
          <div className="space-y-3">
            <div className="text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 text-xs">
              <span>
                {t.sqlExecution.database}: {result.database_type}
              </span>
              <span>
                {result.returned_row_count ?? result.rows.length}{" "}
                {t.sqlExecution.rows}
              </span>
              <span>
                {t.sqlExecution.duration}: {result.duration_ms} ms
              </span>
              {result.truncated ? (
                <span className="text-amber-600 dark:text-amber-400">
                  {t.sqlExecution.truncated}
                </span>
              ) : null}
            </div>

            {result.empty ? (
              <p className="text-muted-foreground text-sm">
                {t.sqlExecution.empty}
              </p>
            ) : (
              <div className="max-h-[60vh] overflow-auto rounded-md border">
                <table className="w-full text-left text-xs">
                  <thead className="bg-muted sticky top-0">
                    <tr>
                      {result.columns.map((column, columnIndex) => (
                        <th
                          className="px-3 py-2 font-medium whitespace-nowrap"
                          key={`${column}-${columnIndex}`}
                        >
                          {column}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {result.rows.map((row, rowIndex) => (
                      <tr className="border-t" key={rowIndex}>
                        {result.columns.map((column, columnIndex) => (
                          <td
                            className="max-w-80 px-3 py-2 break-words"
                            key={`${column}-${columnIndex}`}
                            title={formatCell(row[column])}
                          >
                            {formatCell(row[column])}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        ) : (
          <p className="text-muted-foreground text-sm">
            {t.sqlExecution.noResult}
          </p>
        )}
      </div>
    </section>
  );
}

export function SqlResultPanel() {
  const execution = useSqlExecution();
  return (
    <SqlResultPanelView
      error={execution.error}
      loading={execution.loading}
      onClose={execution.close}
      onRerun={() => void execution.rerun()}
      result={execution.result}
      sql={execution.sql}
    />
  );
}
