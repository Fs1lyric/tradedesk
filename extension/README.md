# tradedesk news capture

A Manifest V3 extension that sends the article you're reading to tradedesk. The backend
(`POST /news/capture`) runs it through the same pipeline as the news workers: dedup, ticker
linking, event type, sentiment, novelty and impact. Signals pick it up on the next refresh.

## Install

1. Download the `tradedesk-extension` artifact from a CI run and unzip it (or use this folder).
2. Open `chrome://extensions`, turn on Developer mode, then "Load unpacked" and pick the folder.
3. Click the toolbar button, enter the backend URL (default `http://localhost:8000`) and sign in.
   Chrome asks for access to that server once; the extension can't reach it otherwise.

## Use

- Toolbar button, then "Send this page". Select text first to send only the selection.
- Right-click a page or a selection: "Send page/selection to tradedesk". The badge shows the result.
- Alt+Shift+T sends the current page.

## How it works

- `src/extract.js` finds the title (og, JSON-LD, h1), canonical url, publish time and the
  densest block of paragraphs, skipping nav, asides and footers. Capped at 100k characters.
- `src/api.js` keeps the refresh token in `chrome.storage.local` and the access token in
  `chrome.storage.session`, refreshes once on a 401, and signs out when the refresh is refused.
- A url already in the feed (after dropping tracking parameters) isn't stored twice.

## Develop

`npm test` (vitest + jsdom), `npm run check` (manifest), `npm run screenshots` (Playwright, popup
with a stubbed `chrome` API). These run in CI; `python3 scripts/icons.py` redraws the icons.
