import { beforeEach, describe, expect, it, vi } from "vitest";
import { capture, login, signOut, state } from "../src/api.js";

// chrome.storage stand-in: one in-memory area per name.
function fakeChrome() {
  const areas = { local: {}, session: {} };
  const area = (name) => ({
    get: async (keys) => Object.fromEntries([keys].flat().filter((k) => k in areas[name]).map((k) => [k, areas[name][k]])),
    set: async (obj) => { Object.assign(areas[name], obj); },
    remove: async (keys) => { for (const k of [keys].flat()) delete areas[name][k]; },
  });
  return { areas, storage: { local: area("local"), session: area("session") } };
}

const json = (status, body) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

let calls;
function serve(handler) {
  calls = [];
  globalThis.fetch = vi.fn(async (url, init = {}) => {
    const req = { url, method: init.method ?? "GET", headers: init.headers ?? {}, body: init.body ? JSON.parse(init.body) : null };
    calls.push(req);
    return handler(req);
  });
}

beforeEach(() => { globalThis.chrome = fakeChrome(); });

describe("api", () => {
  it("logs in, keeps the refresh token in local and the access token in session storage", async () => {
    serve(() => json(200, { access_token: "a1", refresh_token: "r1" }));
    await login("http://localhost:8000/", "a@x.com", "pw", "");
    expect(calls[0].url).toBe("http://localhost:8000/auth/login");
    expect(calls[0].body).toEqual({ email: "a@x.com", password: "pw" });
    expect(chrome.areas.local).toMatchObject({ server: "http://localhost:8000", refresh: "r1" });
    expect(chrome.areas.session).toEqual({ access: "a1" });
    expect(await state()).toEqual({ server: "http://localhost:8000", signedIn: true, dashboard: null });
  });

  it("sends the totp code and reports the server's reason on failure", async () => {
    serve(() => json(401, { detail: "totp required" }));
    await expect(login("http://h", "a@x.com", "pw", "")).rejects.toThrow("totp required");
    await expect(login("http://h", "a@x.com", "pw", "123456")).rejects.toThrow();
    expect(calls[1].body.totp_code).toBe("123456");
  });

  it("refreshes an expired access token once, then retries the capture", async () => {
    chrome.areas.local = { server: "http://h", refresh: "r1" };
    chrome.areas.session = { access: "old" };
    serve((req) => {
      if (req.url.endsWith("/auth/refresh")) return json(200, { access_token: "a2", refresh_token: "r2" });
      return req.headers.Authorization === "Bearer a2" ? json(201, { stored: true, item: { id: 7 } }) : json(401, { detail: "expired" });
    });
    const out = await capture({ url: "https://n.com/a", title: "t", body: "b", published_at: null });
    expect(out).toEqual({ stored: true, item: { id: 7 } });
    expect(calls.map((c) => c.url.replace("http://h", ""))).toEqual(["/news/capture", "/auth/refresh", "/news/capture"]);
    expect(calls[1].body).toEqual({ refresh_token: "r1" });
    expect(chrome.areas.local.refresh).toBe("r2");
  });

  it("signs out when the refresh token is rejected", async () => {
    chrome.areas.local = { server: "http://h", refresh: "spent" };
    serve(() => json(401, { detail: "revoked" }));
    await expect(capture({ url: "https://n.com/a", title: "t", body: "b" })).rejects.toThrow("Signed out");
    expect(chrome.areas.local.refresh).toBeUndefined();
    expect((await state()).signedIn).toBe(false);
  });

  it("signOut revokes the refresh token on the server and forgets it", async () => {
    chrome.areas.local = { server: "http://h", refresh: "r1" };
    serve(() => new Response(null, { status: 204 }));
    await signOut();
    expect(calls[0]).toMatchObject({ url: "http://h/auth/logout", body: { refresh_token: "r1" } });
    expect(chrome.areas.local.refresh).toBeUndefined();
  });
});
