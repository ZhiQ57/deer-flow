import { describe, expect, it } from "@rstest/core";
import { createElement, type KeyboardEvent } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  findMissingRequiredFields,
  HumanInputCard,
  shouldSubmitHumanInputTextOnKeyDown,
} from "@/components/workspace/messages/human-input-card";
import { I18nContext } from "@/core/i18n/context";
import { enUS } from "@/core/i18n/locales/en-US";
import {
  createMultiQuestionChoiceResponse,
  type HumanInputRequest,
  type HumanInputResponse,
} from "@/core/messages/human-input";

const request: HumanInputRequest = {
  version: 1,
  kind: "human_input_request",
  source: "ask_clarification",
  request_id: "clarification:call-abc",
  tool_call_id: "call-abc",
  clarification_type: "approach_choice",
  question: "Which environment should I deploy to?",
  context: "Need the target environment.",
  input_mode: "choice_with_other",
  options: [
    { id: "option-1", label: "development", value: "development" },
    { id: "option-2", label: "staging", value: "staging" },
  ],
};

describe("HumanInputCard", () => {
  it("renders request text, options, and the other-answer input", () => {
    const html = renderCard();

    expect(html).toContain("Need your help");
    expect(html).toContain("Need the target environment.");
    expect(html).toContain("Which environment should I deploy to?");
    expect(html).toContain("development");
    expect(html).toContain("staging");
    expect(html).toContain("Other answer");
    expect(html).toContain("Type another answer...");
  });

  it("renders answered state as disabled with the selected value", () => {
    const response: HumanInputResponse = {
      version: 1,
      kind: "human_input_response",
      source: "ask_clarification",
      request_id: "clarification:call-abc",
      response_kind: "option",
      option_id: "option-2",
      value: "staging",
    };
    const html = renderCard({ answeredResponse: response });

    expect(html).toContain("Answered");
    expect(html).toContain("Answered: staging");
    expect(html).toContain("disabled");
  });

  it("renders answered multi-question approval text without looping", () => {
    const requestWithQuestions: HumanInputRequest = {
      version: 1,
      kind: "human_input_request",
      source: "ask_intent_approval",
      request_id: "data-query:req",
      flow_id: "ABCDEFGH",
      title: "Confirm query intent",
      context: "Please confirm each query condition.",
      input_mode: "multi_question_choice",
      questions: [
        {
          id: "question_1",
          question: "Should the time range be full year 2024?",
          options: [
            { id: "question_1_option_1", label: "Yes", value: "Yes" },
            { id: "question_1_option_2", label: "No", value: "No" },
          ],
        },
        {
          id: "question_2",
          question: "Use order amount or payment amount?",
          options: [
            {
              id: "question_2_option_1",
              label: "Order amount",
              value: "Order amount",
            },
            {
              id: "question_2_option_2",
              label: "Payment amount",
              value: "Payment amount",
            },
          ],
        },
      ],
    };
    const response = createMultiQuestionChoiceResponse(requestWithQuestions, [
      {
        question_id: "question_1",
        option_id: "question_1_option_1",
        value: "Yes",
      },
      {
        question_id: "question_2",
        option_id: "question_2_option_2",
        value: "Payment amount",
      },
    ]);

    const html = renderCard({
      request: requestWithQuestions,
      answeredResponse: response,
    });

    expect(html).toContain("Answered:");
    expect(html).toContain("Yes");
    expect(html).toContain("Payment amount");
    expect(html).toContain("disabled");
  });

  it("renders read-only state when no submit handler is available", () => {
    const html = renderCard({ onSubmit: undefined });

    expect(html).toContain("Read only");
    expect(html).toContain("disabled");
  });

  it("renders markdown in question field (bold, lists)", () => {
    const html = renderCard({
      request: {
        ...request,
        question:
          "What kind of report do you want?\n\n1. **Topic/type**: sales or finance\n2. **Length**: short or detailed",
        input_mode: "free_text",
        options: undefined,
      },
    });

    expect(html).toContain("Topic/type");
    expect(html).toContain("Length");
    expect(html).not.toContain("**Topic/type**");
    expect(html).not.toContain("**Length**");
  });
  it("renders a form request with labeled fields and required markers", () => {
    const html = renderCard({
      request: {
        ...request,
        version: 2,
        request_id: "clarification:call-form",
        question: "Please provide the expense details.",
        input_mode: "form",
        options: undefined,
        fields: [
          { name: "amount", label: "Amount", type: "number", required: true },
          {
            name: "receipts",
            label: "Receipts",
            type: "multi_select",
            required: false,
            options: [
              { id: "receipts-option-1", label: "A-1", value: "A-1" },
              { id: "receipts-option-2", label: "A-2", value: "A-2" },
            ],
          },
          { name: "note", label: "Note", type: "textarea", required: false },
          {
            name: "design",
            label: "DesignPriority",
            type: "checkbox",
            required: false,
          },
        ],
      },
    });

    expect(html).toContain("Amount");
    expect(html).toContain("*");
    expect(html).toContain("Receipts");
    expect(html).toContain("A-1");
    expect(html).toContain("Note");
    expect(html).toContain("Submit");
    // Checkbox fields render as a single toggle row — the label must not be
    // duplicated by an additional field label above the control.
    expect(html.split("DesignPriority").length - 1).toBe(1);
  });

  it("associates form labels with controls and marks required fields", () => {
    const html = renderCard({
      request: {
        ...request,
        version: 2,
        request_id: "clarification:call-form",
        question: "Please provide the expense details.",
        input_mode: "form",
        options: undefined,
        fields: [
          { name: "amount", label: "Amount", type: "number", required: true },
          { name: "note", label: "Note", type: "textarea", required: false },
        ],
      },
    });

    // Every visible label is linked to its control via htmlFor/id.
    const htmlForIds = [...html.matchAll(/<label[^>]*for="([^"]+)"/g)].map(
      (match) => match[1],
    );
    expect(htmlForIds.length).toBe(2);
    for (const id of htmlForIds) {
      expect(html).toContain(`id="${id}"`);
    }
    expect(html).toContain('aria-required="true"');
  });

  it("findMissingRequiredFields flags empty required values only", () => {
    const fields = [
      {
        name: "amount",
        label: "Amount",
        type: "number" as const,
        required: true,
      },
      {
        name: "note",
        label: "Note",
        type: "text" as const,
        required: false,
      },
      {
        name: "receipts",
        label: "Receipts",
        type: "multi_select" as const,
        required: true,
        options: [{ id: "o1", label: "A-1", value: "A-1" }],
      },
    ];

    expect(
      findMissingRequiredFields(fields, {}).map((field) => field.name),
    ).toEqual(["amount", "receipts"]);
    expect(
      findMissingRequiredFields(fields, {
        amount: "  ",
        receipts: [],
      }).map((field) => field.name),
    ).toEqual(["amount", "receipts"]);
    expect(
      findMissingRequiredFields(fields, {
        amount: "300",
        receipts: ["A-1"],
      }),
    ).toEqual([]);
  });

  it("renders multiple questions and grouped options", () => {
    const html = renderCard({
      request: {
        version: 1,
        kind: "human_input_request",
        source: "ask_intent_approval",
        request_id: "data-query:req",
        flow_id: "ABCDEFGH",
        title: "Confirm query intent",
        context: "Please confirm these query conditions.",
        input_mode: "multi_question_choice",
        questions: [
          {
            id: "question_1",
            question: "Should the time range be full year 2024?",
            options: [
              {
                id: "question_1_option_1",
                label: "Yes",
                value: "Yes",
              },
              {
                id: "question_1_option_2",
                label: "No, use the latest year",
                value: "No, use the latest year",
              },
            ],
          },
          {
            id: "question_2",
            question: "Use order amount or payment amount?",
            options: [
              {
                id: "question_2_option_1",
                label: "Order amount",
                value: "Order amount",
              },
              {
                id: "question_2_option_2",
                label: "Payment amount",
                value: "Payment amount",
              },
            ],
          },
        ],
      },
    });

    expect(html).toContain("Should the time range be full year 2024?");
    expect(html).toContain("Use order amount or payment amount?");
    expect(html).toContain("No, use the latest year");
    expect(html).toContain("Payment amount");
  });
  it("renders an other-answer input for every multi-question item", () => {
    const html = renderCard({
      request: {
        version: 1,
        kind: "human_input_request",
        source: "ask_intent_approval",
        request_id: "data-query:req-english",
        flow_id: "ABCDEFGH",
        title: "Confirm query intent",
        context: "Please confirm each query condition.",
        input_mode: "multi_question_choice",
        questions: [
          {
            id: "question_1",
            question: "Should the time range be full year 2024?",
            options: [
              { id: "question_1_option_1", label: "Yes", value: "Yes" },
            ],
          },
          {
            id: "question_2",
            question: "Use order amount or payment amount?",
            options: [
              {
                id: "question_2_option_1",
                label: "Order amount",
                value: "Order amount",
              },
            ],
          },
        ],
      },
    });

    expect(html.match(/Type another answer\.\.\./g)?.length).toBe(2);
    expect(html).toContain("Should the time range be full year 2024?");
    expect(html).toContain("Use order amount or payment amount?");
  });

  it("does not submit text with Enter while IME composition is active", () => {
    expect(shouldSubmitHumanInputTextOnKeyDown(keyEvent())).toBe(true);
    expect(
      shouldSubmitHumanInputTextOnKeyDown(keyEvent({ shiftKey: true })),
    ).toBe(false);
    expect(
      shouldSubmitHumanInputTextOnKeyDown(keyEvent({ isComposing: true })),
    ).toBe(false);
    expect(
      shouldSubmitHumanInputTextOnKeyDown(keyEvent({ keyCode: 229 })),
    ).toBe(false);
    expect(shouldSubmitHumanInputTextOnKeyDown(keyEvent(), true)).toBe(false);
  });
});

function renderCard(props: Partial<Parameters<typeof HumanInputCard>[0]> = {}) {
  return renderToStaticMarkup(
    createElement(
      I18nContext.Provider,
      {
        value: {
          locale: "en-US",
          setLocale: () => undefined,
          t: enUS,
        },
      },
      createElement(HumanInputCard, {
        request,
        onSubmit: () => undefined,
        ...props,
      }),
    ),
  );
}

function keyEvent({
  isComposing = false,
  key = "Enter",
  keyCode = 13,
  shiftKey = false,
}: {
  isComposing?: boolean;
  key?: string;
  keyCode?: number;
  shiftKey?: boolean;
} = {}) {
  return {
    key,
    keyCode,
    nativeEvent: { isComposing },
    shiftKey,
  } as unknown as KeyboardEvent<HTMLTextAreaElement>;
}
