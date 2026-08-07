import { describe, expect, it } from "@rstest/core";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { QueryIntentCard } from "@/components/workspace/messages/query-intent-card";
import { I18nContext } from "@/core/i18n/context";
import type { QueryIntentArtifact } from "@/core/messages/data-query";

const artifact: QueryIntentArtifact = {
  intent: "ranking",
  summary: "查询华东销售额最高的商品",
  labels: [
    { label: "地区", value: "华东", source: "user" },
    {
      label: "指标",
      value: "销售额",
      source: "database",
      evidence: "销售额口径",
    },
  ],
  evidence: [],
  approval: {
    flow_id: "ABCDEFGH",
    required: true,
    reason: "存在未消歧义，需要人工审批",
    next_tool: "ask_intent_approval",
  },
};

describe("QueryIntentCard", () => {
  it("renders the new publish artifact card", () => {
    const html = renderCard();

    expect(html).toContain("查询意图");
    expect(html).toContain("排序查询");
    expect(html).toContain("地区: 华东");
    expect(html).toContain("(database)");
    expect(html).toContain("存在未消歧义，需要人工审批");
    expect(html).toContain("待审批");
  });
});

function renderCard() {
  return renderToStaticMarkup(
    createElement(
      I18nContext.Provider,
      { value: { locale: "en-US", setLocale: () => undefined } },
      createElement(QueryIntentCard, {
        artifact,
      }),
    ),
  );
}
