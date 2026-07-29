import { describe, expect, it } from "@rstest/core";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { QueryIntentCard } from "@/components/workspace/messages/query-intent-card";
import { I18nContext } from "@/core/i18n/context";
import type { QueryIntentArtifact } from "@/core/messages/data-query";

const artifact: QueryIntentArtifact = {
  version: 1,
  kind: "data_query_labels",
  service_name: "data_query",
  snapshot_id: "sha256:snapshot",
  data_source_id: "sales-pg",
  turn_id: "turn-1",
  retrieval_digest: "retrieval:sha256:digest",
  binding_fingerprint: "sha256:target",
  intent: "ranking",
  summary: "查询华东销售额最高的商品",
  ambiguities: ["是否排除退款"],
  ambiguity_items: [
    {
      id: "ambiguity:sha256:refund",
      question: "是否排除退款",
      status: "pending",
      options: [
        { id: "accept", label: "按当前理解继续", value: "accept" },
        { id: "modify", label: "修改这一项", value: "modify" },
      ],
    },
  ],
  labels: [
    { label: "地区", value: "华东", source: "user", evidence_refs: [] },
    { label: "指标", value: "销售额", source: "database", evidence_refs: ["evidence:sha256:metric"] },
  ],
  evidence: [{ ref: "evidence:sha256:metric", kind: "evidence", summary: "销售额口径" }],
  approval: {
    version: 1,
    snapshot_id: "sha256:snapshot",
    status: "awaiting_confirmation",
    action: null,
    source: null,
  },
};

describe("QueryIntentCard", () => {
  it("renders friendly intent, labels, sources, ambiguity, evidence count, and pending status", () => {
    const html = renderToStaticMarkup(
      createElement(
        I18nContext.Provider,
        { value: { locale: "zh-CN", setLocale: () => undefined } },
        createElement(QueryIntentCard, {
          artifact,
          request: {
            version: 1,
            kind: "human_input_request",
            source: "ask_intent_approval",
            request_id: "data-query:req",
            question: "确认查询意图",
            input_mode: "choice_with_other",
            options: [
              { id: "execute", label: "确认并执行", value: "execute" },
              { id: "sql_only", label: "仅生成 SQL", value: "sql_only" },
              { id: "cancel", label: "取消查询", value: "cancel" },
            ],
          },
          onSubmit: () => undefined,
        }),
      ),
    );

    expect(html).toContain("查询意图");
    expect(html).toContain("排序查询");
    expect(html).not.toContain("ranking");
    expect(html).toContain("地区: 华东");
    expect(html).toContain("(database)");
    expect(html).toContain("是否排除退款");
    expect(html).toContain("已绑定 1 条 TableRAG 依据");
    expect(html).not.toContain("[evidence]");
    expect(html).toContain("待确认");
    expect(html).toContain("AI 需要你确认的理解");
    expect(html).toContain("按当前理解继续");
    expect(html).toContain("确认并生成 SQL");
  });

  it("derives confirmed, modified, and cancelled display states from persisted responses", () => {
    const renderStatus = (response: Parameters<typeof QueryIntentCard>[0]["answeredResponse"]) =>
      renderToStaticMarkup(
        createElement(
          I18nContext.Provider,
          { value: { locale: "zh-CN", setLocale: () => undefined } },
          createElement(QueryIntentCard, { artifact, answeredResponse: response }),
        ),
      );

    expect(renderStatus({ version: 1, kind: "human_input_response", source: "ask_intent_approval", request_id: "req", response_kind: "option", option_id: "execute", value: "execute" })).toContain("已确认");
    expect(renderStatus({ version: 1, kind: "human_input_response", source: "ask_intent_approval", request_id: "req", response_kind: "option", option_id: "sql_only", value: "sql_only" })).toContain("已确认");
    expect(renderStatus({ version: 1, kind: "human_input_response", source: "ask_intent_approval", request_id: "req", response_kind: "text", value: "改查去年" })).toContain("已修改");
    expect(renderStatus({ version: 1, kind: "human_input_response", source: "ask_intent_approval", request_id: "req", response_kind: "option", option_id: "cancel", value: "cancel" })).toContain("已取消");
  });
});
