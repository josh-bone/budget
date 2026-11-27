import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:5005",
    ...devices["Desktop Chrome"],
    viewport: { width: 1440, height: 1000 },
  },
  webServer: {
    command:
      "UV_CACHE_DIR=/tmp/uv-cache PYTHONPATH=src BUDGET_DB_PATH=/tmp/budget-ui-playwright.sqlite3 BUDGET_PORT=5005 uv run python app.py",
    url: "http://127.0.0.1:5005",
    // The journeys seed a dedicated database; always use the server configured
    // below so it reads that same database instead of an unrelated local app.
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
