import { CheckCircle2Icon, DatabaseIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { QuerySqlResultArtifact } from "@/core/messages/data-query";

function formatCell(value: unknown) {
  if (value === null || value === undefined) return "";
  if (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean" ||
    typeof value === "bigint"
  )
    return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return "[unsupported value]";
  }
}

export function QueryResultCard({
  artifact,
}: {
  artifact: QuerySqlResultArtifact;
}) {
  const execution = artifact.execution;
  return (
    <section
      className="border-border bg-card/70 text-card-foreground w-full space-y-4 rounded-lg border p-4 shadow-xs"
      data-testid="query-result-card"
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm font-medium">
          <DatabaseIcon className="text-primary size-4" />
          查询结果
        </div>
        <Badge variant={execution && !execution.ok ? "destructive" : "outline"}>
          <CheckCircle2Icon className="mr-1 size-3" />
          {execution ? (execution.ok ? "执行成功" : "执行失败") : "SQL 已校验"}
        </Badge>
      </div>
      <pre className="bg-muted max-h-64 overflow-auto rounded-md p-3 text-xs whitespace-pre-wrap">
        {artifact.validation.executable_sql}
      </pre>
      {execution ? (
        !execution.ok ? (
          <div className="border-destructive/30 bg-destructive/5 space-y-2 rounded-md border p-3 text-sm">
            <p className="text-destructive font-medium">
              执行未完成：{execution.error_code}
            </p>
            {execution.error_message ? (
              <pre
                className="text-destructive overflow-auto text-xs whitespace-pre-wrap"
                data-testid="query-result-error-message"
              >
                {execution.error_message}
              </pre>
            ) : null}
          </div>
        ) : execution.empty ? (
          <p className="text-muted-foreground text-sm">查询成功，结果为空。</p>
        ) : (
          <div className="space-y-2">
            <div className="text-muted-foreground text-xs">
              共 {execution.row_count} 行
              {execution.truncated ? "，结果已截断" : ""}
              {execution.duration_ms !== undefined
                ? `，耗时 ${execution.duration_ms} ms`
                : ""}
            </div>
            <div className="max-h-96 overflow-auto rounded-md border">
              <table className="w-full text-left text-xs">
                <thead className="bg-muted sticky top-0">
                  <tr>
                    {execution.columns.map((column) => (
                      <th key={column} className="px-3 py-2 font-medium">
                        {column}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {execution.rows.map((row, rowIndex) => (
                    <tr key={rowIndex} className="border-t">
                      {execution.columns.map((column) => (
                        <td
                          key={column}
                          className="max-w-80 truncate px-3 py-2"
                        >
                          {formatCell(row[column])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )
      ) : (
        <p className="text-muted-foreground text-sm">
          按你的选择仅生成并校验 SQL，未连接数据库执行。
        </p>
      )}
    </section>
  );
}
