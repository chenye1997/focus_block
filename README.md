# FocusBlock 🛡️

**A robust, anti-bypass Linux desktop focus & productivity shield written in Python 3 / Tkinter.**

FocusBlock enforces distraction-free focus sessions at the kernel and network layers by:
1. **Network Redirection**: Redirecting distracting websites to `127.0.0.1` and `::1` in `/etc/hosts` enclosed within specific marker comments.
2. **DNS-over-HTTPS (DoH) Canary**: Injects `use-application-dns.net` to tell browsers (Firefox, Chromium) to disable DoH and obey `/etc/hosts`.
3. **DNS Flushing**: Flushing the Linux DNS cache via `systemctl restart systemd-resolved` and `resolvectl flush-caches`.
4. **Application Termination**: Killing running distraction instances using `killall -9 -I` and `pkill -9 -i` (case-insensitive and process group support).
5. **Binary Permission Revocation**: Resolves wrapper scripts (e.g., `/usr/bin/steam` -> `/usr/lib/steam/steam`, `/usr/bin/spotify` -> `/opt/spotify/spotify`) and revokes execute permissions (`chmod -x`) on all underlying binaries.
6. **Anti-Bypass Time-Lock**: Storing a root-owned lock in `/etc/.focus_lock`. Any early attempt to cancel or stop focus is strictly rejected with an error warning dialog.
7. **State Restoration**: Restoring `/etc/hosts`, `chmod +x` permissions, and flushing DNS once the timer expires.

---

## Architecture & System Design

```
+-------------------------------------------------------------+
|                      FocusBlock GUI                         |
|      (Tkinter / ttk modern dark slate desktop interface)     |
+------------------------------+------------------------------+
                               |
            +------------------+------------------+
            |                                     |
            v                                     v
+-----------------------+             +-----------------------+
|     SystemManager     |             |   FocusLockManager    |
+-----------------------+             +-----------------------+
| • Modify /etc/hosts   |             | • Enforce anti-bypass |
| • DoH Canary Domain   |             | • /etc/.focus_lock    |
| • systemctl & resolvectl|            | • Resume on reboot    |
| • killall -9 & pkill  |             | • Calculate remaining |
| • Wrapper bin resolver|             |   time                |
| • chmod -x / chmod +x |             |                       |
+-----------------------+             +-----------------------+
```

### 1. Network Redirection & hosts Management
- Rules are injected safely between `# --- FOCUS_MODE_START ---` and `# --- FOCUS_MODE_END ---` in `/etc/hosts`.
- Both root domain (`domain.com`) and `www.domain.com` are automatically added to prevent partial blocking.
- `use-application-dns.net` is blocked to prevent Firefox and Chrome from bypassing `/etc/hosts` via encrypted DNS-over-HTTPS.
- Atomic writes with fallback support for bind-mounted files (such as in Docker or containerized environments).
- After injection or cleanup, `systemctl restart systemd-resolved` and `resolvectl flush-caches` flush the cache immediately.

### 2. Application Process & Permission Control
- Target applications are resolved to both wrapper scripts and their underlying binaries (e.g. `/usr/lib/steam/steam`, `/opt/spotify/spotify`).
- Active instances are terminated using `killall -9 -I` and `pkill -9 -i` to ensure case-insensitive matching (e.g., `Discord` vs `discord`).
- Execute permissions are revoked using `chmod -x` (`st_mode & ~0o111`), causing the Linux kernel to return `EACCES (Permission denied)` if launched from anywhere.
- When focus ends, `chmod +x` (`st_mode | 0o755`) restores normal permissions.

### 3. Anti-Bypass Time Lock
- When a focus session begins, an unlock timestamp is saved to `/etc/.focus_lock` with `0o600` root-only permissions.
- If the user clicks **Stop Focus** before the timer has reached zero:
  - FocusBlock computes the remaining minutes and seconds.
  - A modal error dialog (`messagebox.showerror`) is presented.
  - The stop request is denied; all network and binary restrictions remain strictly in effect.
- **Persistence Across Restarts**: If the window is closed or the workstation restarts, the lock file persists on disk. Upon relaunch, FocusBlock detects `/etc/.focus_lock`, re-attaches to the session, locks the UI, and resumes the countdown.

---

## How to Run

### Option 1: Using the Elevation Launcher (Recommended)
The [`run.sh`](file:///home/chen/focus_block/run.sh) script automatically sets up the X11/Xwayland `xauth` cookie for root, then prompts for PolicyKit GUI elevation (`pkexec`) or falls back to `sudo -E`:
```bash
./run.sh
```

### Option 2: Direct Elevation via `sudo -E`
```bash
sudo -E python3 focus_block.py
```

### Option 3: Zero-Root Simulation Mode (For Testing UI & Logic)
Want to test the GUI, countdown timer, anti-bypass denial dialog, and tab switching without modifying system files or typing your sudo password?
```bash
python3 focus_block.py --simulation
```
In simulation mode, FocusBlock uses temporary sandbox files in `/tmp` so you can safely test the entire workflow!

---

## Running Automated Tests

Run the full unit and integration test suite:

```bash
python3 -m unittest discover -s tests -v
```
All 10 tests run in isolated temporary sandboxes and verify:
- Hosts injection, DoH canary, and subdomain normalization.
- Wrapper script target resolution and process termination logic.
- Revocation (`chmod -x`) and restoration (`chmod +x`).
- Lock file persistence and anti-bypass denial.
- Real-time timer display updates upon spinbox edits.
