#!/usr/bin/env bash
# Run mypy strict type checking on all packages.

set -euo pipefail

echo "Running mypy on llmos-bridge..."
cd packages/llmos-bridge
poetry run mypy llmos_bridge/ --strict --ignore-missing-imports
cd ../..

echo "Running mypy on langchain-llmos..."
cd packages/langchain-llmos
poetry run mypy langchain_llmos/ --ignore-missing-imports
cd ../..

echo "Type check passed."
