#!/usr/bin/env bash
set -euo pipefail

ART_DIR=artifacts/full_flow
mkdir -p ${ART_DIR}

# Ensure node deps
if [ ! -d node_modules ]; then
  echo "Installing npm deps..."
  npm ci || npm install
fi

node tests/e2e/full_flow.js

echo "Full flow finished. Artifacts in ${ART_DIR}" 
