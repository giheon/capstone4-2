import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";

// Two projects, each with its own servers:
//
//   mock — the M2 mock backend inside a Vite dev server on 5181, with PRIVFRAME_PIPELINE_SANDBOX=1, so profiles
//          and catalog candidates the tests create land in a temporary copy of configs/pipeline.
//   real — the FastAPI backend in fixture mode (scripted models and detections: no keys, no network) on 8012,
//          and a Vite dev server on 5182 that proxies /api to it. The backend gets a fresh data directory and
//          its own copy of configs/pipeline, so nothing in the repository or in data/ is touched.
//
// `npx playwright test` runs both. `--project=mock` still starts every server; set PRIVFRAME_E2E_REAL=0 to
// leave the real project and its servers out (for a machine without uv or ffmpeg).
// The ports can be moved (PRIVFRAME_E2E_MOCK_PORT · _REAL_PORT · _BACKEND_PORT) so two test runs do not collide.
const MOCK_PORT = Number(process.env.PRIVFRAME_E2E_MOCK_PORT ?? 5181);
const REAL_PORT = Number(process.env.PRIVFRAME_E2E_REAL_PORT ?? 5182);
const BACKEND_PORT = Number(process.env.PRIVFRAME_E2E_BACKEND_PORT ?? 8012);
const withReal = process.env.PRIVFRAME_E2E_REAL !== "0";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "..");
const env = process.env as Record<string, string>;

// Workers load this file too: the directories are made once, by the first process, and handed down through the environment.
if (withReal && !process.env.PRIVFRAME_E2E_DIR) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "privframe-e2e-"));
  fs.mkdirSync(path.join(dir, "data"));
  fs.cpSync(path.join(repo, "configs", "pipeline"), path.join(dir, "pipeline"), { recursive: true });
  process.env.PRIVFRAME_E2E_DIR = dir;
}
const e2eDir = process.env.PRIVFRAME_E2E_DIR ?? "";

export default defineConfig({
  testDir: "./tests",
  outputDir:
    process.env.PRIVFRAME_TEST_OUTPUT ?? "/tmp/privframe-playwright-results",
  fullyParallel: true,
  use: {
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
  },
  projects: [
    {
      name: "mock",
      testIgnore: /real-.*\.spec\.ts$/,
      use: { baseURL: `http://127.0.0.1:${MOCK_PORT}` },
    },
    ...(withReal
      ? [
          {
            name: "real",
            testMatch: /real-.*\.spec\.ts$/,
            use: { baseURL: `http://127.0.0.1:${REAL_PORT}` },
          },
        ]
      : []),
  ],
  webServer: [
    {
      command: `npm run dev -- --port ${MOCK_PORT} --strictPort`,
      url: `http://127.0.0.1:${MOCK_PORT}`,
      reuseExistingServer: false,
      timeout: 30000,
      env: { ...env, PRIVFRAME_API: "mock", PRIVFRAME_PIPELINE_SANDBOX: "1" },
    },
    ...(withReal
      ? [
          {
            command: `uv run --group models uvicorn privframe.service.app:app --port ${BACKEND_PORT}`,
            cwd: repo,
            url: `http://127.0.0.1:${BACKEND_PORT}/api/config`,
            reuseExistingServer: false,
            timeout: 180000,
            env: {
              ...env,
              PRIVFRAME_FIXTURE: "1",
              // no example video: the fresh data directory has none, and a developer's own setting must not leak in
              PRIVFRAME_SAMPLE_VIDEO: "",
              PRIVFRAME_SERVICE_PROFILE: "",
              PRIVFRAME_DATA_DIR: path.join(e2eDir, "data"),
              PRIVFRAME_PIPELINE_DIR: path.join(e2eDir, "pipeline"),
            },
          },
          {
            command: `npm run dev -- --port ${REAL_PORT} --strictPort`,
            url: `http://127.0.0.1:${REAL_PORT}`,
            reuseExistingServer: false,
            timeout: 30000,
            env: { ...env, PRIVFRAME_API: "proxy", PRIVFRAME_API_URL: `http://127.0.0.1:${BACKEND_PORT}` },
          },
        ]
      : []),
  ],
});
