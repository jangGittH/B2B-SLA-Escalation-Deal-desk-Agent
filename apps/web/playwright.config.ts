import { existsSync } from "node:fs";
import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: process.env.UI_BASE_URL || "http://127.0.0.1:8001",
    viewport: { width: 1440, height: 1050 },
    launchOptions: {
      executablePath:
        process.env.CHROME_PATH ||
        (existsSync(
          "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        )
          ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
          : undefined),
    },
    trace: "retain-on-failure",
  },
  timeout: 30000,
});
