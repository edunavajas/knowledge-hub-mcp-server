#!/bin/sh
# Install the knowledge-hub-mcp stdio server (Linux and macOS).
set -eu

REPO="${KNOWLEDGE_HUB_REPO:-https://github.com/edunavajas/knowledge-hub-mcp-server.git}"
HOME_DIR="${KNOWLEDGE_HUB_HOME:-$HOME/.local/share/knowledge-hub-mcp}"
BIN_DIR="${KNOWLEDGE_HUB_BIN:-$HOME/.local/bin}"

echo "knowledge-hub-mcp installer"
echo "  repo:    $REPO"
echo "  home:    $HOME_DIR"
echo "  bin dir: $BIN_DIR"

if [ -d "$HOME_DIR/.git" ]; then
  echo "Updating existing checkout..."
  git -C "$HOME_DIR" pull --ff-only
elif [ -d "$HOME_DIR" ]; then
  echo "ERROR: $HOME_DIR exists but is not a git checkout." >&2
  echo "Remove it or set KNOWLEDGE_HUB_HOME to a different path." >&2
  exit 1
else
  mkdir -p "$(dirname "$HOME_DIR")"
  echo "Cloning..."
  git clone --depth 1 "$REPO" "$HOME_DIR"
fi

chmod +x "$HOME_DIR/knowledge_hub_mcp.py"
mkdir -p "$BIN_DIR"
ln -sf "$HOME_DIR/knowledge_hub_mcp.py" "$BIN_DIR/knowledge-hub-mcp"

echo "Installed:"
echo "  server:  $HOME_DIR/knowledge_hub_mcp.py"
echo "  command: $BIN_DIR/knowledge-hub-mcp"
echo
echo "Next steps:"
echo "  1. Credentials: create \$HOME/.config/knowledge-hub/knowledge.env"
echo "     (or point KNOWLEDGE_HUB_ENV_FILE at wherever you keep it)."
echo "  2. Point your MCP client at this absolute path:"
echo "       $BIN_DIR/knowledge-hub-mcp"
echo "  3. Verify: $BIN_DIR/knowledge-hub-mcp --version"
echo
echo "If $BIN_DIR is not on your PATH, use the absolute path above."
