#!/usr/bin/env bash
# uninstall.sh — reverse the possess install cleanly.
set -u

INSTALL_DIR="$HOME/.local/share/possess"
BIN_DIR="$HOME/.local/bin"
RC_FILES=("$HOME/.bashrc" "$HOME/.zshrc")

echo "=================================================="
echo "  possess — uninstaller"
echo "=================================================="
echo

# Stop the daemon if running.
if [ -x "$INSTALL_DIR/possess" ]; then
    "$INSTALL_DIR/possess" off >/dev/null 2>&1 || true
fi

# Remove source lines from rc files.
for rc in "${RC_FILES[@]}"; do
    if [ -f "$rc" ] && grep -q 'local/share/possess/possess.sh' "$rc" 2>/dev/null; then
        printf "Remove possess source line from %s? [y/N] " "$rc"
        read -r ans || true
        case "$ans" in
            y|Y|yes|YES)
                # Delete the possess block (comment + source line + blank).
                tmp="$(mktemp)"
                awk '
                    /^# possess — haunted terminal$/ { skip=1; next }
                    skip && /^\[ -f "\$HOME\/.local\/share\/possess\/possess.sh" \]/ { skip=0; next }
                    skip && /^$/ { skip=0; next }
                    { print }
                ' "$rc" > "$tmp" && mv "$tmp" "$rc"
                echo "[ok] removed possess source line from $rc"
                ;;
            *)
                echo "[skip] $rc left unchanged"
                ;;
        esac
    fi
done

# Remove installed files.
if [ -d "$INSTALL_DIR" ]; then
    read -p "Delete $INSTALL_DIR (daemon, CLI, shell hook, state)? [y/N] " ans
    case "$ans" in
        y|Y|yes|YES)
            rm -rf "$INSTALL_DIR"
            echo "[ok] deleted $INSTALL_DIR"
            ;;
        *)
            echo "[skip] $INSTALL_DIR left in place"
            ;;
    esac
fi

if [ -L "$BIN_DIR/possess" ]; then
    read -p "Remove PATH shim $BIN_DIR/possess? [y/N] " ans
    case "$ans" in
        y|Y|yes|YES)
            rm -f "$BIN_DIR/possess"
            echo "[ok] removed $BIN_DIR/possess"
            ;;
        *)
            echo "[skip] $BIN_DIR/possess left in place"
            ;;
    esac
fi

echo
echo "Uninstall complete. Your command history was never logged to disk by possess."
echo "Any shell rc edits you chose to keep are harmless (the sourced file is gone)."
