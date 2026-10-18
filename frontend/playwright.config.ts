import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  outputDir: "e2e/results",
  use: { baseURL: "http://localhost:4173", colorScheme: "dark" },
  projects: [
    { name: "desktop", use: { viewport: { width: 1360, height: 900 } } },
    { name: "phone", use: { ...devices["Pixel 7"] } },
  ],
  webServer: { command: "npx vite preview --port 4173 --strictPort", port: 4173, reuseExistingServer: false },
});
