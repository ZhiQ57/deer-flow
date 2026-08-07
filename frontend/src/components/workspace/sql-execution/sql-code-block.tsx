"use client";

import { Loader2Icon, PlayIcon } from "lucide-react";
import { toast } from "sonner";
import {
  CodeBlock,
  CodeBlockCopyButton,
  CodeBlockDownloadButton,
  type CustomRendererProps,
} from "streamdown";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { useMaybeSqlExecution } from "./context";

export function SqlCodeBlock({
  code,
  isIncomplete,
  language,
}: CustomRendererProps) {
  const { t } = useI18n();
  const execution = useMaybeSqlExecution();
  const executingCurrentSql = execution?.loading && execution.sql === code;
  const executionUnavailable = !execution?.enabled;

  return (
    <CodeBlock code={code} isIncomplete={isIncomplete} language={language}>
      <button
        className={cn(
          "text-muted-foreground hover:text-foreground flex cursor-pointer items-center gap-1 rounded px-1.5 py-1 text-xs transition-colors",
          "disabled:cursor-not-allowed disabled:opacity-50",
        )}
        data-testid="sql-execute-button"
        disabled={isIncomplete || Boolean(execution?.loading)}
        onClick={() => {
          if (executionUnavailable) {
            toast.error(t.sqlExecution.unavailable);
            return;
          }
          void execution.execute(code);
        }}
        title={
          executingCurrentSql
            ? t.sqlExecution.executing
            : executionUnavailable
              ? t.sqlExecution.unavailable
              : t.sqlExecution.execute
        }
        type="button"
      >
        {executingCurrentSql ? (
          <Loader2Icon className="size-3.5 animate-spin" />
        ) : (
          <PlayIcon className="size-3.5" />
        )}
        <span>
          {executingCurrentSql
            ? t.sqlExecution.executing
            : t.sqlExecution.execute}
        </span>
      </button>
      <CodeBlockDownloadButton code={code} language={language} />
      <CodeBlockCopyButton code={code} />
    </CodeBlock>
  );
}
