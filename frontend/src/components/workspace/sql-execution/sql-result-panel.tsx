"use client";

import {
  CheckCircle2Icon,
  DatabaseIcon,
  Loader2Icon,
  MessageCircleIcon,
  PlayIcon,
  TriangleAlertIcon,
  XIcon,
} from "lucide-react";
import { type MouseEvent, useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useMaybeSidecar } from "@/components/workspace/sidecar";
import { useI18n } from "@/core/i18n/hooks";
import type { SidecarContext } from "@/core/sidecar";
import type { SqlExecutionResult } from "@/core/sql-execution/types";
import { cn } from "@/lib/utils";

import { useSqlExecution } from "./context";

const SELECTION_TOOLBAR_MARGIN = 8;
const SELECTION_TOOLBAR_ESTIMATED_HEIGHT = 42;

type SelectionToolbarState = {
  context: SidecarContext;
  x: number;
  y: number;
  placement: "top" | "bottom";
};

/**
 * 构造 SQL 执行结果的对话引用。
 *
 * @param content 用户选中的 SQL、错误或结果文本。
 * @param label 引用在输入框中显示的来源名称。
 * @returns 非空选择对应的引用上下文；空文本返回 null。
 */
export function buildSqlResultSidecarContext(
  content: string,
  label: string,
): SidecarContext | null {
  const normalized = content.trim();
  if (!normalized) return null;
  return {
    type: "referenced_message",
    label,
    role: "assistant",
    content: normalized,
  };
}

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
  const sidecar = useMaybeSidecar();
  const [selectionToolbar, setSelectionToolbar] =
    useState<SelectionToolbarState | null>(null);
  const failed = Boolean(error) || Boolean(result && !result.ok);

  const clearSelectionToolbar = useCallback(() => {
    setSelectionToolbar(null);
  }, []);

  useEffect(() => {
    setSelectionToolbar(null);
  }, [error, loading, result, sql]);

  useEffect(() => {
    if (!selectionToolbar) return;

    const hideOnScroll = () => setSelectionToolbar(null);
    const hideOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSelectionToolbar(null);
    };
    window.addEventListener("scroll", hideOnScroll, true);
    document.addEventListener("keydown", hideOnEscape);
    return () => {
      window.removeEventListener("scroll", hideOnScroll, true);
      document.removeEventListener("keydown", hideOnEscape);
    };
  }, [selectionToolbar]);

  const handleTextSelection = useCallback(
    (event: MouseEvent<HTMLDivElement>) => {
      if (!sidecar) return;
      const selection = window.getSelection();
      const selectedText = selection?.toString().trim();
      if (
        !selection ||
        selection.isCollapsed ||
        !selectedText ||
        selection.rangeCount === 0 ||
        !selection.anchorNode ||
        !selection.focusNode
      ) {
        setSelectionToolbar(null);
        return;
      }
      if (
        !event.currentTarget.contains(selection.anchorNode) ||
        !event.currentTarget.contains(selection.focusNode)
      ) {
        setSelectionToolbar(null);
        return;
      }
      const context = buildSqlResultSidecarContext(
        selectedText,
        t.sqlExecution.title,
      );
      if (!context) return;

      const rect = selection.getRangeAt(0).getBoundingClientRect();
      const fitsAbove =
        rect.top -
          SELECTION_TOOLBAR_MARGIN -
          SELECTION_TOOLBAR_ESTIMATED_HEIGHT >=
        0;
      setSelectionToolbar({
        context,
        x: rect.left + rect.width / 2,
        y: fitsAbove
          ? rect.top - SELECTION_TOOLBAR_MARGIN
          : rect.bottom + SELECTION_TOOLBAR_MARGIN,
        placement: fitsAbove ? "top" : "bottom",
      });
    },
    [sidecar, t.sqlExecution.title],
  );

  const handleAddSelectionToConversation = useCallback(() => {
    if (!selectionToolbar || !sidecar) return;
    sidecar.addContextToConversation(selectionToolbar.context);
    window.getSelection()?.removeAllRanges();
    setSelectionToolbar(null);
  }, [selectionToolbar, sidecar]);

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

      <div
        className="min-h-0 flex-1 space-y-4 overflow-auto p-4"
        onMouseUp={handleTextSelection}
      >
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
              <pre
                className="text-destructive overflow-auto text-xs whitespace-pre-wrap"
                data-testid="sql-result-error-message"
              >
                {result.error_message}
              </pre>
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
      {selectionToolbar && sidecar ? (
        <div
          className={cn(
            "bg-popover text-popover-foreground border-border fixed z-50 flex -translate-x-1/2 items-center gap-1 rounded-full border p-1 shadow-lg",
            selectionToolbar.placement === "bottom"
              ? "translate-y-0"
              : "-translate-y-full",
          )}
          data-sql-result-selection-toolbar
          style={{ left: selectionToolbar.x, top: selectionToolbar.y }}
        >
          <Button
            className="h-8 rounded-full px-2.5 text-xs"
            onClick={handleAddSelectionToConversation}
            onMouseDown={(event) => event.preventDefault()}
            size="sm"
            type="button"
            variant="ghost"
          >
            <MessageCircleIcon className="size-3.5" />
            {t.sidecar.addToConversation}
          </Button>
          <Button
            aria-label={t.common.close}
            className="size-8 rounded-full"
            onClick={clearSelectionToolbar}
            onMouseDown={(event) => event.preventDefault()}
            size="icon-sm"
            type="button"
            variant="ghost"
          >
            <span aria-hidden="true">×</span>
          </Button>
        </div>
      ) : null}
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
