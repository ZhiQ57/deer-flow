"use client";

import { CheckCircle2Icon, DatabaseIcon, ShieldQuestionIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import type { QueryIntentArtifact } from "@/core/messages/data-query";
import { parseHumanInputRequest, type HumanInputRequest, type HumanInputResponse } from "@/core/messages/human-input";

import { HumanInputCard, type HumanInputSubmitResult } from "./human-input-card";

export function QueryIntentCard({
  artifact,
  request,
  answeredResponse = null,
  pending = false,
  disabled = false,
  onSubmit,
}: {
  artifact: QueryIntentArtifact;
  request?: HumanInputRequest | null;
  answeredResponse?: HumanInputResponse | null;
  pending?: boolean;
  disabled?: boolean;
  onSubmit?: (response: HumanInputResponse) => HumanInputSubmitResult | Promise<HumanInputSubmitResult>;
}) {
  const parsedRequest = request ?? parseHumanInputRequest(artifact.human_input);
  const status = artifact.approval.status;
  const answeredAction = answeredResponse?.response_kind === "option" ? answeredResponse.option_id : answeredResponse ? "modify" : null;
  const statusLabel =
    answeredAction === "cancel" || status === "cancelled"
      ? "已取消"
      : answeredAction === "modify"
        ? "已修改"
        : answeredAction === "execute" || answeredAction === "sql_only" || status === "approved"
          ? "已确认"
          : "待确认";
  return (
    <section className="border-border bg-card/70 text-card-foreground w-full space-y-4 rounded-lg border p-4 shadow-xs" data-testid="query-intent-card">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <DatabaseIcon className="text-primary size-4 shrink-0" />
          <h2 className="truncate text-sm font-medium">查询意图</h2>
        </div>
        <Badge variant={statusLabel === "已确认" ? "outline" : "secondary"}>
          {statusLabel === "已确认" ? <CheckCircle2Icon className="mr-1 size-3" /> : <ShieldQuestionIcon className="mr-1 size-3" />}
          {statusLabel}
        </Badge>
      </div>
      <div className="space-y-2 text-sm">
        <div className="font-medium">{artifact.intent}</div>
        {artifact.summary ? <p className="text-muted-foreground">{artifact.summary}</p> : null}
        <div className="flex flex-wrap gap-2">
          {artifact.labels.map((label) => (
            <Badge key={`${label.label}:${label.value}`} variant="secondary">
              {label.label}: {label.value}
              <span className="text-muted-foreground ml-1">({label.source})</span>
            </Badge>
          ))}
        </div>
        {artifact.evidence.length > 0 ? (
          <div className="space-y-1">
            <div className="text-muted-foreground text-xs">TableRAG 依据</div>
            {artifact.evidence.slice(0, 8).map((item) => (
              <div key={item.ref} className="text-muted-foreground truncate text-xs" title={item.summary}>
                [{item.kind}] {item.summary}
              </div>
            ))}
          </div>
        ) : null}
        {artifact.ambiguities.length > 0 ? <p className="text-amber-600">待确认：{artifact.ambiguities.join("；")}</p> : null}
        {artifact.confidence !== null && artifact.confidence !== undefined ? <p className="text-muted-foreground">置信度：{Math.round(artifact.confidence * 100)}%</p> : null}
      </div>
      {parsedRequest ? (
        <HumanInputCard request={parsedRequest} answeredResponse={answeredResponse} pending={pending} disabled={disabled} onSubmit={onSubmit} />
      ) : null}
    </section>
  );
}
