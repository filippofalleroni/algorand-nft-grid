#!/bin/bash
# Algorand NFT Wall Generator — Launcher
# Just run ./start.sh every time you want to generate a wall.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Check if install has been run
if [ ! -d "venv" ]; then
    echo ""
    echo "  ⚠️  First time setup needed."
    echo "  Running installer ..."
    echo ""
    bash install.sh
fi

source venv/bin/activate
python3 nft_grid.py "$@"
