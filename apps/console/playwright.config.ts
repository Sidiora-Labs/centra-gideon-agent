import { defineConfig, devices } from '@playwright/test'


const PORT = Number(process.env.PW_PORT || 4318)
const BASE_URL = process.env.PW_BASE_URL || `http://localhost:${PORT}`

const GATEWAY_PORT = Number(process.env.PW_GATEWAY_PORT || 10437)

const STORAGE_STATE = process.env.STORAGE_STATE || 'e2e/.auth/state.json'

export const VISUAL_BASELINE_PLATFORMS = ['darwin'] as const

export const SCRIPTED = {
  type: 'scripted',
  scriptEnvVar: 'GIDEON_SCRIPTED_MODEL_SCRIPT',
  scriptPath: 'e2e/fixtures/scripted-chat.json',
  reply: 'SCRIPTED-E2E-OK: this reply came from the offline scripted provider.',
} as const

const GATEWAY_COMMAND = [
  'D="${TMPDIR:-/tmp}"; D="${D%/}/gideon-e2e-home"',
  'rm -rf "$D"; mkdir -p "$D/workspace"',
  `S="$PWD/${SCRIPTED.scriptPath}"`,
  `[ -f "$S" ] || { echo "GIDEON_E2E_FATAL: scripted-provider script not found at $S (cwd $PWD)" >&2; exit 1; }`,
  `printf '%s' '{"dashboard":{"user_name":"Keyur"}}' > "$D/config.json"`,
  'mkdir -p "$D/apps"',
  'cp -R e2e/fixtures/app-ui "$D/apps/e2e-ui-fixture"',
  '[ -f "$D/apps/e2e-ui-fixture/installed.json" ] && [ -f "$D/apps/e2e-ui-fixture/ui/index.mjs" ] '
    + '|| { echo "GIDEON_E2E_FATAL: app-UI fixture did not install into $D/apps (cwd $PWD)" >&2; exit 1; }',
  'GIDEON_BIN="../../.venv/bin/gideon"; [ -x "$GIDEON_BIN" ] || GIDEON_BIN=gideon',
  'export PYTHONPATH="$(cd ../.. && pwd)/runtime${PYTHONPATH:+:$PYTHONPATH}"',
  `GIDEON_HOME="$D" GIDEON_WORKSPACE="$D/workspace" ${SCRIPTED.scriptEnvVar}="$S" exec "$GIDEON_BIN" gateway --port ${GATEWAY_PORT} --no-open --json-ready`,
].join('\n')

export default defineConfig({
  testDir: './e2e',
  testIgnore: /onboardingGeometry\.spec\.ts$/,
  snapshotPathTemplate: '{testDir}/__screenshots__/{testFilePath}/{arg}-{platform}{ext}',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI
    ? [['github'], ['html', { open: 'never' }], ['junit', { outputFile: 'test-results/junit.xml' }]]
    : [['list']],
  expect: {
    toHaveScreenshot: { maxDiffPixelRatio: 0.01, animations: 'disabled', caret: 'hide', scale: 'css' },
  },
  use: {
    baseURL: BASE_URL,
    trace: 'on-first-retry',
    viewport: { width: 1280, height: 900 },
    storageState: STORAGE_STATE,
  },
  projects: [
    { name: 'setup', testMatch: /auth\.setup\.ts$/, use: { storageState: { cookies: [], origins: [] } } },
    { name: 'chromium', use: { ...devices['Desktop Chrome'] }, dependencies: ['setup'] },
  ],
  webServer: process.env.PW_NO_SERVER
    ? undefined
    : [
        {
          command: GATEWAY_COMMAND,
          wait: { stdout: /GIDEON_READY:.*"token":\s*"(?<pw_token>[^"]+)"/ },
          timeout: 180_000,
        },
        {
          command: `npm run build && npm run preview -- --port ${PORT} --strictPort`,
          url: BASE_URL,
          timeout: 180_000,
          reuseExistingServer: !process.env.CI,
          env: { GIDEON_PORT: String(GATEWAY_PORT) },
        },
      ],
})
