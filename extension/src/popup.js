import { login, setDashboard, signOut, state } from "./api.js";
import { captureTab } from "./capture.js";

const $ = (id) => document.getElementById(id);
const show = (id, on) => { $(id).hidden = !on; };

function error(message) {
  $("error").textContent = message ?? "";
  show("error", Boolean(message));
}

async function render() {
  const s = await state();
  show("signin", !s.signedIn);
  show("ready", s.signedIn);
  $("who").textContent = s.signedIn ? new URL(s.server).host : "";
  if (s.server) $("server").value = s.server;
  $("dashboard").value = s.dashboard ?? "";
}

const signed = (x) => (x > 0 ? `+${x.toFixed(2)}` : x.toFixed(2));

function el(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

async function showResult({ stored, item }) {
  const { dashboard } = await state();
  const tickers = item.tickers.length
    ? item.tickers.map((t) => el("span", { className: "chip", title: `impact ${t.impact.toFixed(2)}` },
        `${t.symbol} `, el("span", { className: t.impact >= 0 ? "up" : "down" }, signed(t.impact))))
    : [el("span", { className: "muted small" }, "No tickers linked")];
  const card = el("div", { className: "card" },
    el("p", { className: "small muted" }, stored ? "Saved to the feed" : "Already in the feed"),
    el("h2", {}, item.title),
    el("div", { className: "chips" }, ...tickers),
    el("p", { className: "small muted" },
      `${item.event_type.replaceAll("_", " ")} · sentiment `, el("span", { className: item.sentiment >= 0 ? "up" : "down" }, signed(item.sentiment)),
      ` · novelty ${item.novelty.toFixed(2)}`));
  if (dashboard) card.append(el("p", { className: "small" }, el("a", { href: `${dashboard}/news`, target: "_blank" }, "Open the feed")));
  $("result").replaceChildren(card);
  show("result", true);
}

$("signin").addEventListener("submit", async (e) => {
  e.preventDefault();
  error(null);
  const server = $("server").value.trim().replace(/\/+$/, "");
  const button = e.submitter;
  button.disabled = true;
  try {
    // Fetching the backend from the extension needs host access to it; ask while we have the click.
    if (!(await chrome.permissions.request({ origins: [`${new URL(server).origin}/*`] }))) {
      throw new Error("tradedesk needs access to the server to send pages to it.");
    }
    await login(server, $("email").value.trim(), $("password").value, $("totp").value.trim());
    $("password").value = $("totp").value = "";
    await render();
  } catch (err) {
    if (/totp/i.test(err.message)) {
      show("totp-row", true);
      $("totp").focus();
    }
    error(err.message);
  } finally {
    button.disabled = false;
  }
});

$("send").addEventListener("click", async () => {
  error(null);
  $("send").disabled = true;
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    await showResult(await captureTab(tab));
  } catch (err) {
    error(err.message);
    await render();
  } finally {
    $("send").disabled = false;
  }
});

$("dashboard").addEventListener("change", () => setDashboard($("dashboard").value));
$("signout").addEventListener("click", async () => { await signOut(); show("result", false); await render(); });

render();
