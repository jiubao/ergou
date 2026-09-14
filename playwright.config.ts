import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests/browser',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  workers: 1,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: { baseURL: 'http://127.0.0.1:17894', trace: 'retain-on-failure' },
  webServer: {
    command: 'uv run --project services python services/tests/browser_server.py',
    url: 'http://127.0.0.1:17894/api/v1/health',
    timeout: 90_000,
    reuseExistingServer: false,
  },
});
