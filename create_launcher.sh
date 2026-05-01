#!/bin/bash
# Creates a double-clickable launcher on your Desktop

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LAUNCHER="$HOME/Desktop/NFT Wall Generator.command"

cat > "$LAUNCHER" << LAUNCHER
#!/bin/bash
cd "$SCRIPT_DIR"
./start.sh
LAUNCHER

chmod +x "$LAUNCHER"

echo ""
echo "  ✅  Done! A launcher has been created on your Desktop."
echo "      Just double-click 'NFT Wall Generator' to start."
echo ""
