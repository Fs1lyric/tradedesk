// Pull the article out of a news page: title, canonical url, publish time and body text.
// A classic script, not a module: the background worker injects it into the tab, then calls
// globalThis.tradedeskExtract(document, location.href).
(() => {
  const MAX_BODY = 100_000;
  const MIN_PARAGRAPH = 40; // shorter <p>s are bylines, captions and buttons
  const SKIP = "nav, aside, footer, header, figure, figcaption, form, script, style, noscript, [role=navigation], [role=complementary], [aria-hidden=true]";
  const ARTICLE_TYPES = new Set(["Article", "NewsArticle", "ReportageNewsArticle", "AnalysisNewsArticle", "BlogPosting"]);

  const clean = (s) => (s ?? "").replace(/\s+/g, " ").trim();
  const meta = (doc, ...names) => {
    for (const n of names) {
      const el = doc.querySelector(`meta[property="${n}"], meta[name="${n}"], meta[itemprop="${n}"]`);
      if (clean(el?.getAttribute("content"))) return clean(el.getAttribute("content"));
    }
    return null;
  };

  function jsonLdArticle(doc) {
    for (const s of doc.querySelectorAll('script[type="application/ld+json"]')) {
      let data;
      try { data = JSON.parse(s.textContent); } catch { continue; }
      const nodes = [data].flat().flatMap((d) => (d && d["@graph"] ? d["@graph"] : [d]));
      const hit = nodes.find((d) => d && [d["@type"]].flat().some((t) => ARTICLE_TYPES.has(t)));
      if (hit) return hit;
    }
    return null;
  }

  function isoDate(...candidates) {
    for (const c of candidates) {
      const d = c ? new Date(c) : null;
      if (d && !Number.isNaN(d.getTime())) return d.toISOString();
    }
    return null;
  }

  function httpUrl(href, base) {
    if (!href) return null;
    try {
      const u = new URL(href, base);
      return u.protocol === "http:" || u.protocol === "https:" ? u.href : null;
    } catch { return null; }
  }

  function paragraphs(root) {
    return [...root.querySelectorAll("p")]
      .filter((p) => !p.closest(SKIP))
      .map((p) => clean(p.textContent))
      .filter((t) => t.length >= MIN_PARAGRAPH);
  }

  // The element whose own <p> children hold the most text: the story, not the sidebar.
  function densest(doc) {
    const preferred = doc.querySelector('[itemprop="articleBody"], article, main, [role=main]');
    const scope = preferred ?? doc.body ?? doc.documentElement;
    const score = new Map();
    for (const p of scope.querySelectorAll("p")) {
      if (p.closest(SKIP) || !p.parentElement) continue;
      const n = clean(p.textContent).length;
      if (n >= MIN_PARAGRAPH) score.set(p.parentElement, (score.get(p.parentElement) ?? 0) + n);
    }
    let best = null;
    for (const [el, n] of score) if (!best || n > best[1]) best = [el, n];
    return best ? best[0] : scope;
  }

  globalThis.tradedeskExtract = (doc, href, selection = "") => {
    const ld = jsonLdArticle(doc);
    const title = meta(doc, "og:title", "twitter:title") || clean(ld?.headline)
      || clean(doc.querySelector("h1")?.textContent) || clean(doc.title);
    const canonical = doc.querySelector('link[rel="canonical"]')?.getAttribute("href");
    const url = httpUrl(canonical, href) || httpUrl(meta(doc, "og:url"), href) || href;
    const published_at = isoDate(
      meta(doc, "article:published_time"), ld?.datePublished,
      meta(doc, "datePublished", "pubdate", "publishdate", "date", "DC.date.issued"),
      doc.querySelector("time[datetime]")?.getAttribute("datetime"),
    );
    let body = clean(selection);
    if (!body) {
      const fromDom = paragraphs(densest(doc)).join("\n\n");
      const fromLd = clean(ld?.articleBody);
      body = fromLd.length > fromDom.length ? fromLd : fromDom;
    }
    return { url, title, body: body.slice(0, MAX_BODY), published_at };
  };
})();
