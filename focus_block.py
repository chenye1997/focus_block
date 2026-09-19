#!/usr/bin/env python3
"""
FocusBlock - Linux Desktop Distraction Blocker
==============================================
A robust, anti-bypass focus tool for Linux desktop environments that redirects
distracting websites via /etc/hosts, flushes systemd-resolved DNS, terminates
distracting applications using killall -9, and revokes binary execute permissions
via chmod -x for a specified focus duration.

Author: Senior Linux System Engineer
License: MIT
"""

import os
import sys
import time
import json
import stat
import shutil
import tempfile
import datetime
import argparse
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from typing import List, Tuple, Optional, Dict, Any, Set

# ==============================================================================
# Configuration & Constants
# ==============================================================================

DEFAULT_HOSTS_PATH = "/etc/hosts"
DEFAULT_LOCK_PATH = "/etc/.focus_lock"
DEFAULT_CONFIG_PATH = os.path.expanduser("~/.config/focus_block/config.json")

FOCUS_MARKER_START = "# --- FOCUS_MODE_START ---"
FOCUS_MARKER_END = "# --- FOCUS_MODE_END ---"

# Firefox Canary domain: if blocked, Firefox disables DoH (DNS-over-HTTPS) and respects /etc/hosts
DOH_CANARY_DOMAIN = "use-application-dns.net"

DEFAULT_DOMAINS = [
    "youtube.com",
    "reddit.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "twitch.tv",
    "netflix.com",
    "discord.com",
    "bilibili.com",
    "weibo.com",
    "zhihu.com",
    "news.ycombinator.com",
]

DEFAULT_APPS = [
    "/usr/bin/steam",
    "/usr/bin/discord",
    "/usr/bin/telegram-desktop",
    "/usr/bin/spotify",
]

# ==============================================================================
# Linux System Manager
# ==============================================================================

class SystemManager:
    """
    Manages low-level Linux system operations including /etc/hosts modification,
    DNS cache flushing via systemd-resolved, process termination, and permission management.
    """

    def __init__(self, hosts_path: str = DEFAULT_HOSTS_PATH):
        self.hosts_path = hosts_path

    def inject_hosts(self, domains: List[str]) -> Tuple[bool, str]:
        """
        Injects domain redirection rules (127.0.0.1 and ::1) into the hosts file
        wrapped between specific marker comments.
        Uses atomic file replacement with direct fallback for bind-mounted files.
        """
        if not domains:
            return True, "No domains specified to block."

        try:
            original_content = ""
            if os.path.exists(self.hosts_path):
                with open(self.hosts_path, "r", encoding="utf-8") as f:
                    original_content = f.read()

            # Clean any existing focus block first to avoid duplicate blocks
            clean_lines = self._filter_out_marker_block(original_content.splitlines())

            # Prepare new block entries
            block_lines = [
                FOCUS_MARKER_START,
                f"# FocusBlock active session - started at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                f"# Prevents DNS-over-HTTPS bypass in browsers",
                f"127.0.0.1\t{DOH_CANARY_DOMAIN}",
                f"::1\t\t{DOH_CANARY_DOMAIN}",
            ]

            unique_domains: Set[str] = set()
            for raw_dom in domains:
                dom_pair = self.normalize_domain_and_subdomain(raw_dom)
                for d in dom_pair:
                    if d:
                        unique_domains.add(d)

            for dom in sorted(unique_domains):
                block_lines.append(f"127.0.0.1\t{dom}")
                block_lines.append(f"::1\t\t{dom}")

            block_lines.append(FOCUS_MARKER_END)

            # Assemble updated content
            final_content = "\n".join(clean_lines).rstrip() + "\n\n" + "\n".join(block_lines) + "\n"

            # Write atomically with fallback
            self._write_file_safe(self.hosts_path, final_content, mode=0o644)
            return True, f"Successfully injected {len(unique_domains)} blocked domains into {self.hosts_path}."

        except Exception as e:
            return False, f"Failed to modify {self.hosts_path}: {str(e)}"

    def cleanup_hosts(self) -> Tuple[bool, str]:
        """
        Removes the FocusBlock marker section from the hosts file safely.
        """
        try:
            if not os.path.exists(self.hosts_path):
                return True, f"{self.hosts_path} does not exist, nothing to clean."

            with open(self.hosts_path, "r", encoding="utf-8") as f:
                content = f.read()

            if FOCUS_MARKER_START not in content and "# --- FOCUS_MODE ---" not in content:
                return True, "No FocusBlock rules found in hosts file."

            clean_lines = self._filter_out_marker_block(content.splitlines())
            final_content = "\n".join(clean_lines).rstrip() + "\n"

            self._write_file_safe(self.hosts_path, final_content, mode=0o644)
            return True, f"Cleaned up FocusBlock rules from {self.hosts_path}."

        except Exception as e:
            return False, f"Failed to clean up {self.hosts_path}: {str(e)}"

    def flush_dns(self) -> Tuple[bool, str]:
        """
        Flushes the DNS cache via 'systemctl restart systemd-resolved' as specified.
        Also invokes 'resolvectl flush-caches' if available.
        """
        messages = []

        # 1. Official systemd command to flush caches immediately
        if shutil.which("resolvectl"):
            try:
                subprocess.run(["resolvectl", "flush-caches"], capture_output=True, text=True, check=False)
                messages.append("resolvectl cache flushed")
            except Exception:
                pass

        # 2. Restart systemd-resolved service as requested
        try:
            cmd = ["systemctl", "restart", "systemd-resolved"]
            proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if proc.returncode == 0:
                messages.append("systemd-resolved restarted")
                return True, f"DNS cache flushed ({', '.join(messages)})."
            else:
                err_msg = proc.stderr.strip() or proc.stdout.strip() or f"Exit code {proc.returncode}"
                return False, f"systemd-resolved restart failed: {err_msg}"
        except FileNotFoundError:
            # Fallback if systemctl is not present or non-systemd environment
            return True, "Notice: systemctl not available, relying on hosts file direct resolution."
        except Exception as e:
            return False, f"Error flushing DNS cache: {str(e)}"

    def resolve_all_app_targets(self, app_entry: str) -> List[str]:
        """
        Resolves an application entry (name or path) to all concrete file paths,
        including resolving wrapper shell scripts that 'exec' other binaries.
        """
        target = app_entry.strip()
        if not target:
            return []

        # If only a binary name was provided, find in PATH
        if not os.path.isabs(target) and not os.path.exists(target):
            which_path = shutil.which(target)
            if which_path:
                target = which_path
            else:
                return []

        results = set()
        real_path = os.path.realpath(target)
        if os.path.exists(real_path):
            results.add(real_path)

        # Inspect if target is a wrapper script that execs an underlying binary
        # e.g., /usr/bin/steam -> /usr/lib/steam/steam or /usr/bin/spotify -> /opt/spotify/spotify
        if os.path.isfile(real_path):
            try:
                with open(real_path, "r", encoding="utf-8", errors="ignore") as f:
                    # Read first 4KB of script
                    head = f.read(4096)
                    if head.startswith("#!"):
                        for line in head.splitlines():
                            line_s = line.strip()
                            if line_s.startswith("exec ") or "exec /" in line_s:
                                parts = line_s.split()
                                for p in parts:
                                    clean_p = p.strip('"\'')
                                    if clean_p.startswith("/") and os.path.exists(clean_p):
                                        results.add(os.path.realpath(clean_p))
            except Exception:
                pass

        return list(results)

    def block_application(self, app_path: str) -> Tuple[bool, str, List[str]]:
        """
        Terminates running instances using 'killall -9' and 'pkill -9',
        and revokes executable permissions using 'chmod -x'.
        Returns (success, message, list_of_modified_binary_paths).
        """
        all_targets = self.resolve_all_app_targets(app_path)
        base_name = os.path.basename(app_path.strip())

        # Collect process names to terminate (case-insensitive variations)
        proc_names = {base_name, base_name.lower(), base_name.capitalize()}
        for t in all_targets:
            t_base = os.path.basename(t)
            proc_names.add(t_base)
            proc_names.add(t_base.lower())
            proc_names.add(t_base.capitalize())

        # Specific known helpers (e.g. steamwebhelper for steam)
        if "steam" in base_name.lower():
            proc_names.update(["steam", "steamwebhelper"])
        if "discord" in base_name.lower():
            proc_names.update(["Discord", "discord"])
        if "spotify" in base_name.lower():
            proc_names.update(["spotify", "Spotify"])
        if "telegram" in base_name.lower():
            proc_names.update(["telegram-desktop", "Telegram"])

        # 1. Terminate running instances via killall -9 and pkill -9
        for p in proc_names:
            try:
                subprocess.run(["killall", "-9", "-I", p], capture_output=True, text=True, check=False)
                subprocess.run(["pkill", "-9", "-i", "-f", p], capture_output=True, text=True, check=False)
            except Exception:
                pass

        if not all_targets:
            return False, f"Executable not found on system: {app_path}", []

        # 2. Revoke execute permissions via chmod -x on all resolved binaries
        modified_paths = []
        messages = [f"Terminated '{base_name}' instances via killall -9"]

        for t in all_targets:
            try:
                current_mode = os.stat(t).st_mode
                new_mode = current_mode & ~0o111  # Remove executable bits (user, group, other)
                os.chmod(t, new_mode)
                subprocess.run(["chmod", "-x", t], check=False)
                modified_paths.append(t)
                messages.append(f"chmod -x on '{t}'")
            except Exception as e:
                messages.append(f"chmod -x failed on '{t}': {e}")

        return True, " | ".join(messages), modified_paths

    def unblock_application(self, app_path: str) -> Tuple[bool, str]:
        """
        Restores executable permissions using 'chmod +x'.
        """
        all_targets = self.resolve_all_app_targets(app_path)
        if not all_targets:
            # Try raw path directly
            if os.path.exists(app_path):
                all_targets = [app_path]
            else:
                return False, f"File not found: {app_path}"

        messages = []
        for t in all_targets:
            try:
                current_mode = os.stat(t).st_mode
                new_mode = current_mode | 0o111 | 0o755
                os.chmod(t, new_mode)
                subprocess.run(["chmod", "+x", t], check=False)
                messages.append(f"chmod +x '{t}'")
            except Exception as e:
                messages.append(f"failed chmod +x '{t}': {e}")

        return True, ", ".join(messages)

    @staticmethod
    def normalize_domain_and_subdomain(raw_domain: str) -> Tuple[str, str]:
        """
        Sanitizes a domain and returns both the root domain and www. subdomain.
        """
        d = raw_domain.strip().lower()
        if d.startswith("http://"):
            d = d[7:]
        elif d.startswith("https://"):
            d = d[8:]
        d = d.split("/")[0].split("?")[0].split(":")[0].strip()
        if not d:
            return "", ""

        if d.startswith("www."):
            root_dom = d[4:]
            www_dom = d
        else:
            root_dom = d
            www_dom = f"www.{d}"

        return root_dom, www_dom

    def _filter_out_marker_block(self, lines: List[str]) -> List[str]:
        """
        Filters out any lines enclosed between FocusBlock marker comments.
        """
        result = []
        in_block = False
        for line in lines:
            stripped = line.strip()
            if stripped == FOCUS_MARKER_START or stripped == "# --- FOCUS_MODE ---":
                in_block = True
                continue
            if stripped == FOCUS_MARKER_END or (in_block and stripped == "# --- FOCUS_MODE ---"):
                in_block = False
                continue
            if not in_block:
                result.append(line)
        return result

    def _write_file_safe(self, target_path: str, content: str, mode: int = 0o644) -> None:
        """
        Writes content safely to target_path. Uses tempfile replacement with
        fallback to direct truncate-and-write (vital for bind-mounted /etc/hosts in containers).
        """
        dir_name = os.path.dirname(os.path.abspath(target_path)) or "/tmp"
        temp_path = None
        try:
            temp_fd, temp_path = tempfile.mkstemp(prefix=".focus_tmp_", dir=dir_name)
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())

            os.chmod(temp_path, mode)
            os.replace(temp_path, target_path)
            temp_path = None
        except OSError:
            # Fallback for EBUSY/EXDEV on bind-mounted files
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass


# ==============================================================================
# Anti-Bypass Time-Lock Manager
# ==============================================================================

class FocusLockManager:
    """
    Manages the persistent root-owned lock file (/etc/.focus_lock) to enforce
    the anti-bypass guarantee.
    """

    def __init__(self, lock_path: str = DEFAULT_LOCK_PATH):
        self.lock_path = lock_path

    def is_locked(self) -> bool:
        """
        Returns True if a lock file exists on disk.
        """
        return os.path.exists(self.lock_path)

    def load_lock_data(self) -> Optional[Dict[str, Any]]:
        """
        Reads and parses the lock file data.
        """
        if not self.is_locked():
            return None
        try:
            with open(self.lock_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def get_remaining_seconds(self) -> float:
        """
        Calculates remaining seconds until unlock timestamp.
        Returns 0.0 if not locked or expired.
        """
        data = self.load_lock_data()
        if not data or "unlock_timestamp" not in data:
            return 0.0
        now = time.time()
        remaining = data["unlock_timestamp"] - now
        return max(0.0, remaining)

    def create_lock(
        self,
        duration_minutes: float,
        domains: List[str],
        apps: List[str],
        all_modified_binaries: Optional[List[str]] = None,
    ) -> bool:
        """
        Creates the root-owned lock file containing the unlock timestamp and target lists.
        Sets strict permissions (0o600: read/write by root only).
        """
        now = time.time()
        unlock_timestamp = now + (duration_minutes * 60.0)

        data = {
            "version": 1,
            "pid": os.getpid(),
            "start_timestamp": now,
            "duration_minutes": duration_minutes,
            "unlock_timestamp": unlock_timestamp,
            "domains": domains,
            "apps": apps,
            "all_modified_binaries": all_modified_binaries or [],
            "created_at_iso": datetime.datetime.now().isoformat(),
            "unlock_at_iso": datetime.datetime.fromtimestamp(unlock_timestamp).isoformat(),
        }

        try:
            dir_name = os.path.dirname(os.path.abspath(self.lock_path)) or "/tmp"
            temp_fd, temp_path = tempfile.mkstemp(prefix=".focus_lock_", dir=dir_name)
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())

            os.chmod(temp_path, 0o600)
            try:
                os.replace(temp_path, self.lock_path)
            except OSError:
                with open(self.lock_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            return True
        except Exception as e:
            print(f"Error creating lock file: {e}", file=sys.stderr)
            return False

    def remove_lock(self) -> bool:
        """
        Deletes the lock file once session is legitimately expired and unlocked.
        """
        try:
            if os.path.exists(self.lock_path):
                os.remove(self.lock_path)
            return True
        except Exception as e:
            print(f"Error removing lock file: {e}", file=sys.stderr)
            return False


# ==============================================================================
# GUI Application (Tkinter)
# ==============================================================================

class FocusBlockGUI:
    """
    Modern, user-friendly desktop GUI interface for FocusBlock.
    """

    # Modern Dark Theme Palette
    BG_DARK = "#181825"
    SURFACE_DARK = "#1e1e2e"
    SURFACE_ALT = "#313244"
    TEXT_MAIN = "#cdd6f4"
    TEXT_MUTED = "#a6adc8"
    ACCENT_GREEN = "#a6e3a1"
    ACCENT_RED = "#f38ba8"
    ACCENT_BLUE = "#89b4fa"
    ACCENT_YELLOW = "#f9e2af"
    ACCENT_HOVER = "#b4befe"

    def __init__(
        self,
        root: tk.Tk,
        system_mgr: Optional[SystemManager] = None,
        lock_mgr: Optional[FocusLockManager] = None,
        is_simulation: bool = False,
    ):
        self.root = root
        self.system_mgr = system_mgr or SystemManager()
        self.lock_mgr = lock_mgr or FocusLockManager()
        self.is_simulation = is_simulation

        title_prefix = "[SIMULATION] " if self.is_simulation else ""
        self.root.title(f"{title_prefix}FocusBlock — Distraction Shield")
        self.root.geometry("780x800")
        self.root.minsize(680, 640)
        self.root.configure(bg=self.BG_DARK)

        # Handle window close attempt
        self.root.protocol("WM_DELETE_WINDOW", self.on_window_close)

        # State Variables
        self.is_active = False
        self.total_duration_secs = 0.0
        self.preset_buttons: List[tk.Button] = []

        self._configure_styles()
        self._build_ui()
        self._load_configuration()
        self._check_initial_lock_state()

        # Wire duration changes to update idle timer display live
        self.duration_var.trace_add("write", self._on_duration_changed)
        self._update_idle_timer_display()

        # Start periodic countdown loop
        self.root.after(1000, self._timer_tick)

    def _configure_styles(self) -> None:
        """
        Sets up modern ttk styles and custom visual widgets.
        """
        self.style = ttk.Style()
        try:
            self.style.theme_use("clam")
        except Exception:
            pass

        self.style.configure(".", background=self.BG_DARK, foreground=self.TEXT_MAIN)
        self.style.configure("TFrame", background=self.BG_DARK)
        self.style.configure("Card.TFrame", background=self.SURFACE_DARK, relief="flat")
        self.style.configure("InnerCard.TFrame", background=self.SURFACE_ALT, relief="flat")

        # Notebook (Tabs)
        self.style.configure("TNotebook", background=self.BG_DARK, borderwidth=0)
        self.style.configure(
            "TNotebook.Tab",
            background=self.SURFACE_DARK,
            foreground=self.TEXT_MUTED,
            padding=[16, 8],
            font=("Sans", 10, "bold"),
        )
        self.style.map(
            "TNotebook.Tab",
            background=[("selected", self.SURFACE_ALT), ("active", self.SURFACE_ALT)],
            foreground=[("selected", self.ACCENT_BLUE), ("active", self.TEXT_MAIN)],
        )

        # Progress bar
        self.style.configure(
            "Focus.Horizontal.TProgressbar",
            troughcolor=self.SURFACE_ALT,
            background=self.ACCENT_BLUE,
            thickness=8,
            borderwidth=0,
        )

    def _build_ui(self) -> None:
        """
        Constructs all GUI widgets with a clean, structured layout.
        """
        container = ttk.Frame(self.root, padding=18)
        container.pack(fill=tk.BOTH, expand=True)

        # ----------------- Header Section -----------------
        header_frame = ttk.Frame(container)
        header_frame.pack(fill=tk.X, pady=(0, 14))

        title_lbl = tk.Label(
            header_frame,
            text="🛡️ FocusBlock",
            font=("Sans", 20, "bold"),
            bg=self.BG_DARK,
            fg=self.ACCENT_BLUE,
        )
        title_lbl.pack(side=tk.LEFT)

        subtitle_lbl = tk.Label(
            header_frame,
            text="Kernel & Network Level Distraction Control",
            font=("Sans", 10),
            bg=self.BG_DARK,
            fg=self.TEXT_MUTED,
        )
        subtitle_lbl.pack(side=tk.LEFT, padx=(12, 0), pady=(6, 0))

        badge_text = "SIMULATION" if self.is_simulation else "ROOT ACTIVE"
        badge_bg = "#3b3020" if self.is_simulation else "#2e4034"
        badge_fg = self.ACCENT_YELLOW if self.is_simulation else self.ACCENT_GREEN

        root_badge = tk.Label(
            header_frame,
            text=badge_text,
            font=("Sans", 8, "bold"),
            bg=badge_bg,
            fg=badge_fg,
            padx=8,
            pady=3,
        )
        root_badge.pack(side=tk.RIGHT)

        # ----------------- Status Card & Timer -----------------
        status_card = tk.Frame(
            container,
            bg=self.SURFACE_DARK,
            padx=20,
            pady=16,
            highlightthickness=1,
            highlightbackground=self.SURFACE_ALT,
        )
        status_card.pack(fill=tk.X, pady=(0, 16))

        # Status Badge
        self.status_badge = tk.Label(
            status_card,
            text="● IDLE — Ready to Focus",
            font=("Sans", 11, "bold"),
            bg=self.SURFACE_DARK,
            fg=self.ACCENT_GREEN,
        )
        self.status_badge.pack(anchor="w")

        # Timer Display
        timer_frame = tk.Frame(status_card, bg=self.SURFACE_DARK)
        timer_frame.pack(fill=tk.X, pady=(10, 6))

        self.timer_label = tk.Label(
            timer_frame,
            text="25:00",
            font=("JetBrainsMono Nerd Font", 42, "bold"),
            bg=self.SURFACE_DARK,
            fg=self.TEXT_MAIN,
        )
        self.timer_label.pack(side=tk.LEFT)

        self.time_detail_label = tk.Label(
            timer_frame,
            text="Set duration below and click 'Start Focus'",
            font=("Sans", 10),
            bg=self.SURFACE_DARK,
            fg=self.TEXT_MUTED,
            justify=tk.LEFT,
        )
        self.time_detail_label.pack(side=tk.LEFT, padx=(20, 0), pady=(12, 0))

        # Progress Bar
        self.progressbar = ttk.Progressbar(
            status_card,
            style="Focus.Horizontal.TProgressbar",
            orient="horizontal",
            mode="determinate",
            maximum=100.0,
            value=0.0,
        )
        self.progressbar.pack(fill=tk.X, pady=(10, 4))

        # ----------------- Control Actions Bar -----------------
        ctrl_card = tk.Frame(
            container,
            bg=self.SURFACE_DARK,
            padx=16,
            pady=14,
            highlightthickness=1,
            highlightbackground=self.SURFACE_ALT,
        )
        ctrl_card.pack(fill=tk.X, pady=(0, 16))

        # Duration Entry and Presets
        top_ctrl = tk.Frame(ctrl_card, bg=self.SURFACE_DARK)
        top_ctrl.pack(fill=tk.X)

        dur_label = tk.Label(
            top_ctrl,
            text="Duration (minutes):",
            font=("Sans", 11, "bold"),
            bg=self.SURFACE_DARK,
            fg=self.TEXT_MAIN,
        )
        dur_label.pack(side=tk.LEFT, padx=(0, 10))

        self.duration_var = tk.StringVar(value="25")
        self.duration_spin = tk.Spinbox(
            top_ctrl,
            from_=1,
            to=1440,
            textvariable=self.duration_var,
            width=6,
            font=("Sans", 12, "bold"),
            bg=self.SURFACE_ALT,
            fg=self.TEXT_MAIN,
            insertbackground=self.TEXT_MAIN,
            buttonbackground=self.SURFACE_ALT,
            relief="flat",
        )
        self.duration_spin.pack(side=tk.LEFT, padx=(0, 14))

        # Preset Buttons
        presets_frame = tk.Frame(top_ctrl, bg=self.SURFACE_DARK)
        presets_frame.pack(side=tk.LEFT)

        self.preset_buttons.clear()
        for p_min, p_label in [(15, "15m"), (25, "25m Pomodoro"), (45, "45m"), (60, "1h"), (120, "2h")]:
            btn = tk.Button(
                presets_frame,
                text=p_label,
                font=("Sans", 9),
                bg=self.SURFACE_ALT,
                fg=self.TEXT_MAIN,
                activebackground=self.ACCENT_HOVER,
                activeforeground=self.BG_DARK,
                relief="flat",
                padx=8,
                pady=2,
                cursor="hand2",
                command=lambda m=p_min: self._set_preset_duration(m),
            )
            btn.pack(side=tk.LEFT, padx=3)
            self.preset_buttons.append(btn)

        # Action Buttons (Start / Stop)
        btn_frame = tk.Frame(ctrl_card, bg=self.SURFACE_DARK)
        btn_frame.pack(fill=tk.X, pady=(14, 0))

        self.start_btn = tk.Button(
            btn_frame,
            text="🚀 Start Focus (Engage Lock)",
            font=("Sans", 11, "bold"),
            bg="#238636",
            fg="#ffffff",
            activebackground="#2ea043",
            activeforeground="#ffffff",
            relief="flat",
            padx=20,
            pady=9,
            cursor="hand2",
            command=self.start_focus,
        )
        self.start_btn.pack(side=tk.LEFT, padx=(0, 12))

        self.stop_btn = tk.Button(
            btn_frame,
            text="🛑 Stop Focus",
            font=("Sans", 11, "bold"),
            bg="#da3633",
            fg="#ffffff",
            activebackground="#f85149",
            activeforeground="#ffffff",
            relief="flat",
            padx=20,
            pady=9,
            cursor="hand2",
            command=self.stop_focus,
        )
        self.stop_btn.pack(side=tk.LEFT)

        # Anti-bypass notice
        lock_hint = tk.Label(
            btn_frame,
            text="🔒 Anti-bypass time lock strictly enforced",
            font=("Sans", 9, "italic"),
            bg=self.SURFACE_DARK,
            fg=self.ACCENT_YELLOW,
        )
        lock_hint.pack(side=tk.RIGHT, pady=8)

        # ----------------- Tabs (Domains, Apps, Logs) -----------------
        notebook = ttk.Notebook(container)
        notebook.pack(fill=tk.BOTH, expand=True)

        # Tab 1: Blocked Websites
        web_tab = tk.Frame(notebook, bg=self.SURFACE_DARK, padx=12, pady=12)
        notebook.add(web_tab, text="🌐 Websites to Block")

        web_desc = tk.Label(
            web_tab,
            text="Enter domains to redirect to 127.0.0.1 in /etc/hosts (one domain per line):",
            font=("Sans", 9),
            bg=self.SURFACE_DARK,
            fg=self.TEXT_MUTED,
            anchor="w",
        )
        web_desc.pack(fill=tk.X, pady=(0, 6))

        self.domains_text = tk.Text(
            web_tab,
            font=("JetBrainsMono Nerd Font", 10),
            bg=self.BG_DARK,
            fg=self.TEXT_MAIN,
            insertbackground=self.TEXT_MAIN,
            relief="flat",
            padx=10,
            pady=10,
            wrap=tk.NONE,
        )
        web_scroll = ttk.Scrollbar(web_tab, orient="vertical", command=self.domains_text.yview)
        self.domains_text.configure(yscrollcommand=web_scroll.set)
        web_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.domains_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Tab 2: Blocked Applications
        apps_tab = tk.Frame(notebook, bg=self.SURFACE_DARK, padx=12, pady=12)
        notebook.add(apps_tab, text="📱 Applications to Block")

        apps_desc_frame = tk.Frame(apps_tab, bg=self.SURFACE_DARK)
        apps_desc_frame.pack(fill=tk.X, pady=(0, 6))

        apps_desc = tk.Label(
            apps_desc_frame,
            text="Binary paths or commands to terminate (killall -9) & revoke permissions (chmod -x):",
            font=("Sans", 9),
            bg=self.SURFACE_DARK,
            fg=self.TEXT_MUTED,
        )
        apps_desc.pack(side=tk.LEFT)

        add_app_btn = tk.Button(
            apps_desc_frame,
            text="📂 Browse & Add Executable...",
            font=("Sans", 9),
            bg=self.SURFACE_ALT,
            fg=self.TEXT_MAIN,
            relief="flat",
            padx=8,
            pady=2,
            cursor="hand2",
            command=self._browse_executable,
        )
        add_app_btn.pack(side=tk.RIGHT)

        self.apps_text = tk.Text(
            apps_tab,
            font=("JetBrainsMono Nerd Font", 10),
            bg=self.BG_DARK,
            fg=self.TEXT_MAIN,
            insertbackground=self.TEXT_MAIN,
            relief="flat",
            padx=10,
            pady=10,
            wrap=tk.NONE,
        )
        apps_scroll = ttk.Scrollbar(apps_tab, orient="vertical", command=self.apps_text.yview)
        self.apps_text.configure(yscrollcommand=apps_scroll.set)
        apps_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.apps_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Tab 3: System Diagnostics & Logs
        log_tab = tk.Frame(notebook, bg=self.SURFACE_DARK, padx=12, pady=12)
        notebook.add(log_tab, text="📋 Activity Log")

        self.log_text = tk.Text(
            log_tab,
            font=("JetBrainsMono Nerd Font", 9),
            bg=self.BG_DARK,
            fg=self.TEXT_MUTED,
            relief="flat",
            padx=10,
            pady=10,
            wrap=tk.WORD,
            state=tk.DISABLED,
        )
        log_scroll = ttk.Scrollbar(log_tab, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._log_message(f"FocusBlock initialized ({'SIMULATION' if self.is_simulation else 'ROOT'}). Ready.")

    # --------------------------------------------------------------------------
    # UI Helpers & Logging
    # --------------------------------------------------------------------------

    def _log_message(self, msg: str) -> None:
        """
        Appends an event log entry with a timestamp.
        """
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {msg}\n"
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, entry)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _send_desktop_notification(self, title: str, body: str) -> None:
        """
        Sends a native Linux desktop notification via notify-send.
        """
        if shutil.which("notify-send"):
            try:
                subprocess.run(
                    ["notify-send", "-a", "FocusBlock", "-u", "normal", title, body],
                    check=False,
                    capture_output=True,
                )
            except Exception:
                pass

    def _on_duration_changed(self, *args) -> None:
        if not self.is_active:
            self._update_idle_timer_display()

    def _update_idle_timer_display(self) -> None:
        try:
            val = float(self.duration_var.get().strip())
            mins = int(val)
            secs = int((val - mins) * 60)
            self.timer_label.config(text=f"{mins:02d}:{secs:02d}", fg=self.TEXT_MAIN)
        except ValueError:
            self.timer_label.config(text="--:--", fg=self.TEXT_MUTED)

    def _set_preset_duration(self, minutes: int) -> None:
        if not self.is_active:
            self.duration_var.set(str(minutes))

    def _browse_executable(self) -> None:
        """
        File dialog to browse for application binaries (e.g., in /usr/bin or /opt).
        """
        path = filedialog.askopenfilename(
            title="Select Application Executable Binary",
            initialdir="/usr/bin",
            filetypes=[("All Files", "*"), ("Binaries", "*")],
        )
        if path:
            self.apps_text.configure(state=tk.NORMAL)
            current = self.apps_text.get("1.0", tk.END).strip()
            lines = [l.strip() for l in current.splitlines() if l.strip()]
            if path not in lines:
                lines.append(path)
                self.apps_text.delete("1.0", tk.END)
                self.apps_text.insert(tk.END, "\n".join(lines) + "\n")
                self._log_message(f"Added application path: {path}")

    # --------------------------------------------------------------------------
    # Configuration Loading & Persistence
    # --------------------------------------------------------------------------

    def _load_configuration(self) -> None:
        """
        Loads configured domains and apps from config file or loads defaults.
        """
        domains = DEFAULT_DOMAINS
        apps = DEFAULT_APPS

        config_file = DEFAULT_CONFIG_PATH
        local_config = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        if not os.path.exists(config_file) and os.path.exists(local_config):
            config_file = local_config

        if os.path.exists(config_file):
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    domains = cfg.get("domains", DEFAULT_DOMAINS)
                    apps = cfg.get("apps", DEFAULT_APPS)
                    if "duration" in cfg:
                        self.duration_var.set(str(cfg["duration"]))
            except Exception as e:
                self._log_message(f"Notice: Failed to load config from {config_file}: {e}")

        self.domains_text.configure(state=tk.NORMAL)
        self.domains_text.delete("1.0", tk.END)
        self.domains_text.insert(tk.END, "\n".join(domains) + "\n")

        self.apps_text.configure(state=tk.NORMAL)
        self.apps_text.delete("1.0", tk.END)
        self.apps_text.insert(tk.END, "\n".join(apps) + "\n")

    def _save_configuration(self, domains: List[str], apps: List[str], duration: float) -> None:
        """
        Saves current domains and apps into user config file with proper file ownership.
        """
        try:
            config_dir = os.path.dirname(DEFAULT_CONFIG_PATH)
            os.makedirs(config_dir, exist_ok=True)
            cfg = {
                "domains": domains,
                "apps": apps,
                "duration": duration,
                "last_saved": datetime.datetime.now().isoformat(),
            }
            with open(DEFAULT_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)

            # Fix file ownership if running via sudo
            sudo_uid = os.environ.get("SUDO_UID")
            sudo_gid = os.environ.get("SUDO_GID")
            if sudo_uid and sudo_gid:
                os.chown(DEFAULT_CONFIG_PATH, int(sudo_uid), int(sudo_gid))
        except Exception:
            pass

    def _get_configured_domains(self) -> List[str]:
        raw = self.domains_text.get("1.0", tk.END)
        return [l.strip() for l in raw.splitlines() if l.strip() and not l.strip().startswith("#")]

    def _get_configured_apps(self) -> List[str]:
        raw = self.apps_text.get("1.0", tk.END)
        return [l.strip() for l in raw.splitlines() if l.strip() and not l.strip().startswith("#")]

    # --------------------------------------------------------------------------
    # Lock State & Resumption
    # --------------------------------------------------------------------------

    def _check_initial_lock_state(self) -> None:
        """
        Checks if an active focus lock exists from a previous session or reboot.
        If locked, re-attaches to the active session and locks the UI.
        """
        if self.lock_mgr.is_locked():
            lock_data = self.lock_mgr.load_lock_data()
            if lock_data:
                remaining = self.lock_mgr.get_remaining_seconds()
                unlock_time_iso = lock_data.get("unlock_at_iso", "unknown")

                self.domains_text.configure(state=tk.NORMAL)
                if "domains" in lock_data:
                    self.domains_text.delete("1.0", tk.END)
                    self.domains_text.insert(tk.END, "\n".join(lock_data["domains"]) + "\n")

                self.apps_text.configure(state=tk.NORMAL)
                if "apps" in lock_data:
                    self.apps_text.delete("1.0", tk.END)
                    self.apps_text.insert(tk.END, "\n".join(lock_data["apps"]) + "\n")

                if "duration_minutes" in lock_data:
                    self.duration_var.set(str(lock_data["duration_minutes"]))

                self.total_duration_secs = float(lock_data.get("duration_minutes", 25)) * 60.0

                if remaining > 0:
                    self.is_active = True
                    self._set_ui_locked_state(True)
                    self._log_message(f"Resumed active focus lock. Expires at {unlock_time_iso}.")
                else:
                    self.is_active = True
                    self._set_ui_locked_state(True)
                    self.status_badge.config(
                        text="✓ FOCUS TIME EXPIRED — Ready to Unlock",
                        fg=self.ACCENT_GREEN,
                    )
                    self.timer_label.config(text="00:00", fg=self.ACCENT_GREEN)
                    self.time_detail_label.config(text="Session completed. Click 'Stop Focus' to restore system.")
                    self._log_message("Existing lock has expired. Ready to unlock.")

    def _set_ui_locked_state(self, locked: bool) -> None:
        """
        Updates UI elements according to locked / unlocked state.
        """
        if locked:
            self.start_btn.config(state=tk.DISABLED, bg=self.SURFACE_ALT)
            self.stop_btn.config(state=tk.NORMAL, bg="#da3633")
            self.duration_spin.config(state=tk.DISABLED)
            for btn in self.preset_buttons:
                btn.config(state=tk.DISABLED)
            self.domains_text.config(state=tk.DISABLED)
            self.apps_text.config(state=tk.DISABLED)
            self.status_badge.config(
                text="🔒 FOCUS MODE ACTIVE (ANTI-BYPASS LOCKED)",
                fg=self.ACCENT_RED,
            )
            self.timer_label.config(fg=self.ACCENT_RED)
        else:
            self.start_btn.config(state=tk.NORMAL, bg="#238636")
            self.stop_btn.config(state=tk.NORMAL, bg="#da3633")
            self.duration_spin.config(state=tk.NORMAL)
            for btn in self.preset_buttons:
                btn.config(state=tk.NORMAL)
            self.domains_text.config(state=tk.NORMAL)
            self.apps_text.config(state=tk.NORMAL)
            self.status_badge.config(
                text="● IDLE — Ready to Focus",
                fg=self.ACCENT_GREEN,
            )
            self._update_idle_timer_display()
            self.time_detail_label.config(text="Set duration below and click 'Start Focus'")
            self.progressbar.config(value=0.0)

    # --------------------------------------------------------------------------
    # Core Actions: Start & Stop Focus
    # --------------------------------------------------------------------------

    def start_focus(self) -> None:
        """
        Engages focus mode: modifies /etc/hosts, restarts systemd-resolved,
        terminates distracting apps, revokes binary execute permissions, and
        writes the root lock file.
        """
        if self.is_active or self.lock_mgr.is_locked():
            messagebox.showinfo("Session Active", "A focus session is already in progress.")
            return

        # Validate duration
        try:
            duration_val = float(self.duration_var.get().strip())
            if duration_val <= 0:
                raise ValueError("Duration must be greater than zero.")
        except ValueError:
            messagebox.showerror("Invalid Duration", "Please enter a valid positive duration in minutes.")
            return

        domains = self._get_configured_domains()
        apps = self._get_configured_apps()

        if not domains and not apps:
            messagebox.showwarning("Empty Target List", "Please specify at least one domain or application to block.")
            return

        # Confirmation Dialog
        confirm_msg = (
            f"Commit to Focus Mode for {duration_val:.1f} minutes?\n\n"
            f"• {len(domains)} domain(s) will be redirected in /etc/hosts\n"
            f"• {len(apps)} app(s) will be terminated and chmod -x applied\n"
            f"• Anti-bypass time lock will be ENGAGED in /etc/.focus_lock\n\n"
            "⚠️  CRITICAL: You will NOT be able to cancel or stop focus\n"
            "until the timer expires! Are you ready to begin?"
        )
        if not messagebox.askyesno("Confirm Focus Session", confirm_msg, icon="warning"):
            return

        self._log_message(f"Starting focus session for {duration_val} minutes...")

        # 1. Modify /etc/hosts
        if domains:
            ok, msg = self.system_mgr.inject_hosts(domains)
            self._log_message(msg)
            if not ok:
                messagebox.showerror("Network Blocking Error", msg)

        # 2. Flush DNS Cache via systemctl restart systemd-resolved
        ok, msg = self.system_mgr.flush_dns()
        self._log_message(msg)

        # 3. Terminate Apps & Revoke Permissions (chmod -x)
        all_modified_binaries: List[str] = []
        for app in apps:
            ok, msg, mod_paths = self.system_mgr.block_application(app)
            all_modified_binaries.extend(mod_paths)
            self._log_message(msg)

        # 4. Create Root Lock File (Anti-bypass guarantee)
        lock_created = self.lock_mgr.create_lock(duration_val, domains, apps, all_modified_binaries)
        if not lock_created:
            messagebox.showerror(
                "System Error",
                "Failed to write root lock file (/etc/.focus_lock).\nCheck filesystem permissions.",
            )
            # Revert applied blocks if lock file creation fails
            self.system_mgr.cleanup_hosts()
            for b in all_modified_binaries:
                self.system_mgr.unblock_application(b)
            return

        # Save active configuration
        self._save_configuration(domains, apps, duration_val)

        # Update State
        self.is_active = True
        self.total_duration_secs = duration_val * 60.0
        self._set_ui_locked_state(True)
        self._log_message("All distraction blocks successfully applied and locked.")
        self._send_desktop_notification(
            "Focus Mode Active 🛡️",
            f"Focus session started ({duration_val:.1f} minutes). Anti-bypass lock is engaged.",
        )

    def stop_focus(self) -> None:
        """
        Attempts to stop focus mode. If time is not expired, access is DENIED
        under the anti-bypass policy. If expired, restores system state.
        """
        if not self.is_active and not self.lock_mgr.is_locked():
            messagebox.showinfo("FocusBlock", "No active focus session is running.")
            return

        remaining_secs = self.lock_mgr.get_remaining_seconds()

        # ANTI-BYPASS CHECK: Deny request if time is not up!
        if remaining_secs > 0:
            mins = int(remaining_secs // 60)
            secs = int(remaining_secs % 60)
            warning_msg = (
                f"🛑 ACCESS DENIED — ANTI-BYPASS ACTIVE!\n\n"
                f"Focus mode is locked and cannot be stopped prematurely.\n\n"
                f"Remaining time: {mins:02d}m {secs:02d}s\n\n"
                f"Your distracting websites and applications will remain blocked until the timer reaches zero."
            )
            messagebox.showerror("Focus Locked", warning_msg)
            self._log_message(f"Stop request DENIED. Remaining time: {mins:02d}m {secs:02d}s.")
            return

        # Time is expired! Execute restoration.
        self._log_message("Focus timer completed. Restoring system state...")

        lock_data = self.lock_mgr.load_lock_data() or {}
        apps = lock_data.get("apps") or self._get_configured_apps()
        modified_binaries = lock_data.get("all_modified_binaries") or []

        # 1. Clean up /etc/hosts
        ok, msg = self.system_mgr.cleanup_hosts()
        self._log_message(msg)

        # 2. Flush DNS Cache via systemctl restart systemd-resolved
        ok, msg = self.system_mgr.flush_dns()
        self._log_message(msg)

        # 3. Restore executable permissions (chmod +x)
        restore_targets = set(apps)
        restore_targets.update(modified_binaries)

        for app in restore_targets:
            ok, msg = self.system_mgr.unblock_application(app)
            self._log_message(msg)

        # 4. Remove root lock file
        self.lock_mgr.remove_lock()
        self._log_message("Removed root lock file /etc/.focus_lock.")

        # Update State
        self.is_active = False
        self._set_ui_locked_state(False)

        try:
            self.root.bell()
        except Exception:
            pass

        self._send_desktop_notification(
            "Focus Session Completed 🎉",
            "Great job! All website and application restrictions have been restored.",
        )

        messagebox.showinfo(
            "Focus Session Completed",
            "🎉 Focus session completed successfully!\n\n"
            "All website restrictions have been removed, application execute permissions "
            "have been restored, and DNS cache has been refreshed.",
        )
        self._log_message("System fully restored to normal state.")

    # --------------------------------------------------------------------------
    # Timer & Window Management
    # --------------------------------------------------------------------------

    def _timer_tick(self) -> None:
        """
        Periodic 1-second timer tick to update clock display and progress.
        """
        if self.is_active:
            remaining_secs = self.lock_mgr.get_remaining_seconds()

            if remaining_secs > 0:
                mins = int(remaining_secs // 60)
                secs = int(remaining_secs % 60)
                self.timer_label.config(text=f"{mins:02d}:{secs:02d}", fg=self.ACCENT_RED)

                if self.total_duration_secs > 0:
                    elapsed = self.total_duration_secs - remaining_secs
                    percent = min(100.0, max(0.0, (elapsed / self.total_duration_secs) * 100.0))
                    self.progressbar.config(value=percent)
                    self.time_detail_label.config(
                        text=f"Anti-bypass locked • {percent:.0f}% completed\nClick 'Stop Focus' when time expires"
                    )
            else:
                self.timer_label.config(text="00:00", fg=self.ACCENT_GREEN)
                self.progressbar.config(value=100.0)
                self.status_badge.config(
                    text="✓ FOCUS TIME COMPLETED — Ready to Unlock",
                    fg=self.ACCENT_GREEN,
                )
                self.time_detail_label.config(text="Session completed! Click 'Stop Focus' to restore system.")

        self.root.after(1000, self._timer_tick)

    def on_window_close(self) -> None:
        """
        Intercepts window close event. If locked, warns user that system remains blocked.
        """
        if self.is_active and self.lock_mgr.get_remaining_seconds() > 0:
            msg = (
                "Focus mode is currently ACTIVE and LOCKED!\n\n"
                "Closing this window will NOT unblock websites or applications. "
                "The root-level lock in /etc/.focus_lock and /etc/hosts remains strictly active.\n\n"
                "You can reopen FocusBlock at any time to check remaining time or restore when finished.\n\n"
                "Do you want to close the window?"
            )
            if messagebox.askyesno("Focus Session Active", msg, icon="warning"):
                self.root.destroy()
        else:
            self.root.destroy()


# ==============================================================================
# Privilege Verification & Entry Point
# ==============================================================================

def verify_root_privileges(allow_unprivileged: bool = False) -> None:
    """
    Verifies that the script is executing with root privileges (EUID == 0).
    If not root, displays an informative GUI dialog advising the user how to run
    the application via sudo -E or pkexec, then exits.
    """
    if os.geteuid() == 0 or allow_unprivileged:
        return

    err_title = "FocusBlock — Root Privileges Required"
    err_msg = (
        "FocusBlock requires administrative (root) privileges to:\n"
        "  1. Modify /etc/hosts to redirect distracting domains.\n"
        "  2. Restart systemd-resolved to flush DNS cache.\n"
        "  3. Terminate running applications (killall -9).\n"
        "  4. Revoke binary execute permissions (chmod -x).\n"
        "  5. Create anti-bypass lock file in /etc/.focus_lock.\n\n"
        "Please run this tool using the launcher script:\n\n"
        "    ./run.sh\n\n"
        "Or run directly with sudo -E:\n\n"
        "    sudo -E python3 focus_block.py\n\n"
        "Or explore the UI in unprivileged simulation mode:\n\n"
        "    python3 focus_block.py --simulation"
    )

    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(err_title, err_msg)
        root.destroy()
    except Exception:
        # Fallback to terminal output if GUI connection fails
        print(f"\n[ERROR] {err_title}\n{err_msg}\n", file=sys.stderr)

    sys.exit(1)


def main() -> None:
    """
    Main program entry point.
    """
    parser = argparse.ArgumentParser(description="FocusBlock - Anti-Bypass Distraction Blocker")
    parser.add_argument(
        "--simulation",
        "--demo",
        action="store_true",
        help="Run in unprivileged simulation mode using mock files for testing UI and anti-bypass logic.",
    )
    parser.add_argument(
        "--emergency-unlock",
        "--unlock",
        action="store_true",
        help="Emergency recovery: force unlock, clean /etc/hosts, restore all binary permissions, and delete lock file.",
    )
    args = parser.parse_args()

    # Emergency unlock handler
    if args.emergency_unlock:
        if os.geteuid() != 0:
            print("[Error] Emergency unlock requires root privileges. Please run with sudo:", file=sys.stderr)
            print("  sudo python3 focus_block.py --emergency-unlock", file=sys.stderr)
            sys.exit(1)

        print("[FocusBlock] Executing emergency state restoration...")
        sys_mgr = SystemManager()
        l_mgr = FocusLockManager()

        # 1. Clean hosts
        ok, msg = sys_mgr.cleanup_hosts()
        print(f"  [1/4] {msg}")

        # 2. Flush DNS
        ok, msg = sys_mgr.flush_dns()
        print(f"  [2/4] {msg}")

        # 3. Restore app permissions
        lock_data = l_mgr.load_lock_data() or {}
        restore_targets = set(lock_data.get("apps", []))
        restore_targets.update(lock_data.get("all_modified_binaries", []))
        for d in DEFAULT_APPS:
            restore_targets.add(d)

        count = 0
        for target in restore_targets:
            ok, msg = sys_mgr.unblock_application(target)
            if ok:
                count += 1
                print(f"  [3/4] {msg}")

        # 4. Remove lock file
        l_mgr.remove_lock()
        print(f"  [4/4] Removed root lock file {l_mgr.lock_path}.")
        print("\n[SUCCESS] All websites and application execute permissions have been completely restored!\n")
        sys.exit(0)

    # Check for root privileges unless simulation mode was explicitly requested
    verify_root_privileges(allow_unprivileged=args.simulation)

    # In simulation mode, use temporary paths to test safely without root
    if args.simulation:
        sim_dir = tempfile.mkdtemp(prefix="focusblock_sim_")
        hosts_path = os.path.join(sim_dir, "hosts")
        lock_path = os.path.join(sim_dir, ".focus_lock")
        with open(hosts_path, "w") as f:
            f.write("127.0.0.1 localhost\n")
        system_mgr = SystemManager(hosts_path=hosts_path)
        lock_mgr = FocusLockManager(lock_path=lock_path)
    else:
        system_mgr = SystemManager()
        lock_mgr = FocusLockManager()

    try:
        root = tk.Tk()
    except tk.TclError as e:
        print(
            f"\n[FocusBlock Error] Unable to connect to graphical display ({e}).\n"
            f"If running via sudo or pkexec, ensure display authorization is exported:\n"
            f"  Use: ./run.sh\n"
            f"  Or:  xhost +si:localuser:root (if X11/Xwayland)\n",
            file=sys.stderr,
        )
        sys.exit(1)

    app = FocusBlockGUI(root, system_mgr=system_mgr, lock_mgr=lock_mgr, is_simulation=args.simulation)
    root.mainloop()


if __name__ == "__main__":
    main()
