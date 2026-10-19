// Context menu and keyboard shortcut capture. The result shows on the toolbar badge.
import { captureTab } from "./capture.js";

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({ id: "capture-page", title: "Send page to tradedesk", contexts: ["page"] });
  chrome.contextMenus.create({ id: "capture-selection", title: "Send selection to tradedesk", contexts: ["selection"] });
});

async function run(tab, selection) {
  try {
    const out = await captureTab(tab, selection);
    const symbols = out.item.tickers.map((t) => t.symbol).join(", ") || "no tickers";
    await badge(tab.id, "✓", "#138a4b", `${out.stored ? "Saved" : "Already in the feed"}: ${symbols}`);
  } catch (e) {
    await badge(tab.id, "!", "#c8352b", e.message);
  }
}

async function badge(tabId, text, color, title) {
  await chrome.action.setBadgeBackgroundColor({ tabId, color });
  await chrome.action.setBadgeText({ tabId, text });
  await chrome.action.setTitle({ tabId, title: `tradedesk: ${title}` });
  setTimeout(() => chrome.action.setBadgeText({ tabId, text: "" }), 5000);
}

chrome.contextMenus.onClicked.addListener((info, tab) => run(tab, info.selectionText ?? ""));

chrome.commands.onCommand.addListener(async (command, tab) => {
  if (command === "capture-page") run(tab ?? (await chrome.tabs.query({ active: true, currentWindow: true }))[0], "");
});
