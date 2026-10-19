import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  outputDir: "e2e/results",
  use: { baseURL: "http://localhost:4174", viewport: { width: 340, height: 520 } },
  projects: [
    { name: "light", use: { colorScheme: "light" } },
    { name: "dark", use: { colorScheme: "dark" } },
  ],
  webServer: { command: "npx vite --port 4174 --strictPort", port: 4174, reuseExistingServer: false },
});
