import { describe, expect, it } from "@rstest/core";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { QueryIntentCard } from "@/components/workspace/messages/query-intent-card";
import { I18nContext } from "@/core/i18n/context";
import type { QueryIntentArtifact } from "@/core/messages/data-query";
import type { HumanInputRequest } from "@/core/messages/human-input";

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
    reason: "存在未消解歧义，需要人工审批",
    next_tool: "ask_intent_approval",
  },
};

const request: HumanInputRequest = {
  version: 1,
  kind: "human_input_request",
  source: "ask_intent_approval",
  request_id: "data-query:req",
  flow_id: "ABCDEFGH",
  title: "确认数据查询意图",
  context: "需要确认这些查询条件",
  input_mode: "multi_question_choice",
  questions: [
    {
      id: "question_1",
      question: "时间范围是否是 2024 年全年？",
      options: [
        {
          id: "question_1_option_1",
          label: "是",
          value: "是",
        },
        {
          id: "question_1_option_2",
          label: "否，最近一年",
          value: "否，最近一年",
        },
      ],
    },
  ],
};

describe("QueryIntentCard", () => {
  it("renders the new publish artifact and multi-question approval card", () => {
    const html = renderCard();

    expect(html).toContain("查询意图");
    expect(html).toContain("排序查询");
    expect(html).toContain("地区: 华东");
    expect(html).toContain("(database)");
    expect(html).toContain("存在未消解歧义，需要人工审批");
    expect(html).toContain("时间范围是否是 2024 年全年？");
    expect(html).toContain("待审批");
  });

  it("derives confirmed state from persisted intent approval answers", () => {
    const html = renderCard({
      answeredResponse: {
        version: 1,
        kind: "human_input_response",
        source: "ask_intent_approval",
        request_id: "data-query:req",
        flow_id: "ABCDEFGH",
        response_kind: "text",
        value: JSON.stringify({
          kind: "intent_approval_answers",
          flow_id: "ABCDEFGH",
          answers: [
            {
              question_id: "question_1",
              option_id: "question_1_option_1",
              value: "是",
            },
          ],
          final_action: "execute",
        }),
      },
    });

    expect(html).toContain("已确认");
    expect(html).toContain("Answered: 是");
  });
});

function renderCard(
  props: Partial<Parameters<typeof QueryIntentCard>[0]> = {},
) {
  return renderToStaticMarkup(
    createElement(
      I18nContext.Provider,
      { value: { locale: "en-US", setLocale: () => undefined } },
      createElement(QueryIntentCard, {
        artifact,
        request,
        onSubmit: () => undefined,
        ...props,
      }),
    ),
  );
}
