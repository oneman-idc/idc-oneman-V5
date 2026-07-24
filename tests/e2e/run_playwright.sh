#!/usr/bin/env bash
set -euo pipefail

# Install deps and run Playwright tests, saving artifacts under artifacts/playwright
ARTIFACT_DIR=artifacts/playwright
mkdir -p ${ARTIFACT_DIR}

# Ensure node_modules
if [ ! -d node_modules ]; then
  echo "Installing npm deps..."
  npm ci --omit=dev || npm install
fi

# Install Playwright browsers if playwright is available
if npx playwright --version >/dev/null 2>&1; then
  echo "Ensuring Playwright browsers are installed..."
  npx playwright install --with-deps || true
else
  echo "Playwright not found; ensure dev deps include @playwright/test"
fi

# Run tests
npx playwright test --config=tests/e2e/playwright/playwright.config.ts --project=chromium --reporter=list --output=${ARTIFACT_DIR}

# Move generated artifacts (videos, screenshots, report)
mv artifacts/* ${ARTIFACT_DIR}/ 2>/dev/null || true

echo "Playwright run finished. Artifacts in ${ARTIFACT_DIR}" 
