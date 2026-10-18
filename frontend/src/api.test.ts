import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const json = (status: number, body: unknown) =>
  new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("api client", () => {
  beforeEach(() => { localStorage.clear(); vi.resetModules(); });
  afterEach(() => vi.unstubAllGlobals());

  it("logs in, sends the bearer token, and refreshes once on 401", async () => {
    const calls: [string, RequestInit | undefined][] = [];
    let accessN = 0;
    vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
      calls.push([url, init]);
      if (url.endsWith("/auth/login") || url.endsWith("/auth/refresh")) {
        accessN += 1;
        return json(200, { access_token: `a${accessN}`, refresh_token: `r${accessN}` });
      }
      const auth = (init?.headers as Record<string, string>).Authorization;
      return auth === "Bearer a1" ? json(401, { detail: "invalid token" }) : json(200, []);
    }));
    const { api } = await import("./api");
    await api.login("a@example.com", "pw");
    expect(localStorage.getItem("tradedesk.refresh")).toBe("r1");
    await expect(api.accounts()).resolves.toEqual([]);
    const urls = calls.map(([u]) => u.replace("/api", ""));
    expect(urls).toEqual(["/auth/login", "/accounts", "/auth/refresh", "/accounts"]);
    expect((calls[3][1]?.headers as Record<string, string>).Authorization).toBe("Bearer a2");
    expect(localStorage.getItem("tradedesk.refresh")).toBe("r2");
  });

  it("signs out when the refresh token is rejected", async () => {
    localStorage.setItem("tradedesk.refresh", "stale");
    vi.stubGlobal("fetch", vi.fn(async () => json(401, { detail: "invalid token" })));
    const { api, hasSession, onAuthChange } = await import("./api");
    const seen: boolean[] = [];
    onAuthChange((s) => seen.push(s));
    await expect(api.accounts()).rejects.toMatchObject({ status: 401 });
    expect(hasSession()).toBe(false);
    expect(seen).toEqual([false]);
  });

  it("surfaces the API's error detail", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(409, { detail: "recommendation is executed" })));
    const { api } = await import("./api");
    await expect(api.approve("x")).rejects.toThrow("recommendation is executed");
  });
});
