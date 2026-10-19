// Read the article out of a tab and send it to the backend.
import { capture } from "./api.js";

export async function captureTab(tab, selection = "") {
  if (!tab?.id || !/^https?:/.test(tab.url ?? "")) throw new Error("Only web pages can be sent.");
  const target = { tabId: tab.id };
  await chrome.scripting.executeScript({ target, files: ["src/extract.js"] });
  const [hit] = await chrome.scripting.executeScript({
    target,
    func: (sel) => globalThis.tradedeskExtract(document, location.href, sel || String(getSelection())),
    args: [selection],
  });
  const page = hit?.result;
  if (!page || (!page.title && !page.body)) throw new Error("Couldn't find any article text on this page.");
  return capture(page);
}
