#!/usr/bin/env bash
# ==============================================================================
# FocusBlock Binary Build Script
# ==============================================================================
# Builds a standalone ELF 64-bit Linux binary using PyInstaller.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

echo "=================================================="
echo "🔨 Building FocusBlock Standalone Linux Binary"
echo "=================================================="

# Check for pyinstaller
PYINSTALLER="pyinstaller"
if ! command -v pyinstaller >/dev/null 2>&1; then
    if [ -f "/tmp/test_build_venv/bin/pyinstaller" ]; then
        PYINSTALLER="/tmp/test_build_venv/bin/pyinstaller"
    else
        echo "[!] PyInstaller not found. Creating build venv..."
        python3 -m venv /tmp/focusblock_build_venv
        /tmp/focusblock_build_venv/bin/pip install --upgrade pyinstaller
        PYINSTALLER="/tmp/focusblock_build_venv/bin/pyinstaller"
    fi
fi

echo "[1/2] Compiling with PyInstaller..."
"${PYINSTALLER}" \
    --noconfirm \
    --onefile \
    --windowed \
    --name focus-block \
    --icon assets/focus-block.png \
    focus_block.py

echo "[2/2] Stripping binary symbols for performance..."
strip "${SCRIPT_DIR}/dist/focus-block" 2>/dev/null || true

echo "=================================================="
echo "🎉 Build succeeded!"
echo "   Binary location: ${SCRIPT_DIR}/dist/focus-block"
echo "   Run installer:   ./install.sh"
echo "=================================================="
