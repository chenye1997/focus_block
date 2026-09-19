#!/usr/bin/env bash
# ==============================================================================
# FocusBlock Elevation Launcher
# ==============================================================================
# Seamlessly handles X11/Xwayland display authorization so root can render
# the GUI without "Invalid MIT-MAGIC-COOKIE-1" or "cannot connect to display" errors.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BINARY="${SCRIPT_DIR}/dist/focus-block"
PYTHON_SCRIPT="${SCRIPT_DIR}/focus_block.py"

# Select execution target: prefer compiled binary if present
if [ -x "${BINARY}" ]; then
    CMD=("${BINARY}")
else
    CMD=(/usr/bin/python3 "${PYTHON_SCRIPT}")
fi

# If already running as root, execute directly
if [ "$EUID" -eq 0 ]; then
    exec "${CMD[@]}" "$@"
fi

# Detect display environment
DISP="${DISPLAY:-:0}"
WAYLAND="${WAYLAND_DISPLAY:-}"

# 1. Clean up any invalid or broken cookies created by previous runs
if [ -f "${HOME}/.Xauthority" ]; then
    # If the X server does not require auth, a bogus .Xauthority will cause "Invalid MIT-MAGIC-COOKIE-1 key"
    # Verify if current cookie is rejected
    if DISPLAY="${DISP}" XAUTHORITY="${HOME}/.Xauthority" python3 -c "import tkinter; r=tkinter.Tk(); r.destroy()" >/dev/null 2>&1; then
        VALID_XAUTH="${HOME}/.Xauthority"
    else
        echo "[FocusBlock] Notice: Removing invalid Xauthority cookie to prevent display rejection..."
        rm -f "${HOME}/.Xauthority"
        VALID_XAUTH=""
    fi
fi
rm -f /tmp/.focus_block_xauth_* 2>/dev/null || true

# 2. Authorize local root connections on X11/Xwayland
# Method A: xhost if installed
if command -v xhost >/dev/null 2>&1; then
    xhost +si:localuser:root >/dev/null 2>&1 || true
    xhost +local: >/dev/null 2>&1 || true
fi

# Method B: Native X11 protocol Opcode 111 (SetAccessControl: Disable)
# Works on all Linux distros without requiring xorg-xhost package!
python3 -c "
import socket, struct, os
disp = os.environ.get('DISPLAY', ':0')
sock_num = disp.split(':')[-1].split('.')[0]
sock_path = f'/tmp/.X11-unix/X{sock_num}'
if os.path.exists(sock_path):
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(sock_path)
        s.sendall(struct.pack('=cxHHHHH', b'l', 11, 0, 0, 0, 0))
        header = s.recv(8)
        if header and len(header) == 8:
            status, _, _, _, add_len = struct.unpack('=BBHHH', header)
            if status == 1:
                rem = add_len * 4
                while rem > 0:
                    chunk = s.recv(min(rem, 4096))
                    if not chunk: break
                    rem -= len(chunk)
                # Opcode 111: SetAccessControl (mode=0 Disable, len=1)
                s.sendall(struct.pack('=BBH', 111, 0, 1))
        s.close()
    except Exception:
        pass
" 2>/dev/null || true

# 3. Launch via PolicyKit (pkexec)
if command -v pkexec >/dev/null 2>&1; then
    echo "[FocusBlock] Requesting root authorization via PolicyKit (pkexec)..."
    ENV_ARGS=(
        "DISPLAY=${DISP}"
        "WAYLAND_DISPLAY=${WAYLAND}"
        "XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    )
    if [ -n "${VALID_XAUTH}" ]; then
        ENV_ARGS+=("XAUTHORITY=${VALID_XAUTH}")
    else
        ENV_ARGS+=("XAUTHORITY=")
    fi

    if pkexec env "${ENV_ARGS[@]}" "${CMD[@]}" "$@"; then
        exit 0
    fi
    echo "[FocusBlock] pkexec cancelled or failed. Falling back to sudo -E..."
fi

# 4. Fallback to sudo -E
echo "[FocusBlock] Elevating with sudo -E..."
sudo -E env \
    DISPLAY="${DISP}" \
    WAYLAND_DISPLAY="${WAYLAND}" \
    XAUTHORITY="${VALID_XAUTH:-}" \
    "${CMD[@]}" "$@"
