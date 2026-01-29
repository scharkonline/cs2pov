"""CS2 input automation using xdotool - simple, non-threaded functions."""

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional


def check_xdotool():
    """Check if xdotool is available."""
    if not shutil.which("xdotool"):
        raise RuntimeError("xdotool not found. Install with: emerge x11-misc/xdotool")


def find_cs2_window(display: str = ":0") -> Optional[str]:
    """Find CS2 window ID using xdotool.

    Args:
        display: X display where CS2 is running

    Returns:
        Window ID string, or None if not found
    """
    env = os.environ.copy()
    env["DISPLAY"] = display

    try:
        result = subprocess.run(
            ["xdotool", "search", "--class", "cs2"],
            capture_output=True,
            text=True,
            timeout=5,
            env=env
        )
        if result.returncode == 0 and result.stdout.strip():
            windows = result.stdout.strip().split('\n')
            # Return first window found
            if windows and windows[0]:
                return windows[0]
    except Exception:
        pass
    return None


def send_key(key: str, display: str = ":0", window_id: Optional[str] = None) -> bool:
    """Send a key press to CS2 window.

    Args:
        key: Key to send (e.g., "F5", "space", "Return")
        display: X display
        window_id: Optional window ID (will find if not provided)

    Returns:
        True if successful
    """
    if window_id is None:
        window_id = find_cs2_window(display)
    if not window_id:
        return False

    env = os.environ.copy()
    env["DISPLAY"] = display

    try:
        result = subprocess.run(
            ["xdotool", "key", "--window", window_id, key],
            capture_output=True,
            text=True,
            timeout=5,
            env=env
        )
        return result.returncode == 0
    except Exception:
        return False


def check_demo_ended(log_path: Path, last_position: int = 0) -> tuple[bool, int]:
    """Check if demo has ended by looking for marker in console.log.

    Args:
        log_path: Path to CS2 console.log
        last_position: File position to start reading from

    Returns:
        Tuple of (demo_ended: bool, new_position: int)
    """
    if not log_path.exists():
        return False, last_position

    demo_end_pattern = re.compile(r"CGameRules - paused on tick")

    try:
        with open(log_path, 'r', errors='ignore') as f:
            f.seek(last_position)
            content = f.read()
            new_position = f.tell()

            if demo_end_pattern.search(content):
                return True, new_position

            return False, new_position
    except Exception:
        return False, last_position


def wait_for_map_load(log_path: Path, timeout: float = 120, poll_interval: float = 0.5) -> bool:
    """Wait for map to finish loading by watching console.log.

    Looks for: [Client] Created physics for <map_name>

    Args:
        log_path: Path to CS2 console.log
        timeout: Maximum time to wait in seconds
        poll_interval: Time between checks

    Returns:
        True if map load detected, False if timeout
    """
    import time

    map_load_pattern = re.compile(r"\[Client\] Created physics for")
    start = time.time()
    last_position = 0

    while time.time() - start < timeout:
        if not log_path.exists():
            time.sleep(poll_interval)
            continue

        try:
            with open(log_path, 'r', errors='ignore') as f:
                f.seek(last_position)
                content = f.read()
                last_position = f.tell()

                if map_load_pattern.search(content):
                    return True
        except Exception:
            pass

        time.sleep(poll_interval)

    return False


def wait_for_cs2_window(display: str = ":0", timeout: float = 120, poll_interval: float = 2.0) -> Optional[str]:
    """Wait for CS2 window to appear.

    Args:
        display: X display
        timeout: Maximum time to wait in seconds
        poll_interval: Time between checks

    Returns:
        Window ID when found, or None if timeout
    """
    import time
    start = time.time()
    while time.time() - start < timeout:
        window_id = find_cs2_window(display)
        if window_id:
            return window_id
        time.sleep(poll_interval)
    return None
