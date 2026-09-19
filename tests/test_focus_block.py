#!/usr/bin/env python3
"""
Unit and Integration Tests for FocusBlock
=========================================
Tests the core logic of SystemManager, FocusLockManager, and FocusBlockGUI
anti-bypass logic in an isolated test environment.
"""

import os
import sys
import stat
import time
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Import FocusBlock components
import focus_block
from focus_block import (
    SystemManager,
    FocusLockManager,
    FocusBlockGUI,
    FOCUS_MARKER_START,
    FOCUS_MARKER_END,
    DOH_CANARY_DOMAIN,
)


class TestSystemManagerHosts(unittest.TestCase):
    """
    Tests /etc/hosts modification, DNS-over-HTTPS canary, and restoration.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.hosts_path = os.path.join(self.temp_dir.name, "hosts")
        self.initial_content = (
            "127.0.0.1 localhost\n"
            "::1 localhost ip6-localhost ip6-loopback\n"
            "127.0.1.1 my-workstation\n"
        )
        with open(self.hosts_path, "w", encoding="utf-8") as f:
            f.write(self.initial_content)

        self.sys_mgr = SystemManager(hosts_path=self.hosts_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_inject_hosts_and_subdomains(self):
        # Even if user enters www.youtube.com or reddit.com, both variants must be blocked
        domains = ["www.youtube.com", "reddit.com"]
        ok, msg = self.sys_mgr.inject_hosts(domains)
        self.assertTrue(ok, msg)

        with open(self.hosts_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Original entries must be preserved
        self.assertIn("127.0.0.1 localhost", content)
        self.assertIn("127.0.1.1 my-workstation", content)

        # Markers must exist
        self.assertIn(FOCUS_MARKER_START, content)
        self.assertIn(FOCUS_MARKER_END, content)

        # Both root domain and www. subdomain must be blocked
        self.assertIn("127.0.0.1\tyoutube.com", content)
        self.assertIn("127.0.0.1\twww.youtube.com", content)
        self.assertIn("127.0.0.1\treddit.com", content)
        self.assertIn("127.0.0.1\twww.reddit.com", content)

        # Firefox DoH canary must be included to prevent DoH bypass
        self.assertIn(f"127.0.0.1\t{DOH_CANARY_DOMAIN}", content)

    def test_cleanup_hosts(self):
        domains = ["twitter.com", "x.com"]
        self.sys_mgr.inject_hosts(domains)

        # Ensure injected
        with open(self.hosts_path, "r", encoding="utf-8") as f:
            injected_content = f.read()
        self.assertIn(FOCUS_MARKER_START, injected_content)

        # Now clean up
        ok, msg = self.sys_mgr.cleanup_hosts()
        self.assertTrue(ok, msg)

        with open(self.hosts_path, "r", encoding="utf-8") as f:
            cleaned_content = f.read()

        self.assertNotIn(FOCUS_MARKER_START, cleaned_content)
        self.assertNotIn(FOCUS_MARKER_END, cleaned_content)
        self.assertNotIn("twitter.com", cleaned_content)
        self.assertNotIn(DOH_CANARY_DOMAIN, cleaned_content)
        self.assertIn("127.0.0.1 localhost", cleaned_content)
        self.assertIn("127.0.1.1 my-workstation", cleaned_content)

    def test_idempotent_injection(self):
        """Re-injecting domains should cleanly replace old rules without duplicate blocks."""
        self.sys_mgr.inject_hosts(["youtube.com"])
        self.sys_mgr.inject_hosts(["reddit.com"])

        with open(self.hosts_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Only one pair of markers should exist
        self.assertEqual(content.count(FOCUS_MARKER_START), 1)
        self.assertEqual(content.count(FOCUS_MARKER_END), 1)
        self.assertIn("reddit.com", content)
        self.assertNotIn("youtube.com", content)


class TestSystemManagerApps(unittest.TestCase):
    """
    Tests application process termination and chmod -x / chmod +x permission management.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app_path = os.path.join(self.temp_dir.name, "dummy_app")
        # Create a dummy executable script
        with open(self.app_path, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\necho 'Running'\n")
        os.chmod(self.app_path, 0o755)

        self.sys_mgr = SystemManager()

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("subprocess.run")
    def test_block_and_unblock_application(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Initial state: executable
        initial_mode = os.stat(self.app_path).st_mode
        self.assertTrue(initial_mode & stat.S_IXUSR)

        # 1. Block application
        ok, msg, modified_paths = self.sys_mgr.block_application(self.app_path)
        self.assertTrue(ok, msg)
        self.assertIn(self.app_path, modified_paths)

        # Verify execute bit is revoked
        blocked_mode = os.stat(self.app_path).st_mode
        self.assertFalse(blocked_mode & stat.S_IXUSR, "Executable permission was not revoked!")
        self.assertFalse(blocked_mode & stat.S_IXGRP)
        self.assertFalse(blocked_mode & stat.S_IXOTH)

        # Verify killall -9 with -I was called
        mock_run.assert_any_call(["killall", "-9", "-I", "dummy_app"], capture_output=True, text=True, check=False)

        # 2. Unblock application
        ok, msg = self.sys_mgr.unblock_application(self.app_path)
        self.assertTrue(ok, msg)

        # Verify execute bit is restored
        restored_mode = os.stat(self.app_path).st_mode
        self.assertTrue(restored_mode & stat.S_IXUSR, "Executable permission was not restored!")

    def test_wrapper_script_target_resolution(self):
        """Tests that scripts executing underlying binaries resolve both files."""
        underlying_bin = os.path.join(self.temp_dir.name, "real_binary")
        with open(underlying_bin, "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(underlying_bin, 0o755)

        wrapper_script = os.path.join(self.temp_dir.name, "wrapper_launcher")
        with open(wrapper_script, "w") as f:
            f.write(f'#!/bin/sh\nexec "{underlying_bin}" "$@"\n')
        os.chmod(wrapper_script, 0o755)

        resolved = self.sys_mgr.resolve_all_app_targets(wrapper_script)
        self.assertIn(os.path.realpath(wrapper_script), resolved)
        self.assertIn(os.path.realpath(underlying_bin), resolved)


class TestFocusLockManager(unittest.TestCase):
    """
    Tests anti-bypass lock file creation, remaining time calculation, and persistence.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.lock_path = os.path.join(self.temp_dir.name, ".focus_lock")
        self.lock_mgr = FocusLockManager(lock_path=self.lock_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_lock_lifecycle(self):
        self.assertFalse(self.lock_mgr.is_locked())
        self.assertEqual(self.lock_mgr.get_remaining_seconds(), 0.0)

        # Create 10-minute lock
        created = self.lock_mgr.create_lock(
            duration_minutes=10,
            domains=["youtube.com"],
            apps=["/usr/bin/steam"],
            all_modified_binaries=["/usr/bin/steam", "/usr/lib/steam/steam"],
        )
        self.assertTrue(created)
        self.assertTrue(self.lock_mgr.is_locked())

        # Remaining seconds should be approximately 600s
        remaining = self.lock_mgr.get_remaining_seconds()
        self.assertGreater(remaining, 580.0)
        self.assertLessEqual(remaining, 600.0)

        # Load lock data
        data = self.lock_mgr.load_lock_data()
        self.assertEqual(data["duration_minutes"], 10)
        self.assertEqual(data["domains"], ["youtube.com"])
        self.assertEqual(data["apps"], ["/usr/bin/steam"])
        self.assertEqual(data["all_modified_binaries"], ["/usr/bin/steam", "/usr/lib/steam/steam"])

        # Test removal
        removed = self.lock_mgr.remove_lock()
        self.assertTrue(removed)
        self.assertFalse(self.lock_mgr.is_locked())
        self.assertEqual(self.lock_mgr.get_remaining_seconds(), 0.0)

    def test_expired_lock(self):
        self.lock_mgr.create_lock(duration_minutes=-5, domains=[], apps=[])
        self.assertTrue(self.lock_mgr.is_locked())
        self.assertEqual(self.lock_mgr.get_remaining_seconds(), 0.0)


class TestGUIInteractions(unittest.TestCase):
    """
    Tests GUI interaction, anti-bypass error dialog, and state transitions.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.hosts_path = os.path.join(self.temp_dir.name, "hosts")
        self.lock_path = os.path.join(self.temp_dir.name, ".focus_lock")

        with open(self.hosts_path, "w") as f:
            f.write("127.0.0.1 localhost\n")

        self.sys_mgr = SystemManager(hosts_path=self.hosts_path)
        self.lock_mgr = FocusLockManager(lock_path=self.lock_path)

        import tkinter as tk
        self.root = tk.Tk()
        self.root.withdraw()
        self.gui = FocusBlockGUI(self.root, system_mgr=self.sys_mgr, lock_mgr=self.lock_mgr, is_simulation=True)

    def tearDown(self):
        self.root.destroy()
        self.temp_dir.cleanup()

    @patch("tkinter.messagebox.askyesno", return_value=True)
    @patch("tkinter.messagebox.showerror")
    @patch("subprocess.run")
    def test_anti_bypass_denies_stop(self, mock_subproc, mock_showerror, mock_askyesno):
        mock_subproc.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Set 30 minute duration
        self.gui.duration_var.set("30")
        self.gui.domains_text.delete("1.0", "end")
        self.gui.domains_text.insert("end", "youtube.com\n")

        # Start focus
        self.gui.start_focus()
        self.assertTrue(self.gui.is_active)
        self.assertTrue(self.lock_mgr.is_locked())

        # Attempt to stop focus before time expires
        self.gui.stop_focus()

        # Must have triggered error dialog denying the stop
        self.assertTrue(mock_showerror.called)
        err_title, err_msg = mock_showerror.call_args[0]
        self.assertIn("Focus Locked", err_title)
        self.assertIn("ACCESS DENIED", err_msg)

        # System must REMAIN active and locked
        self.assertTrue(self.gui.is_active)
        self.assertTrue(self.lock_mgr.is_locked())

    @patch("tkinter.messagebox.askyesno", return_value=True)
    @patch("tkinter.messagebox.showinfo")
    @patch("subprocess.run")
    def test_successful_stop_after_expiration(self, mock_subproc, mock_showinfo, mock_askyesno):
        mock_subproc.return_value = MagicMock(returncode=0, stdout="", stderr="")

        # Simulate an expired lock
        self.lock_mgr.create_lock(duration_minutes=-1, domains=["youtube.com"], apps=[])
        self.gui.is_active = True

        # Now stop focus
        self.gui.stop_focus()

        # Must succeed and clear lock
        self.assertFalse(self.gui.is_active)
        self.assertFalse(self.lock_mgr.is_locked())
        self.assertTrue(mock_showinfo.called)

    def test_duration_change_updates_display(self):
        """Typing in spinbox or presets immediately updates the timer label when idle."""
        self.gui.duration_var.set("45")
        self.assertEqual(self.gui.timer_label.cget("text"), "45:00")
        self.gui.duration_var.set("15")
        self.assertEqual(self.gui.timer_label.cget("text"), "15:00")


if __name__ == "__main__":
    unittest.main()
