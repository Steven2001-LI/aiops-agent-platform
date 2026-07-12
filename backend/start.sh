#!/bin/bash
# AIOps Agent Platform - Startup Script
# ============================================================
# Usage: ./start.sh
# ============================================================

set -e

# Get script directory
cd "$(dirname "$0")"

# Install dependencies
echo "Installing dependencies..."
pip install -q -r requirements.txt

# Start the application
echo "Starting AIOps Agent Platform..."
python -m app.main
