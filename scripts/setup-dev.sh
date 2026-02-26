#!/usr/bin/env bash
# Setup the development environment for LLMOS Bridge.
# Run once after cloning the repository.

set -euo pipefail

echo "Setting up LLMOS Bridge development environment..."

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | cut -d' ' -f2 | cut -d'.' -f1-2)
REQUIRED="3.11"
if [[ "$(printf '%s\n' "$REQUIRED" "$PYTHON_VERSION" | sort -V | head -n1)" != "$REQUIRED" ]]; then
    echo "ERROR: Python $REQUIRED+ required. Found: $PYTHON_VERSION"
    exit 1
fi

# Check Poetry
if ! command -v poetry &> /dev/null; then
    echo "Installing Poetry..."
    curl -sSL https://install.python-poetry.org | python3 -
fi

# Install main package dependencies
echo "Installing llmos-bridge..."
cd packages/llmos-bridge
poetry install --with dev --all-extras
cd ../..

# Install SDK package
echo "Installing langchain-llmos..."
cd packages/langchain-llmos
poetry install --with dev
cd ../..

# Install pre-commit hooks
echo "Installing pre-commit hooks..."
poetry run pre-commit install

# Run initial checks
echo "Running linter..."
cd packages/llmos-bridge
poetry run ruff check llmos_bridge/ --fix
poetry run ruff format llmos_bridge/
cd ../..

echo ""
echo "Development environment ready."
echo ""
echo "  Start daemon:  cd packages/llmos-bridge && poetry run llmos-bridge daemon start"
echo "  Run tests:     cd packages/llmos-bridge && poetry run pytest"
echo "  Check types:   cd packages/llmos-bridge && poetry run mypy llmos_bridge/"
echo ""
