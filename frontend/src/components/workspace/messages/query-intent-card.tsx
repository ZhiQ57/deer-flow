"use client";

import {
  CheckCircle2Icon,
  DatabaseIcon,
  ShieldQuestionIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  formatQueryIntentLabel,
  type QueryIntentArtifact,
} from "@/core/messages/data-query";

/**
 * 渲染数据查询意图标签卡片。
 *
 * Args:
 *   artifact: publish_query_labels 产出的结构化意图标签。
 *
 * Returns:
 *   查询意图标签卡片。
 */
export function QueryIntentCard({
  artifact,
}: {
  artifact: QueryIntentArtifact;
}) {
  const approvalRequired = artifact.approval.required;

  return (
    <section
      className="border-border bg-card/70 text-card-foreground w-full space-y-4 rounded-lg border p-4 shadow-xs"
      data-testid="query-intent-card"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <DatabaseIcon className="text-primary size-4 shrink-0" />
          <h2 className="truncate text-sm font-medium">查询意图</h2>
        </div>
        <Badge variant={approvalRequired ? "secondary" : "outline"}>
          {approvalRequired ? (
            <ShieldQuestionIcon className="mr-1 size-3" />
          ) : (
            <CheckCircle2Icon className="mr-1 size-3" />
          )}
          {approvalRequired ? "待审批" : "自动通过"}
        </Badge>
      </div>

      <div className="space-y-2 text-sm">
        <div className="font-medium">
          {formatQueryIntentLabel(artifact.intent)}
        </div>
        {artifact.summary ? (
          <p className="text-muted-foreground">{artifact.summary}</p>
        ) : null}
        <div className="flex flex-wrap gap-2">
          {artifact.labels.map((label) => (
            <Badge
              key={`${label.label}:${label.value}`}
              className="border-primary/30 bg-primary/5 px-2.5 py-1 text-sm"
              variant="outline"
            >
              {label.label}: {label.value}
              <span className="text-muted-foreground ml-1">
                ({label.source})
              </span>
            </Badge>
          ))}
        </div>
        {artifact.evidence.length > 0 ? (
          <p className="text-muted-foreground text-xs">
            已绑定 {artifact.evidence.length} 条查询依据
          </p>
        ) : null}
        <p className="text-muted-foreground text-xs">
          {artifact.approval.reason}
        </p>
      </div>
    </section>
  );
}
