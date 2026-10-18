import { expect, test, type Page } from "@playwright/test";
import { routes } from "./fixtures";

async function signedIn(page: Page) {
  await page.addInitScript(() => localStorage.setItem("tradedesk.refresh", "r"));
  await page.route("**/api/**", (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname.replace(/^\/api/, "") + url.search;
    if (path === "/auth/refresh") return route.fulfill({ json: { access_token: "a", refresh_token: "r" } });
    const body = routes[path] ?? routes[url.pathname.replace(/^\/api/, "")];
    return body === undefined ? route.fulfill({ status: 404, json: { detail: "not found" } }) : route.fulfill({ json: body });
  });
}

const shot = (page: Page, name: string) =>
  page.screenshot({ path: `e2e/screens/${test.info().project.name}-${name}.png`, fullPage: true });

test("login", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  await shot(page, "login");
});

const pages: [string, string, string][] = [
  ["dashboard", "/", "Pending recommendations"],
  ["news", "/news", "Nvidia (NVDA) raises guidance"],
  ["recommendations", "/recommendations", "TSLA"],
  ["orders", "/orders", "New order"],
  ["rules", "/rules", "Fade negative overreaction"],
  ["backtests", "/backtests", "news_momentum"],
  ["settings", "/settings", "Megacap tech"],
];

for (const [name, path, text] of pages) {
  test(name, async ({ page }) => {
    await signedIn(page);
    await page.goto(path);
    await expect(page.getByText(text).first()).toBeVisible();
    await shot(page, name);
  });
}

test("backtest detail", async ({ page }) => {
  await signedIn(page);
  await page.goto("/backtests");
  await page.getByRole("cell", { name: "composite" }).first().click();
  await expect(page.locator("svg.chart")).toBeVisible();
  await shot(page, "backtest-detail");
});

test("no horizontal scroll on any page", async ({ page }) => {
  await signedIn(page);
  for (const [, path, text] of pages) {
    await page.goto(path);
    await expect(page.getByText(text).first()).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, path).toBeLessThanOrEqual(1);
  }
});
