#!/bin/bash
# Algorand NFT Wall Generator — Installer
# Run this once to set everything up.

echo ""
echo "╔══════════════════════════════════════╗"
echo "║    Algorand NFT Wall Generator       ║"
echo "║    Installation                      ║"
echo "╚══════════════════════════════════════╝"
echo ""

# Check Python 3
if ! command -v python3 &> /dev/null; then
    echo "  ❌  Python 3 is not installed."
    echo "      Please download it from https://www.python.org/downloads/"
    echo "      Then run this script again."
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "  ✅  Python $PYTHON_VERSION found."

# Create virtual environment
echo "  Setting up the environment ..."
python3 -m venv venv
if [ $? -ne 0 ]; then
    echo "  ❌  Failed to create environment. Please check your Python installation."
    exit 1
fi

# Install dependencies
source venv/bin/activate
pip install -q -r requirements.txt
if [ $? -ne 0 ]; then
    echo "  ❌  Failed to install dependencies."
    exit 1
fi

echo "  ✅  All dependencies installed."
echo ""
echo "══════════════════════════════════════════"
echo "  Installation complete!"
echo ""
echo "  To generate your NFT wall, run:"
echo "      ./start.sh"
echo ""
echo "  That's it — enjoy! 🖼️"
echo "══════════════════════════════════════════"
echo ""
