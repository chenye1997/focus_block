#!/usr/bin/env bash
# ==============================================================================
# FocusBlock Desktop & Binary Installer
# ==============================================================================
# Installs binary, desktop shortcut, icons, and menu integration.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=================================================="
echo "🛡️ Installing FocusBlock into your Linux Desktop"
echo "=================================================="

# 1. Install Icons
echo "[1/4] Installing system icons..."
ICON_DIR_SVG="${HOME}/.local/share/icons/hicolor/scalable/apps"
ICON_DIR_PNG="${HOME}/.local/share/icons/hicolor/256x256/apps"
mkdir -p "${ICON_DIR_SVG}" "${ICON_DIR_PNG}"

cp "${SCRIPT_DIR}/assets/focus-block.svg" "${ICON_DIR_SVG}/focus-block.svg"
cp "${SCRIPT_DIR}/assets/focus-block.png" "${ICON_DIR_PNG}/focus-block.png"
echo "      ✓ Icons installed to ~/.local/share/icons"

# 2. Install CLI executable symlink into ~/.local/bin
echo "[2/4] Installing executable binary..."
mkdir -p "${HOME}/.local/bin"
if [ -f "${SCRIPT_DIR}/dist/focus-block" ]; then
    ln -sf "${SCRIPT_DIR}/dist/focus-block" "${HOME}/.local/bin/focus-block"
    echo "      ✓ Binary symlinked: ~/.local/bin/focus-block -> dist/focus-block"
else
    ln -sf "${SCRIPT_DIR}/run.sh" "${HOME}/.local/bin/focus-block"
    echo "      ✓ Launcher symlinked: ~/.local/bin/focus-block -> run.sh"
fi

# 3. Install Application Menu Shortcut
echo "[3/4] Installing desktop entry into application menu..."
APPS_DIR="${HOME}/.local/share/applications"
mkdir -p "${APPS_DIR}"
cp "${SCRIPT_DIR}/focus-block.desktop" "${APPS_DIR}/focus-block.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "${APPS_DIR}" 2>/dev/null || true
fi
echo "      ✓ Application menu entry created: ~/.local/share/applications/focus-block.desktop"

# 4. Install Desktop Shortcut (if ~/Desktop exists)
if [ -d "${HOME}/Desktop" ]; then
    echo "[4/4] Placing shortcut onto your Desktop..."
    DESKTOP_FILE="${HOME}/Desktop/focus-block.desktop"
    cp "${SCRIPT_DIR}/focus-block.desktop" "${DESKTOP_FILE}"
    chmod +x "${DESKTOP_FILE}"
    # GNOME/KDE trust attribute
    if command -v gio >/dev/null 2>&1; then
        gio set "${DESKTOP_FILE}" metadata::trusted true 2>/dev/null || true
    fi
    echo "      ✓ Desktop shortcut created: ~/Desktop/focus-block.desktop"
fi

echo "=================================================="
echo "🎉 Installation complete!"
echo "   You can now launch FocusBlock:"
echo "   1. Directly from your desktop or application menu"
echo "   2. From terminal: focus-block"
echo "=================================================="
