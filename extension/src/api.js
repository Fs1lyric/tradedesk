// The backend client shared by the popup and the background worker. The refresh token lives
// in chrome.storage.local; the access token only in chrome.storage.session, which the browser
// clears when it closes.

const trim = (url) => url.trim().replace(/\/+$/, "");

async function config() {
  const { server, refresh, dashboard } = await chrome.storage.local.get(["server", "refresh", "dashboard"]);
  return { server: server ?? null, refresh: refresh ?? null, dashboard: dashboard ?? null };
}

async function call(server, path, { method = "POST", body, token } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(server + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = res.status === 204 ? null : await res.json().catch(() => null);
  return { status: res.status, ok: res.ok, data };
}

const reason = (r) => (typeof r.data?.detail === "string" ? r.data.detail : `HTTP ${r.status}`);

async function store(pair) {
  await chrome.storage.local.set({ refresh: pair.refresh_token });
  await chrome.storage.session.set({ access: pair.access_token });
}

async function forget() {
  await chrome.storage.local.remove("refresh");
  await chrome.storage.session.remove("access");
}

export async function state() {
  const { server, refresh, dashboard } = await config();
  return { server, signedIn: Boolean(server && refresh), dashboard };
}

export async function setDashboard(url) {
  if (url.trim()) await chrome.storage.local.set({ dashboard: trim(url) });
  else await chrome.storage.local.remove("dashboard");
}

export async function login(serverUrl, email, password, totpCode) {
  const server = trim(serverUrl);
  const body = { email, password };
  if (totpCode) body.totp_code = totpCode;
  const r = await call(server, "/auth/login", { body });
  if (!r.ok) throw new Error(reason(r));
  await chrome.storage.local.set({ server });
  await store(r.data);
}

export async function signOut() {
  const { server, refresh } = await config();
  if (server && refresh) await call(server, "/auth/logout", { body: { refresh_token: refresh } }).catch(() => {});
  await forget();
}

async function refreshAccess(server, refresh) {
  const r = await call(server, "/auth/refresh", { body: { refresh_token: refresh } });
  if (!r.ok) {
    await forget();
    throw new Error("Signed out: sign in again from the toolbar button.");
  }
  await store(r.data);
  return r.data.access_token;
}

// A call with the access token, refreshing it once when the server says it has expired.
async function authed(path, body) {
  const { server, refresh } = await config();
  if (!server || !refresh) throw new Error("Signed out: sign in again from the toolbar button.");
  let { access } = await chrome.storage.session.get("access");
  if (!access) access = await refreshAccess(server, refresh);
  let r = await call(server, path, { body, token: access });
  if (r.status === 401) r = await call(server, path, { body, token: await refreshAccess(server, refresh) });
  if (!r.ok) throw new Error(reason(r));
  return r.data;
}

export const capture = (page) => authed("/news/capture", page);
