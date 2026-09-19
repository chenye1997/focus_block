#!/usr/bin/env bash
# ==============================================================================
# FocusBlock Emergency Unlock Script
# ==============================================================================
# Instantly overrides anti-bypass locks and restores all websites and applications.
# Usage: sudo ./emergency_unlock.sh
# ==============================================================================

set -e

if [ "$EUID" -ne 0 ]; then
    echo "[!] Emergency unlock requires root privileges."
    echo "    Please run: sudo $0"
    exit 1
fi

echo "=================================================="
echo "🚨 FocusBlock 紧急恢复程序 (Emergency Unlock)"
echo "=================================================="

# 1. 恢复 /etc/hosts
if [ -f /etc/hosts ]; then
    echo "[1/4] 正在清理 /etc/hosts 拦截规则..."
    sed -i '/# --- FOCUS_MODE_START ---/,/# --- FOCUS_MODE_END ---/d' /etc/hosts
    # 额外清理单标记残留（如有）
    sed -i '/# --- FOCUS_MODE ---/d' /etc/hosts
    echo "      ✓ /etc/hosts 已恢复干净状态。"
fi

# 2. 刷新 DNS 缓存
echo "[2/4] 正在刷新系统 DNS 缓存..."
if command -v resolvectl >/dev/null 2>&1; then
    resolvectl flush-caches 2>/dev/null || true
fi
if systemctl is-active --quiet systemd-resolved 2>/dev/null; then
    systemctl restart systemd-resolved 2>/dev/null || true
    echo "      ✓ systemd-resolved 已重启并清除缓存。"
else
    echo "      ✓ DNS 缓存刷新指令已发送。"
fi

# 3. 恢复所有应用的可执行权限 (chmod +x)
echo "[3/4] 正在恢复被锁定的应用程序执行权限 (chmod +x)..."

# 尝试从 /etc/.focus_lock 读取已锁定的具体文件列表
RESTORED_COUNT=0
if [ -f /etc/.focus_lock ]; then
    # 使用 Python 提取 lock 中的所有二进制路径
    LOCKED_FILES=$(python3 -c "
import json
try:
    with open('/etc/.focus_lock') as f:
        d = json.load(f)
        files = set(d.get('apps', []))
        files.update(d.get('all_modified_binaries', []))
        print(' '.join(f for f in files if f))
except Exception:
    pass
" 2>/dev/null || true)

    for f in $LOCKED_FILES; do
        if [ -e "$f" ]; then
            chmod +x "$f" 2>/dev/null || true
            echo "      ✓ 已恢复权限: $f"
            RESTORED_COUNT=$((RESTORED_COUNT + 1))
        fi
    done
fi

# 兜底恢复常见预设应用路径与底层二进制
DEFAULT_TARGETS=(
    "/usr/bin/steam"
    "/usr/lib/steam/steam"
    "/usr/lib/steam/bin_steam.sh"
    "/usr/bin/discord"
    "/usr/bin/spotify"
    "/opt/spotify/spotify"
    "/usr/bin/telegram-desktop"
    "/usr/bin/google-chrome"
    "/usr/bin/chromium"
)

for t in "${DEFAULT_TARGETS[@]}"; do
    if [ -e "$t" ]; then
        chmod +x "$t" 2>/dev/null || true
        RESTORED_COUNT=$((RESTORED_COUNT + 1))
    fi
done
echo "      ✓ 共检查并恢复了 $RESTORED_COUNT 个应用相关文件权限。"

# 4. 删除根防绕过锁文件
echo "[4/4] 正在解除防绕过时间锁..."
if [ -f /etc/.focus_lock ]; then
    rm -f /etc/.focus_lock
    echo "      ✓ /etc/.focus_lock 已删除。"
fi

echo "=================================================="
echo "🎉 恢复完成！所有受限网站和桌面应用均已完全恢复正常！"
echo "=================================================="
