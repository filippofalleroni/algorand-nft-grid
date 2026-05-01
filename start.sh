#!/bin/bash
# Algorand NFT Wall Generator — Launcher

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Check if install has been run
if [ ! -d "venv" ]; then
    echo ""
    echo "  Setting up for the first time ..."
    bash install.sh
fi

# Auto-update from GitHub
echo "  Checking for updates ..."
git pull -q
if [ $? -eq 0 ]; then
    echo "  Up to date."
else
    echo "  Could not check for updates (no internet?). Continuing anyway."
fi
echo ""

source venv/bin/activate
python3 nft_grid.py "$@"
