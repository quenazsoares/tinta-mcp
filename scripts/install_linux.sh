#!/usr/bin/env bash
# One-command installer for Tinta (Inkscape MCP server) on Linux.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

step() { echo -e "\n\033[1;36m==> $1\033[0m"; }
ok()   { echo -e "    \033[1;32mOK:\033[0m $1"; }
warn() { echo -e "    \033[1;33mWARNING:\033[0m $1"; }
err()  { echo -e "    \033[1;31mERROR:\033[0m $1"; }

# --- Step 1: Check dependencies ---------------------------------------------
step "Checking required tools"

if ! command -v inkscape >/dev/null 2>&1; then
    err "Inkscape is not installed or not on PATH."
    echo "    On Debian/Ubuntu: sudo apt install inkscape"
    echo "    On Arch Linux:    sudo pacman -S inkscape"
    echo "    On Fedora:        sudo dnf install inkscape"
    exit 1
fi
ok "Found Inkscape at $(command -v inkscape)"

if ! command -v gdbus >/dev/null 2>&1; then
    err "gdbus is not installed or not on PATH."
    echo "    On Debian/Ubuntu: sudo apt install libglib2.0-bin"
    echo "    On Arch Linux:    sudo pacman -S glib2"
    echo "    On Fedora:        sudo dnf install glib2"
    exit 1
fi
ok "Found gdbus at $(command -v gdbus)"

# --- Step 2: Extensions directory -------------------------------------------
step "Locating Inkscape's user extensions directory"

USER_DATA_DIR="$(inkscape --user-data-directory 2>/dev/null | tr -d '\r' | tail -n 1 || true)"
if [ -z "$USER_DATA_DIR" ] || [ ! -d "$USER_DATA_DIR" ]; then
    USER_DATA_DIR="${HOME}/.config/inkscape"
fi
EXTENSIONS_DIR="${USER_DATA_DIR}/extensions"
mkdir -p "$EXTENSIONS_DIR"
ok "Extensions directory: $EXTENSIONS_DIR"

# --- Step 3: Install the Tinta extension ------------------------------------
step "Installing the Tinta extension"

# Remove legacy files if present
rm -f "${EXTENSIONS_DIR}/inkscape_mcp.py" "${EXTENSIONS_DIR}/inkscape_mcp.inx"
rm -rf "${EXTENSIONS_DIR}/inkmcp"

cp "${REPO_ROOT}/extension/tinta_mcp.py" "$EXTENSIONS_DIR/"
cp "${REPO_ROOT}/extension/tinta_mcp.inx" "$EXTENSIONS_DIR/"

rm -rf "${EXTENSIONS_DIR}/tinta_mcp_ops"
mkdir -p "${EXTENSIONS_DIR}/tinta_mcp_ops"
cp -r "${REPO_ROOT}/extension/tinta_mcp_ops/." "${EXTENSIONS_DIR}/tinta_mcp_ops/"
find "${EXTENSIONS_DIR}/tinta_mcp_ops" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

ok "Tinta extension installed to $EXTENSIONS_DIR"

# --- Step 4: Python dependencies and global tool via uv --------------------
step "Setting up Python environment with uv"

TOOL_INSTALLED=false
if command -v uv >/dev/null 2>&1; then
    (cd "$REPO_ROOT" && uv sync)
    ok "Environment synchronized via uv (.venv)"

    echo -e "\033[0;36mInstalling tinta as a global CLI tool via 'uv tool install'...\033[0m"
    if (cd "$REPO_ROOT" && uv tool install . --force >/dev/null 2>&1); then
        TOOL_INSTALLED=true
        ok "tinta command is now globally available on your system"
    else
        warn "Could not install global tool via 'uv tool install', fallback to local uv run."
    fi
else
    warn "uv is not installed. Install it via 'curl -LsSf https://astral.sh/uv/install.sh | sh'."
fi

# --- Step 5: Install AI Agent Skill (optional) ------------------------------
step "Checking for AI agent skill directories"

CANDIDATE_SKILL_DIRS=(
    "${HOME}/.gemini/config/skills"
    "${HOME}/.claude/skills"
    "${HOME}/.cursor/skills"
)

SOURCE_SKILL_DIR="${REPO_ROOT}/.agents/skills/tinta-designer"
if [ ! -d "$SOURCE_SKILL_DIR" ]; then
    SOURCE_SKILL_DIR="${REPO_ROOT}/skills/tinta-designer"
fi

if [ -d "$SOURCE_SKILL_DIR" ]; then
    for cand in "${CANDIDATE_SKILL_DIRS[@]}"; do
        parent="$(dirname "$cand")"
        if [ -d "$parent" ]; then
            read -p "Detected AI agent directory ($cand). Install the 'tinta-designer' skill here? [Y/n] " -r reply
            reply="${reply:-y}"
            if [[ "$reply" =~ ^[YySs]$ ]]; then
                target_skill="${cand}/tinta-designer"
                rm -rf "$target_skill"
                mkdir -p "$target_skill"
                cp -r "${SOURCE_SKILL_DIR}"/* "$target_skill/"
                ok "Installed tinta-designer skill to $target_skill"
            else
                echo "    Skipped skill installation for $cand"
            fi
        fi
    done
fi

# --- Step 6: Generate and print MCP client configuration -------------------
step "MCP client configuration"

CONFIG_FILE="${REPO_ROOT}/mcp_config.snippet.json"
if [ "$TOOL_INSTALLED" = true ]; then
    cat <<EOF > "$CONFIG_FILE"
{
  "mcpServers": {
    "tinta": {
      "command": "tinta"
    }
  }
}
EOF
else
    cat <<EOF > "$CONFIG_FILE"
{
  "mcpServers": {
    "tinta": {
      "command": "uv",
      "args": [
        "--directory",
        "${REPO_ROOT}",
        "run",
        "tinta"
      ]
    }
  }
}
EOF
fi

cat "$CONFIG_FILE"
echo ""
ok "Configuration saved to $CONFIG_FILE"
echo "    Paste the above JSON into your AI client's MCP configuration (Claude Desktop, Claude Code, Antigravity, Cursor, etc.)."

# --- Step 7: Verify extension on running instance --------------------------
step "Verifying Tinta extension reachability"

if pgrep -x inkscape >/dev/null 2>&1; then
    LIST_OUTPUT="$(gdbus call --session --dest org.inkscape.Inkscape --object-path /org/inkscape/Inkscape --method org.gtk.Actions.List 2>/dev/null || true)"
    if echo "$LIST_OUTPUT" | grep -q "org\.tinta\.mcp"; then
        ok "Tinta extension detected on the running Inkscape instance and is ready to use."
    else
        warn "Extension action org.tinta.mcp not detected on the running Inkscape instance."
        warn "Restart Inkscape to register the new action."
    fi
else
    echo "    Inkscape is not currently running. Start Inkscape with a document open to use Tinta."
fi

echo -e "\n\033[1;32mSetup complete.\033[0m\n"
