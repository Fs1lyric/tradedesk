// Fail if the manifest is malformed or points at files that don't exist.
import { existsSync, readFileSync } from "node:fs";

const m = JSON.parse(readFileSync("manifest.json", "utf8"));
const errors = [];
const need = (cond, msg) => { if (!cond) errors.push(msg); };

need(m.manifest_version === 3, "manifest_version must be 3");
need(/^\d+(\.\d+){0,3}$/.test(m.version ?? ""), `bad version ${m.version}`);
need((m.name ?? "").length <= 75, "name is over 75 characters");
need((m.description ?? "").length <= 132, "description is over 132 characters");

const files = [
  m.background?.service_worker,
  m.action?.default_popup,
  ...Object.values(m.icons ?? {}),
  ...Object.values(m.action?.default_icon ?? {}),
  "src/extract.js", // injected by src/capture.js
];
for (const f of files) need(f && existsSync(f), `missing file ${f}`);

// Files the popup page loads.
const popup = readFileSync(m.action.default_popup, "utf8");
const dir = m.action.default_popup.replace(/[^/]+$/, "");
for (const [, ref] of popup.matchAll(/(?:src|href)="([^":]+)"/g)) need(existsSync(dir + ref), `popup refers to missing ${ref}`);

if (errors.length) {
  console.error(errors.join("\n"));
  process.exit(1);
}
console.log(`manifest ok: ${files.length} files`);
