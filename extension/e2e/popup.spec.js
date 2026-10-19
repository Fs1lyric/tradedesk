import { expect, test } from "@playwright/test";

// The popup runs as a normal page here, with chrome.* stubbed and the backend mocked.
async function stubChrome(page, local = {}) {
  await page.addInitScript((initial) => {
    const make = (data) => ({
      get: async (keys) => Object.fromEntries([keys].flat().filter((k) => k in data).map((k) => [k, data[k]])),
      set: async (obj) => { Object.assign(data, obj); },
      remove: async (keys) => { for (const k of [keys].flat()) delete data[k]; },
    });
    const page = {
      url: "https://news.example.com/nvidia-guidance",
      title: "Nvidia raises guidance on data center demand",
      body: "Nvidia Corporation raised its revenue outlook.",
      published_at: "2026-10-19T12:00:00.000Z",
    };
    globalThis.chrome = {
      storage: { local: make(initial), session: make({}) },
      permissions: { request: async () => true },
      tabs: { query: async () => [{ id: 1, url: page.url }] },
      scripting: { executeScript: async ({ func }) => (func ? [{ result: page }] : []) },
    };
  }, local);
  await page.route("http://localhost:8000/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/auth/login") return route.fulfill({ json: { detail: "totp required" }, status: 401 });
    if (path === "/auth/refresh") return route.fulfill({ json: { access_token: "a", refresh_token: "r2" } });
    if (path === "/news/capture") return route.fulfill({ status: 201, json: { stored: true, item: {
      id: 1, title: "Nvidia raises guidance on data center demand", url: "https://news.example.com/nvidia-guidance",
      source: "extension", published_at: "2026-10-19T12:00:00Z", available_at: "2026-10-19T12:01:00Z",
      event_type: "guidance_raise", sentiment: 0.62, novelty: 0.91, cluster_id: 1, body: "…",
      tickers: [{ symbol: "NVDA", confidence: 0.98, relevance: 1, novelty: 0.91, impact: 0.74 },
                { symbol: "AMD", confidence: 0.6, relevance: 0.3, novelty: 0.8, impact: 0.12 }] } } });
    return route.fulfill({ status: 404, json: { detail: "not found" } });
  });
}

const shot = (page, name) => page.screenshot({ path: `e2e/screens/${test.info().project.name}-${name}.png` });

test("signed out, asks for the two-factor code when the server wants one", async ({ page }) => {
  await stubChrome(page);
  await page.goto("/src/popup.html");
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  await page.getByLabel("Email").fill("trader@example.com");
  await page.getByLabel("Password").fill("correct horse 1");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toHaveText("totp required");
  await expect(page.getByLabel("Two-factor code")).toBeVisible();
  await expect(page.getByRole("button", { name: "Send this page" })).toBeHidden();
  await shot(page, "signin");
});

test("signed in, sends the page and shows its score", async ({ page }) => {
  await stubChrome(page, { server: "http://localhost:8000", refresh: "r1", dashboard: "http://localhost:5173" });
  await page.goto("/src/popup.html");
  await expect(page.getByText("localhost:8000")).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeHidden();
  await page.getByRole("button", { name: "Send this page" }).click();
  await expect(page.getByText("Saved to the feed")).toBeVisible();
  await expect(page.getByText("NVDA")).toBeVisible();
  await expect(page.getByRole("link", { name: "Open the feed" })).toHaveAttribute("href", "http://localhost:5173/news");
  await shot(page, "captured");
});
