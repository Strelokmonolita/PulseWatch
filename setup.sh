#!/bin/bash
set -euo pipefail

# Installation script for PulseWatch
cd "$(dirname "$0")"

echo "Installing PulseWatch dependencies..."
python -m pip install -e ".[dev]"
echo "✓ Dependencies installed"
