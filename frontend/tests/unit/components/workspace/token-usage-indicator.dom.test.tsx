import { afterEach, describe, expect, it } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { TokenUsageIndicator } from "@/components/workspace/token-usage-indicator";
import { I18nContext } from "@/core/i18n/context";
import { enUS } from "@/core/i18n/locales/en-US";

afterEach(cleanup);

describe("TokenUsageIndicator prompt cache metrics", () => {
  it("renders cache metrics for historical backend usage", async () => {
    render(
      <I18nContext.Provider
        value={{ locale: "en-US", setLocale: () => undefined, t: enUS }}
      >
        <TokenUsageIndicator
          threadId="thread-history"
          messages={[]}
          backendUsage={{
            inputTokens: 100,
            outputTokens: 20,
            totalTokens: 120,
            cacheReadTokens: 80,
          }}
          enabled
          preferences={{
            headerTotal: true,
            inlineMode: "off",
          }}
          onPreferencesChange={() => undefined}
        />
      </I18nContext.Provider>,
    );

    fireEvent.keyDown(screen.getByRole("button", { name: /Tokens/ }), {
      key: "ArrowDown",
    });

    expect(await screen.findByText("Cache read")).not.toBeNull();
    expect(screen.getByText("Uncached input")).not.toBeNull();
    expect(screen.getByText("Cache hit rate")).not.toBeNull();
    expect(screen.getByText("80%")).not.toBeNull();
  });
});
