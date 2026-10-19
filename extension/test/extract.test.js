import { beforeAll, describe, expect, it } from "vitest";

beforeAll(async () => {
  await import("../src/extract.js"); // a classic script: it sets globalThis.tradedeskExtract
});

const page = (html, url = "https://news.example.com/story?id=1") => {
  const doc = new DOMParser().parseFromString(html, "text/html");
  return globalThis.tradedeskExtract(doc, url);
};

const para = (s) => `<p>${s} ${"Revenue and margins came in ahead of what analysts expected. ".repeat(2)}</p>`;

describe("tradedeskExtract", () => {
  it("prefers og/meta fields and the article body over page chrome", () => {
    const got = page(`<html><head>
      <title>Apple beats | Example News</title>
      <meta property="og:title" content="Apple beats estimates">
      <link rel="canonical" href="/apple-beats">
      <meta property="article:published_time" content="2026-10-19T12:00:00Z">
      </head><body>
      <nav><p>${"Home Markets Tech Opinion ".repeat(8)}</p></nav>
      <article>${para("Apple Inc. reported record revenue.")}${para("Shares rose after hours.")}
        <aside><p>${"Related: ten stocks to watch this week and more. ".repeat(3)}</p></aside>
        <script>var x = "not text";</script></article>
      <footer><p>${"Copyright Example News, all rights reserved. ".repeat(3)}</p></footer>
      </body></html>`);
    expect(got.title).toBe("Apple beats estimates");
    expect(got.url).toBe("https://news.example.com/apple-beats");
    expect(got.published_at).toBe("2026-10-19T12:00:00.000Z");
    expect(got.body.split("\n\n")).toHaveLength(2);
    expect(got.body).toMatch(/^Apple Inc\. reported record revenue\./);
    expect(got.body).not.toMatch(/Related|Copyright|Home Markets|not text/);
  });

  it("falls back to JSON-LD, h1 and the densest block", () => {
    const got = page(`<html><head><title>t</title>
      <script type="application/ld+json">${JSON.stringify([
        { "@type": "BreadcrumbList" },
        { "@type": "NewsArticle", headline: "Tesla recalls 2M cars", datePublished: "2026-10-18T09:30:00-04:00" },
      ])}</script></head><body>
      <h1>Tesla recall</h1>
      <div class="sidebar">${para("Short sidebar.")}</div>
      <div class="story">${para("Tesla is recalling vehicles.")}${para("Regulators opened a probe.")}${para("The stock fell.")}</div>
      </body></html>`);
    expect(got.title).toBe("Tesla recalls 2M cars");
    expect(got.published_at).toBe("2026-10-18T13:30:00.000Z");
    expect(got.body).toMatch(/^Tesla is recalling/);
    expect(got.body).not.toMatch(/sidebar/);
    expect(got.url).toBe("https://news.example.com/story?id=1");
  });

  it("uses the selection when there is one, ignores bad dates, and caps the length", () => {
    const got = page(`<html><head><title>Plain title</title>
      <meta name="date" content="not a date"></head>
      <body><article>${para("x".repeat(200_000))}</article></body></html>`);
    expect(got.title).toBe("Plain title");
    expect(got.published_at).toBeNull();
    expect(got.body.length).toBe(100_000);
    const doc = new DOMParser().parseFromString("<p>whole page</p>", "text/html");
    expect(globalThis.tradedeskExtract(doc, "https://x.com/a", "  Picked text  ").body).toBe("Picked text");
  });
});
