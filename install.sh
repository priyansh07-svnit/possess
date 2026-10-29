#!/usr/bin/env bash
# install.sh — install possess, the haunted terminal.
#
# Copies files to ~/.local/share/possess, adds a source line to your .bashrc
# and/or .zshrc (asking before editing), checks for Python 3 and Ollama, and
# tells you which model to pull if it is missing.
#
# Runs on Linux/macOS (and Windows Git Bash). Review before running.

set -u

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$HOME/.local/share/possess"
BIN_DIR="$HOME/.local/bin"
CONFIG_DIR="$HOME/.config/possess"
RC_FILES=("$HOME/.bashrc" "$HOME/.zshrc")
SOURCE_LINE='[ -f "$HOME/.local/share/possess/possess.sh" ] && . "$HOME/.local/share/possess/possess.sh"'

MODEL="${POSSESS_MODEL:-llama3.2:3b}"
ENDPOINT="${POSSESS_ENDPOINT:-http://localhost:11434/v1}"

have() { command -v "$1" >/dev/null 2>&1; }

echo "=================================================="
echo "  possess — haunted terminal (installer)"
echo "=================================================="
echo

# --- Checks -----------------------------------------------------------------
if ! have python3; then
    echo "Error: python3 is required but was not found in PATH." >&2
    echo "       Install Python 3 and re-run." >&2
    exit 1
fi
echo "[ok] python3: $(python3 --version 2>&1)"

if have ollama; then
    echo "[ok] ollama: installed"
    if ! ollama list 2>/dev/null | grep -q "$MODEL"; then
        echo "[note] model '$MODEL' not pulled yet. After install, run:"
        echo "       ollama pull $MODEL"
    else
        echo "[ok] model '$MODEL' is available"
    fi
else
    echo "[warn] ollama not found in PATH. possess will still work using its"
    echo "       built-in offline fallback whispers (no model required)."
fi

echo

# --- Copy files -------------------------------------------------------------
mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$CONFIG_DIR"
cp "$REPO_DIR/possess_daemon.py" "$INSTALL_DIR/"
cp "$REPO_DIR/possess"           "$INSTALL_DIR/"
cp "$REPO_DIR/possess.sh"        "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/possess_daemon.py" "$INSTALL_DIR/possess"

# Convenience shim on PATH.
if [ ! -e "$BIN_DIR/possess" ]; then
    ln -sf "$INSTALL_DIR/possess" "$BIN_DIR/possess"
    echo "[ok] linked $BIN_DIR/possess"
else
    echo "[ok] $BIN_DIR/possess already exists"
fi

# Example config.
if [ ! -f "$CONFIG_DIR/config.toml" ]; then
    cat > "$CONFIG_DIR/config.toml" <<EOF
# possess configuration (also overridable via environment variables)
model = "$MODEL"
endpoint = "$ENDPOINT"
haunt = 2            # 1 = subtle, 2 = creepy, 3 = full poltergeist
lang = "en"          # "en" or "es"
socket = "$HOME/.local/share/possess/possess.sock"
EOF
    echo "[ok] wrote example config to $CONFIG_DIR/config.toml"
fi

echo
echo "Files installed to $INSTALL_DIR"

# --- Shell rc integration (ask before editing) ------------------------------
echo
for rc in "${RC_FILES[@]}"; do
    if [ ! -f "$rc" ]; then
        continue
    fi
    if grep -q 'local/share/possess/possess.sh' "$rc" 2>/dev/null; then
        echo "[skip] $rc already sources possess.sh"
        continue
    fi
    printf "possess would add a source line to %s. Edit now? [y/N] " "$rc"
    read -r ans || true
    case "$ans" in
        y|Y|yes|YES)
            {
                echo ""
                echo "# possess — haunted terminal"
                echo "$SOURCE_LINE"
            } >> "$rc"
            echo "[ok] added source line to $rc"
            ;;
        *)
            echo "[skip] not editing $rc"
            ;;
    esac
done

echo
echo "Installation complete."
echo
echo "Next steps:"
echo "  1. Restart your shell, or source the file manually:"
echo "       . $HOME/.local/share/possess/possess.sh"
echo "  2. Start possess:  possess on"
echo "  3. If the model is missing:  ollama pull $MODEL"
echo "  4. Run a command and wait for the ghost to whisper."
echo "  5. Try:  possess ofrenda        (or  possess ofrenda --lang es)"
echo
echo "Uninstall with:  $REPO_DIR/uninstall.sh"
