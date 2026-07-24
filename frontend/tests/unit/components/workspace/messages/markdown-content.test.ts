import { describe, expect, it } from "@rstest/core";
import {
  createElement,
  type ComponentType,
  type ImgHTMLAttributes,
  type ReactNode,
} from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { MarkdownContent } from "@/components/workspace/messages/markdown-content";
import { SqlExecutionProvider } from "@/components/workspace/sql-execution";
import { I18nProvider } from "@/core/i18n/context";

const TestI18nProvider = I18nProvider as ComponentType<{
  initialLocale: "en-US" | "zh-CN";
  children?: ReactNode;
}>;
const TestSqlExecutionProvider = SqlExecutionProvider as ComponentType<{
  threadId: string;
  agentName?: string;
  enabled: boolean;
  children?: ReactNode;
}>;

function renderMarkdown(
  content: string,
  isLoading: boolean,
  components?: Parameters<typeof MarkdownContent>[0]["components"],
) {
  return renderToStaticMarkup(
    createElement(MarkdownContent, { content, isLoading, components }),
  );
}

describe("MarkdownContent streaming code blocks", () => {
  it("renders fenced code without Streamdown highlighting while streaming", () => {
    const html = renderMarkdown(
      ["```html", '<main class="report">Hello</main>', "```"].join("\n"),
      true,
    );

    expect(html).toContain("data-streaming-code-block");
    expect(html).toContain('data-language="html"');
    expect(html).toContain(
      "&lt;main class=&quot;report&quot;&gt;Hello&lt;/main&gt;",
    );
    expect(html).not.toContain('data-streamdown="code-block"');
  });

  it("keeps inline code inline while streaming", () => {
    const html = renderMarkdown("Use `const answer = 42` here.", true);

    expect(html).toContain('data-streaming-inline-code="true"');
    expect(html).not.toContain("data-streaming-code-block");
  });

  it("keeps an unlabeled single-line fence as a block while streaming", () => {
    const html = renderMarkdown(["```", "x", "```"].join("\n"), true);

    expect(html).toContain("data-streaming-code-block");
    expect(html).not.toContain('data-streaming-inline-code="true"');
  });

  it("restores Streamdown code rendering after streaming finishes", () => {
    const html = renderMarkdown(
      ["```typescript", "const answer: number = 42;", "```"].join("\n"),
      false,
    );

    expect(html).toContain('data-streamdown="code-block"');
    expect(html).not.toContain("data-streaming-code-block");
  });

  it("preserves custom non-code renderers while streaming", () => {
    const html = renderMarkdown(
      "[Docs](https://example.com)\n\n![Chart](chart.png)",
      true,
      {
        a: ({ children, href }) =>
          createElement("a", { "data-custom-link": true, href }, children),
        img: (props: ImgHTMLAttributes<HTMLImageElement>) =>
          createElement("img", { ...props, "data-custom-image": true }),
      },
    );

    expect(html).toContain('data-custom-link="true"');
    expect(html).toContain('data-custom-image="true"');
  });

  it("preserves a caller-provided code renderer while streaming", () => {
    const html = renderMarkdown(
      ["```html", "<main />", "```"].join("\n"),
      true,
      {
        code: ({ children }) =>
          createElement("code", { "data-custom-code": true }, children),
      },
    );

    expect(html).toContain('data-custom-code="true"');
    expect(html).toContain("data-streaming-code-block");
  });

  it("does not paint an initial large streaming chunk all at once", () => {
    const content = "x".repeat(120);

    expect(renderMarkdown(content, true)).not.toContain(content);
    expect(renderMarkdown(content, false)).toContain(content);
  });
});

describe("MarkdownContent streaming animation", () => {
  it("uses Streamdown animation only for newly streamed words", () => {
    const html = renderMarkdown("Hello streaming world", true);

    expect(html).toContain("data-sd-animate");
    expect(html).toContain("--sd-animation:sd-fadeIn");
    expect(html).toContain("--sd-duration:200ms");
    expect(html).not.toContain("animate-fade-in");
  });

  it("does not animate completed markdown", () => {
    const html = renderMarkdown("Hello completed world", false);

    expect(html).not.toContain("data-sd-animate");
  });
});

describe("MarkdownContent strikethrough", () => {
  it("preserves single tildes in temperature ranges", () => {
    const html = renderMarkdown("周六23~30℃；周日22~30℃", false);

    expect(html).toContain("周六23~30℃；周日22~30℃");
    expect(html).not.toContain("<del>");
  });

  it("continues to render double-tilde strikethrough", () => {
    const html = renderMarkdown("状态：~~已取消~~", false);

    expect(html).toContain("<del>已取消</del>");
  });
});

describe("MarkdownContent SQL execution", () => {
  it("shows execute before the standard actions for completed DataAgent SQL", () => {
    const html = renderToStaticMarkup(
      createElement(
        TestI18nProvider,
        { initialLocale: "zh-CN" },
        createElement(
          TestSqlExecutionProvider,
          {
            threadId: "thread-1",
            agentName: "data-agent",
            enabled: true,
          },
          createElement(MarkdownContent, {
            content: ["```sql", "SELECT region FROM orders", "```"].join("\n"),
            isLoading: false,
          }),
        ),
      ),
    );

    expect(html).toContain('data-testid="sql-execute-button"');
    expect(html).toContain("执行");
    expect(html.indexOf('data-testid="sql-execute-button"')).toBeLessThan(
      html.indexOf('data-streamdown="code-block-download-button"'),
    );
    expect(html).toContain('data-streamdown="code-block-copy-button"');
  });

  it("does not show execute for a normal agent or a streaming SQL fence", () => {
    const normalAgent = renderToStaticMarkup(
      createElement(
        TestI18nProvider,
        { initialLocale: "en-US" },
        createElement(
          TestSqlExecutionProvider,
          {
            threadId: "thread-1",
            agentName: "lead-agent",
            enabled: false,
          },
          createElement(MarkdownContent, {
            content: ["```sql", "SELECT 1", "```"].join("\n"),
            isLoading: false,
          }),
        ),
      ),
    );
    const streaming = renderToStaticMarkup(
      createElement(
        TestI18nProvider,
        { initialLocale: "en-US" },
        createElement(
          TestSqlExecutionProvider,
          {
            threadId: "thread-1",
            agentName: "data-agent",
            enabled: true,
          },
          createElement(MarkdownContent, {
            content: ["```sql", "SELECT 1", "```"].join("\n"),
            isLoading: true,
          }),
        ),
      ),
    );

    expect(normalAgent).not.toContain('data-testid="sql-execute-button"');
    expect(streaming).not.toContain('data-testid="sql-execute-button"');
  });
});
