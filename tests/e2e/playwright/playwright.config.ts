import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './',
  timeout: 60_000,
  expect: { timeout: 5000 },
  fullyParallel: true,
  reporter: [['list'], ['html', { outputFolder: 'artifacts/playwright-report', open: 'never' }]],
  use: {
    actionTimeout: 0,
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:3000',
    trace: 'on-first-retry',
    video: 'on',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
});
