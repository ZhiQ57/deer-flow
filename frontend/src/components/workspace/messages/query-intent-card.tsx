"use client";

import { CheckCircle2Icon, DatabaseIcon, ShieldQuestionIcon } from "lucide-react";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  createDataQueryReviewResponse,
  parseDataQueryReviewDecisions,
  parseDataQueryReviewFinalAction,
  type QueryIntentArtifact,
  type QueryIntentReviewDecision,
} from "@/core/messages/data-query";
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
  const answeredAction = parseDataQueryReviewFinalAction(answeredResponse);
  const answeredDecisions = parseDataQueryReviewDecisions(answeredResponse);
  const reviewItems = artifact.ambiguity_items;
  const [decisions, setDecisions] = useState<Record<string, QueryIntentReviewDecision>>({});
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const statusLabel =
    answeredAction === "cancel" || status === "cancelled"
      ? "已取消"
      : answeredAction === "modify"
        ? "已修改"
        : answeredAction === "execute" || answeredAction === "sql_only" || status === "approved"
          ? "已确认"
          : "待确认";

  const submitReview = async (finalAction: "execute" | "sql_only" | "cancel") => {
    if (!parsedRequest || !onSubmit || disabled || pending || answeredResponse) return;
    if (finalAction !== "cancel") {
      const normalized: QueryIntentReviewDecision[] = [];
      for (const item of reviewItems) {
        const decision = decisions[item.id];
        if (!decision) {
          setError("请逐项确认所有不清晰点后再继续。");
          return;
        }
        if (decision.decision === "modify" && !drafts[item.id]?.trim()) {
          setError("请填写需要修改的查询条件。");
          return;
        }
        const modifiedValue = drafts[item.id]?.trim();
        normalized.push({
          ...decision,
          ...(decision.decision === "modify" && modifiedValue ? { value: modifiedValue } : {}),
        });
      }
      setError(null);
      await onSubmit(createDataQueryReviewResponse(parsedRequest, artifact, normalized, finalAction));
      return;
    }
    setError(null);
    await onSubmit(createDataQueryReviewResponse(parsedRequest, artifact, [], finalAction));
  };

  const setDecision = (id: string, decision: "accept" | "modify") => {
    if (disabled || pending || answeredResponse) return;
    setError(null);
    setDecisions((current) => ({ ...current, [id]: { id, decision } }));
  };

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
            <Badge key={`${label.label}:${label.value}`} variant="outline" className="border-primary/30 bg-primary/5 px-2.5 py-1 text-sm">
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
      {reviewItems.length > 0 ? (
        <div className="space-y-3 rounded-md border border-amber-200 bg-amber-50/60 p-3">
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-sm font-semibold">AI 需要你确认的理解</h3>
            <Badge variant="secondary">{reviewItems.length} 项</Badge>
          </div>
          <div className="space-y-2">
            {reviewItems.map((item) => {
              const decision = decisions[item.id]?.decision ?? answeredDecisions[item.id]?.decision;
              return (
                <div key={item.id} className="space-y-2 rounded-md border bg-background p-3" data-testid={`query-review-item-${item.id}`}>
                  <div className="flex items-start justify-between gap-3">
                    <p className="text-sm leading-6">{item.question}</p>
                    <Badge variant={decision ? "outline" : "secondary"}>{decision === "accept" ? "已接受" : decision === "modify" ? "待修改" : "待确认"}</Badge>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button type="button" size="sm" variant={decision === "accept" ? "default" : "outline"} disabled={disabled || pending || Boolean(answeredResponse) || !parsedRequest || !onSubmit} onClick={() => setDecision(item.id, "accept")}>
                      按当前理解继续
                    </Button>
                    <Button type="button" size="sm" variant={decision === "modify" ? "default" : "outline"} disabled={disabled || pending || Boolean(answeredResponse) || !parsedRequest || !onSubmit} onClick={() => setDecision(item.id, "modify")}>
                      修改这一项
                    </Button>
                  </div>
                  {decision === "modify" ? (
                    <Textarea
                      value={drafts[item.id] ?? ""}
                      disabled={disabled || pending || Boolean(answeredResponse) || !parsedRequest || !onSubmit}
                      placeholder="请输入你希望采用的条件或口径"
                      onChange={(event) => setDrafts((current) => ({ ...current, [item.id]: event.target.value }))}
                    />
                  ) : null}
                </div>
              );
            })}
          </div>
          {error ? <p className="text-destructive text-sm">{error}</p> : null}
          {parsedRequest && onSubmit ? (
            <div className="flex flex-wrap justify-end gap-2 border-t pt-3">
              <Button type="button" variant="outline" disabled={disabled || pending || Boolean(answeredResponse)} onClick={() => void submitReview("cancel")}>取消查询</Button>
              <Button type="button" variant="outline" disabled={disabled || pending || Boolean(answeredResponse)} onClick={() => void submitReview("sql_only")}>仅生成 SQL</Button>
              <Button type="button" disabled={disabled || pending || Boolean(answeredResponse)} onClick={() => void submitReview("execute")}>确认并生成 SQL</Button>
            </div>
          ) : null}
        </div>
      ) : parsedRequest ? (
        <HumanInputCard request={parsedRequest} answeredResponse={answeredResponse} pending={pending} disabled={disabled} onSubmit={onSubmit} />
      ) : null}
    </section>
  );
}
