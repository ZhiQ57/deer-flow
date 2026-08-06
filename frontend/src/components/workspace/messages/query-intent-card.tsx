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
import {
  parseIntentApprovalAnswers,
  type HumanInputRequest,
  type HumanInputResponse,
} from "@/core/messages/human-input";

import {
  HumanInputCard,
  type HumanInputSubmitResult,
} from "./human-input-card";

/**
 * 渲染 DataAgent 查询意图与可选人工审批卡片。
 *
 * Args:
 *   artifact: publish_query_labels 返回的新标签 artifact。
 *   request: ask_intent_approval 返回的人机输入请求。
 *   answeredResponse: 已持久化的人机输入响应。
 *   pending: 当前审批是否正在提交。
 *   disabled: 是否禁止继续操作。
 *   onSubmit: 审批响应提交函数。
 *
 * Returns:
 *   查询意图卡片。
 */
export function QueryIntentCard({
  artifact,
  request = null,
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
  onSubmit?: (
    response: HumanInputResponse,
  ) => HumanInputSubmitResult | Promise<HumanInputSubmitResult>;
}) {
  const answeredApproval = parseIntentApprovalAnswers(answeredResponse);
  const cancelled = answeredApproval?.final_action === "cancel";
  const approved =
    Boolean(answeredApproval) && answeredApproval?.final_action !== "cancel";
  const statusLabel = cancelled
    ? "已取消"
    : approved
      ? "已确认"
      : request
        ? "待审批"
        : artifact.approval.required
          ? "等待审批"
          : "自动通过";

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
        <Badge variant={approved ? "outline" : "secondary"}>
          {approved ? (
            <CheckCircle2Icon className="mr-1 size-3" />
          ) : (
            <ShieldQuestionIcon className="mr-1 size-3" />
          )}
          {statusLabel}
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

      {request ? (
        <HumanInputCard
          key={request.request_id}
          answeredResponse={answeredResponse}
          disabled={disabled}
          pending={pending}
          request={request}
          onSubmit={onSubmit}
        />
      ) : null}
    </section>
  );
}
