"use client";

import {
  CheckIcon,
  CheckCircle2Icon,
  Loader2Icon,
  MessageCircleQuestionMarkIcon,
} from "lucide-react";
import { useEffect, useId, useMemo, useState, type KeyboardEvent } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useI18n } from "@/core/i18n/hooks";
import {
  createHumanInputOptionResponse,
  createHumanInputTextResponse,
  createMultiQuestionChoiceResponse,
  parseIntentApprovalAnswers,
  type HumanInputAnswer,
  type HumanInputOption,
  type HumanInputRequest,
  type HumanInputResponse,
} from "@/core/messages/human-input";
import { isIMEComposing } from "@/lib/ime";
import { cn } from "@/lib/utils";

import { MarkdownContent } from "./markdown-content";

export type HumanInputSubmitResult = boolean | void;

export function shouldSubmitHumanInputTextOnKeyDown(
  event: KeyboardEvent<HTMLTextAreaElement>,
  isComposing = false,
) {
  return (
    event.key === "Enter" &&
    !event.shiftKey &&
    !isIMEComposing(event, isComposing)
  );
}

function areStringRecordsEqual(
  left: Record<string, string>,
  right: Record<string, string>,
) {
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  if (leftKeys.length !== rightKeys.length) {
    return false;
  }

  for (const key of leftKeys) {
    if (left[key] !== right[key]) {
      return false;
    }
  }

  return true;
}

/**
 * 渲染通用人机输入审批卡片。
 *
 * Args:
 *   request: 后端下发的人机输入请求。
 *   disabled: 是否禁用交互。
 *   pending: 当前是否处于提交中。
 *   answeredResponse: 已落盘的隐藏响应。
 *   onSubmit: 提交回调。
 *
 * Returns:
 *   人机输入卡片。
 */
export function HumanInputCard({
  request,
  disabled = false,
  pending = false,
  answeredResponse = null,
  onSubmit,
}: {
  request: HumanInputRequest;
  disabled?: boolean;
  pending?: boolean;
  answeredResponse?: HumanInputResponse | null;
  onSubmit?: (
    response: HumanInputResponse,
  ) => HumanInputSubmitResult | Promise<HumanInputSubmitResult>;
}) {
  const { t } = useI18n();
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [isComposing, setIsComposing] = useState(false);
  const [selectedOptionIds, setSelectedOptionIds] = useState<
    Record<string, string>
  >({});
  const [questionTexts, setQuestionTexts] = useState<Record<string, string>>(
    {},
  );
  const titleId = useId();
  const textInputId = useId();
  const allowText =
    request.input_mode === "free_text" ||
    request.input_mode === "choice_with_other";
  const options = request.options ?? [];
  const questions = useMemo(() => request.questions ?? [], [request.questions]);
  const answeredIntentApproval = useMemo(
    () => parseIntentApprovalAnswers(answeredResponse),
    [answeredResponse],
  );
  const answeredOptionIds = useMemo(
    () =>
      Object.fromEntries(
        (answeredIntentApproval?.answers ?? []).map((answer) => [
          answer.question_id,
          answer.option_id,
        ]),
      ),
    [answeredIntentApproval],
  );
  const readOnly = !onSubmit;
  const isDisabled =
    disabled || pending || Boolean(answeredResponse) || readOnly;
  const statusLabel = answeredResponse
    ? t.humanInput.answered
    : pending
      ? t.humanInput.pending
      : readOnly
        ? t.humanInput.readOnly
        : null;

  useEffect(() => {
    if (!answeredIntentApproval) {
      return;
    }

    const nextSelectedOptionIds: Record<string, string> = {};
    const nextQuestionTexts: Record<string, string> = {};

    for (const answer of answeredIntentApproval.answers) {
      const question = questions.find((item) => item.id === answer.question_id);
      if (!question) {
        continue;
      }

      const matchedOption = question.options.find(
        (option) => option.id === answer.option_id,
      );
      if (matchedOption) {
        nextSelectedOptionIds[answer.question_id] = matchedOption.id;
      } else {
        nextQuestionTexts[answer.question_id] = answer.value;
      }
    }

    setSelectedOptionIds((current) =>
      areStringRecordsEqual(current, nextSelectedOptionIds)
        ? current
        : nextSelectedOptionIds,
    );
    setQuestionTexts((current) =>
      areStringRecordsEqual(current, nextQuestionTexts)
        ? current
        : nextQuestionTexts,
    );
  }, [answeredIntentApproval, questions]);

  const submitResponse = async (response: HumanInputResponse) => {
    if (isDisabled || !onSubmit) {
      return;
    }
    setError("");
    const result = await onSubmit(response);
    if (result !== false && response.response_kind === "text") {
      setText("");
    }
  };

  const handleOptionClick = (option: HumanInputOption) => {
    void submitResponse(createHumanInputOptionResponse(request, option));
  };

  const handleTextSubmit = (event: { preventDefault(): void }) => {
    event.preventDefault();
    const value = text.trim();
    if (!value) {
      setError(t.humanInput.emptyError);
      return;
    }
    void submitResponse(createHumanInputTextResponse(request, value));
  };

  const handleTextKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (shouldSubmitHumanInputTextOnKeyDown(event, isComposing)) {
      event.preventDefault();
      const value = text.trim();
      if (!value) {
        setError(t.humanInput.emptyError);
        return;
      }
      void submitResponse(createHumanInputTextResponse(request, value));
    }
  };

  const getOtherAnswerOptionId = (questionId: string) => `${questionId}:other`;

  /**
   * 提交多问题审批答案。
   *
   * Args:
   *   event: 表单提交事件。
   *
   * Returns:
   *   无返回值。
   */
  const handleMultiQuestionSubmit = (event: { preventDefault(): void }) => {
    event.preventDefault();
    const answers: HumanInputAnswer[] = [];

    for (const question of questions) {
      const freeTextValue = questionTexts[question.id]?.trim();
      if (freeTextValue) {
        answers.push({
          question_id: question.id,
          option_id: getOtherAnswerOptionId(question.id),
          value: freeTextValue,
        });
        continue;
      }

      const optionId = selectedOptionIds[question.id];
      const option = question.options.find((item) => item.id === optionId);
      if (!option) {
        setError(t.humanInput.emptyError);
        return;
      }
      answers.push({
        question_id: question.id,
        option_id: option.id,
        value: option.value,
      });
    }

    setError("");
    void submitResponse(createMultiQuestionChoiceResponse(request, answers));
  };

  return (
    <section
      aria-labelledby={titleId}
      className="border-border bg-card/70 text-card-foreground rounded-lg border p-4 shadow-xs"
      data-testid="human-input-card"
    >
      <div className="flex items-start gap-3">
        <div className="bg-primary/10 text-primary mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-md">
          <MessageCircleQuestionMarkIcon className="size-4" />
        </div>
        <div className="min-w-0 flex-1 space-y-3">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div className="min-w-0 space-y-1">
              <h2 id={titleId} className="text-sm leading-5 font-medium">
                {request.title ?? t.toolCalls.needYourHelp}
              </h2>
              {request.context ? (
                <div className="text-muted-foreground text-sm leading-6">
                  <MarkdownContent
                    content={request.context}
                    isLoading={false}
                  />
                </div>
              ) : null}
            </div>
            {statusLabel ? (
              <Badge
                className={cn(
                  "h-6 rounded-md px-2",
                  pending && "gap-1.5",
                  answeredResponse &&
                    "border-primary/20 bg-primary/10 text-primary",
                )}
                variant={answeredResponse ? "outline" : "secondary"}
              >
                {pending ? (
                  <Loader2Icon className="size-3 animate-spin" />
                ) : null}
                {answeredResponse ? (
                  <CheckCircle2Icon className="size-3" />
                ) : null}
                {statusLabel}
              </Badge>
            ) : null}
          </div>

          {request.question ? (
            <div className="text-foreground text-sm leading-6">
              <MarkdownContent content={request.question} isLoading={false} />
            </div>
          ) : null}

          {request.input_mode === "multi_question_choice" ? (
            <form className="space-y-4" onSubmit={handleMultiQuestionSubmit}>
              {questions.map((question, questionIndex) => {
                const selectedOptionId =
                  answeredOptionIds[question.id] ??
                  selectedOptionIds[question.id];
                const answerDraft = questionTexts[question.id] ?? "";
                const questionTextInputId = `${textInputId}-${question.id}`;

                return (
                  <fieldset
                    key={question.id}
                    className="border-border/70 space-y-3 rounded-md border p-3"
                    disabled={isDisabled}
                  >
                    <legend className="flex items-center gap-2 px-1 text-sm font-medium">
                      <span className="bg-primary/10 text-primary flex size-5 shrink-0 items-center justify-center rounded-full text-xs font-semibold">
                        {questionIndex + 1}
                      </span>
                      <span className="min-w-0 break-words">
                        {question.question}
                      </span>
                    </legend>
                    <div className="grid gap-2">
                      {question.options.map((option) => {
                        const selected = selectedOptionId === option.id;
                        return (
                          <Button
                            key={option.id}
                            aria-pressed={selected}
                            className="min-h-11 w-full justify-start rounded-md px-3 py-2 text-left leading-5 whitespace-normal"
                            disabled={isDisabled}
                            type="button"
                            variant={selected ? "secondary" : "outline"}
                            onClick={() => {
                              setError("");
                              setSelectedOptionIds((current) => ({
                                ...current,
                                [question.id]: option.id,
                              }));
                              setQuestionTexts((current) => ({
                                ...current,
                                [question.id]: "",
                              }));
                            }}
                          >
                            <span className="flex min-w-0 items-center gap-2">
                              <span className="border-input flex size-4 shrink-0 items-center justify-center rounded-full border">
                                {selected ? (
                                  <CheckIcon className="size-3" />
                                ) : null}
                              </span>
                              <span className="min-w-0 wrap-break-word whitespace-pre-wrap">
                                {option.label}
                              </span>
                            </span>
                          </Button>
                        );
                      })}
                    </div>
                    <div className="space-y-2">
                      <div className="text-muted-foreground text-xs">
                        {t.humanInput.otherLabel}
                      </div>
                      <Input
                        aria-invalid={Boolean(error)}
                        aria-label={`${question.question} - ${t.humanInput.otherLabel}`}
                        className="h-10 text-sm"
                        disabled={isDisabled}
                        id={questionTextInputId}
                        placeholder={t.humanInput.otherPlaceholder}
                        value={answerDraft}
                        onChange={(event) => {
                          const value = event.target.value;
                          setError("");
                          setQuestionTexts((current) => ({
                            ...current,
                            [question.id]: value,
                          }));
                          if (value.trim()) {
                            setSelectedOptionIds((current) => ({
                              ...current,
                              [question.id]: "",
                            }));
                          }
                        }}
                      />
                    </div>
                  </fieldset>
                );
              })}
              <div className="flex min-h-9 flex-wrap items-center justify-between gap-2">
                {error ? (
                  <p className="text-destructive text-sm">{error}</p>
                ) : answeredIntentApproval ? (
                  <p className="text-muted-foreground text-sm">
                    {t.humanInput.answeredValue(
                      answeredIntentApproval.answers
                        .map((answer) => answer.value)
                        .join("，"),
                    )}
                  </p>
                ) : (
                  <span />
                )}
                <Button
                  className="min-w-24"
                  disabled={isDisabled}
                  type="submit"
                >
                  {pending ? (
                    <Loader2Icon className="size-4 animate-spin" />
                  ) : null}
                  {t.humanInput.submit}
                </Button>
              </div>
            </form>
          ) : null}

          {request.input_mode !== "multi_question_choice" &&
          options.length > 0 ? (
            <div className="grid gap-2">
              {options.map((option) => (
                <Button
                  key={option.id}
                  className="min-h-11 w-full justify-start rounded-md px-3 py-2 text-left leading-5 whitespace-normal"
                  disabled={isDisabled}
                  type="button"
                  variant="outline"
                  onClick={() => handleOptionClick(option)}
                >
                  <span className="min-w-0 wrap-break-word whitespace-pre-wrap">
                    {option.label}
                  </span>
                </Button>
              ))}
            </div>
          ) : null}

          {request.input_mode !== "multi_question_choice" && allowText ? (
            <form className="space-y-2" onSubmit={handleTextSubmit}>
              <label className="sr-only" htmlFor={textInputId}>
                {t.humanInput.otherLabel}
              </label>
              <Textarea
                id={textInputId}
                aria-invalid={Boolean(error)}
                aria-describedby={error ? `${textInputId}-error` : undefined}
                className="min-h-20 resize-y text-sm"
                disabled={isDisabled}
                placeholder={t.humanInput.otherPlaceholder}
                value={text}
                onChange={(event) => {
                  setText(event.target.value);
                  if (error) {
                    setError("");
                  }
                }}
                onCompositionEnd={() => setIsComposing(false)}
                onCompositionStart={() => setIsComposing(true)}
                onKeyDown={handleTextKeyDown}
              />
              <div className="flex min-h-9 flex-wrap items-center justify-between gap-2">
                {error ? (
                  <p
                    className="text-destructive text-sm"
                    id={`${textInputId}-error`}
                  >
                    {error}
                  </p>
                ) : answeredResponse ? (
                  <p
                    className="text-muted-foreground text-sm"
                    aria-live="polite"
                  >
                    {t.humanInput.answeredValue(answeredResponse.value)}
                  </p>
                ) : (
                  <span />
                )}
                <Button
                  className="min-w-24"
                  disabled={isDisabled}
                  type="submit"
                  variant="secondary"
                >
                  {pending ? (
                    <Loader2Icon className="size-4 animate-spin" />
                  ) : null}
                  {t.humanInput.submit}
                </Button>
              </div>
            </form>
          ) : request.input_mode !== "multi_question_choice" &&
            answeredResponse ? (
            <p className="text-muted-foreground text-sm" aria-live="polite">
              {t.humanInput.answeredValue(answeredResponse.value)}
            </p>
          ) : null}
        </div>
      </div>
    </section>
  );
}
