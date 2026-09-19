#!/usr/bin/env bash
# ==============================================================================
# FocusBlock Elevation Launcher
# ==============================================================================
# Automatically prepares X11/Xwayland authentication cookies so root can
# connect to the graphical user desktop without authorization errors.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="${SCRIPT_DIR}/focus_block.py"

# If already running as root, execute directly
if [ "$EUID" -eq 0 ]; then
    exec /usr/bin/python3 "${PYTHON_SCRIPT}" "$@"
fi

# Detect display environment
DISP="${DISPLAY:-:0}"
WAYLAND="${WAYLAND_DISPLAY:-}"

# Prepare XAUTHORITY so root can connect to the user's Xwayland/X11 server
USER_XAUTH="${XAUTHORITY:-${HOME}/.Xauthority}"

# If ~/.Xauthority doesn't exist or doesn't have a cookie for this display, generate one
if command -v xauth >/dev/null 2>&1 && [ -n "$DISP" ]; then
    if [ ! -s "$USER_XAUTH" ] || ! xauth -f "$USER_XAUTH" list "$DISP" 2>/dev/null | grep -q "$DISP"; then
        xauth -f "$USER_XAUTH" generate "$DISP" . trusted >/dev/null 2>&1 || true
    fi
fi

# If xhost is available, authorize local root user
if command -v xhost >/dev/null 2>&1; then
    xhost +si:localuser:root >/dev/null 2>&1 || true
fi

# Export cookie to a root-readable temporary file to avoid permission issues
ROOT_XAUTH="/tmp/.focus_block_xauth_$(id -u)"
if [ -f "$USER_XAUTH" ]; then
    cp "$USER_XAUTH" "$ROOT_XAUTH" 2>/dev/null || true
    chmod 644 "$ROOT_XAUTH" 2>/dev/null || true
fi

# Try PolicyKit (pkexec)
if command -v pkexec >/dev/null 2>&1; then
    echo "[FocusBlock] Requesting root authorization via PolicyKit (pkexec)..."
    if pkexec env \
        DISPLAY="${DISP}" \
        XAUTHORITY="${ROOT_XAUTH:-$USER_XAUTH}" \
        WAYLAND_DISPLAY="${WAYLAND}" \
        XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}" \
        /usr/bin/python3 "${PYTHON_SCRIPT}" "$@"; then
        rm -f "$ROOT_XAUTH" 2>/dev/null || true
        exit 0
    fi
    echo "[FocusBlock] pkexec cancelled or failed. Falling back to sudo -E..."
fi

# Fallback to sudo -E
echo "[FocusBlock] Elevating with sudo -E..."
sudo -E env \
    DISPLAY="${DISP}" \
    XAUTHORITY="${ROOT_XAUTH:-$USER_XAUTH}" \
    WAYLAND_DISPLAY="${WAYLAND}" \
    /usr/bin/python3 "${PYTHON_SCRIPT}" "$@"

rm -f "$ROOT_XAUTH" 2>/dev/null || true
