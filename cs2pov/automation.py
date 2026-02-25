"""CS2 input automation using xdotool - simple, non-threaded functions."""

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
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


def check_demo_ended_tick_aware(
    log_path: Path,
    last_position: int,
    min_end_tick: int,
) -> tuple[bool, int]:
    """Check if demo has ended, filtering out pauses from navigation.

    In tick-nav mode, 'CGameRules - paused on tick X' lines appear from our
    own deliberate pauses (calibration, death handling). This function only
    considers it a real demo end if the tick is past min_end_tick.

    Args:
        log_path: Path to CS2 console.log
        last_position: File position to start reading from
        min_end_tick: Only consider ended if paused tick >= this value.
            Typically the last alive segment's end tick.

    Returns:
        Tuple of (demo_ended: bool, new_position: int)
    """
    if not log_path.exists():
        return False, last_position

    demo_end_pattern = re.compile(r"CGameRules - paused on tick (\d+)")

    try:
        with open(log_path, 'r', errors='ignore') as f:
            f.seek(last_position)
            content = f.read()
            new_position = f.tell()

            for match in demo_end_pattern.finditer(content):
                tick = int(match.group(1))
                if tick >= min_end_tick:
                    return True, new_position

            return False, new_position
    except Exception:
        return False, last_position


@dataclass
class DemoEndInfo:
    """Information about when the demo ended."""
    tick: int
    timestamp: float  # Unix timestamp from console.log


def parse_demo_end_info(log_path: Path) -> Optional[DemoEndInfo]:
    """Parse demo end information from console.log.

    Extracts the tick number and timestamp from:
    MM/DD HH:mm:ss CGameRules - paused on tick <tick>

    Args:
        log_path: Path to console.log file

    Returns:
        DemoEndInfo with tick and timestamp, or None if not found
    """
    from datetime import datetime

    if not log_path.exists():
        return None

    # Pattern: MM/DD HH:mm:ss CGameRules - paused on tick <number>
    demo_end_pattern = re.compile(
        r"^(\d{2}/\d{2} \d{2}:\d{2}:\d{2}).*CGameRules - paused on tick (\d+)"
    )

    try:
        with open(log_path, 'r', errors='ignore') as f:
            for line in f:
                match = demo_end_pattern.match(line)
                if match:
                    timestamp_str = match.group(1)
                    tick = int(match.group(2))

                    # Parse timestamp (MM/DD HH:mm:ss) - assume current year
                    parsed = datetime.strptime(timestamp_str, "%m/%d %H:%M:%S")
                    parsed = parsed.replace(year=datetime.now().year)

                    return DemoEndInfo(tick=tick, timestamp=parsed.timestamp())
    except Exception:
        pass

    return None


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
    map_load_pattern = re.compile(r"\[Client\] Created physics for")
    start_t = time.time()
    last_position = 0

    while time.time() - start_t < timeout:
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


def wait_for_demo_ready(
    log_path: Path,
    timeout: float = 180,
    poll_interval: float = 0.5
) -> bool:
    """Wait for demo to be ready by watching console.log.

    Looks for: [HostStateManager] Host activate: Playing Demo

    This indicates the demo has loaded and playback is starting.

    Args:
        log_path: Path to CS2 console.log
        timeout: Maximum time to wait in seconds
        poll_interval: Time between checks

    Returns:
        True if ready state detected, False if timeout
    """
    ready_pattern = re.compile(
        r"\[HostStateManager\] Host activate: Playing Demo"
    )
    start_t = time.time()
    last_position = 0

    while time.time() - start_t < timeout:
        if not log_path.exists():
            time.sleep(poll_interval)
            continue

        try:
            with open(log_path, 'r', errors='ignore') as f:
                f.seek(last_position)
                content = f.read()
                last_position = f.tell()

                if ready_pattern.search(content):
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
    start = time.time()
    while time.time() - start < timeout:
        window_id = find_cs2_window(display)
        if window_id:
            return window_id
        time.sleep(poll_interval)
    return None


# =============================================================================
# Tick-based navigation primitives
# =============================================================================

def send_console_command(command: str, display: str, window_id: str) -> bool:
    """Send a console command to CS2 by opening console, typing, and closing.

    Opens console (grave key), types the command, presses Return, closes console.
    Includes small delays between steps for reliability.

    Args:
        command: Console command to send (e.g. "demo_gototick 12345")
        display: X display string
        window_id: CS2 window ID

    Returns:
        True if all xdotool steps succeeded
    """
    env = os.environ.copy()
    env["DISPLAY"] = display

    steps = [
        # Open console
        (["xdotool", "key", "--window", window_id, "grave"], 0.1),
        # Type command
        (["xdotool", "type", "--window", window_id, "--clearmodifiers", command], 0.05),
        # Press enter
        (["xdotool", "key", "--window", window_id, "Return"], 0.1),
        # Close console
        (["xdotool", "key", "--window", window_id, "grave"], 0.0),
    ]

    for cmd, delay in steps:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5, env=env)
            if result.returncode != 0:
                return False
        except Exception:
            return False
        if delay > 0:
            time.sleep(delay)

    return True


def read_paused_tick(
    console_log_path: Path,
    last_position: int,
    timeout: float = 5.0,
) -> tuple[Optional[int], int]:
    """After a demo_pause, poll console.log for the paused tick line.

    Looks for: CGameRules - paused on tick X

    Args:
        console_log_path: Path to CS2 console.log
        last_position: File position to start reading from
        timeout: Maximum time to wait

    Returns:
        (tick, new_position) or (None, position) on timeout
    """
    pattern = re.compile(r"CGameRules - paused on tick (\d+)")
    start = time.time()

    while time.time() - start < timeout:
        if not console_log_path.exists():
            time.sleep(0.1)
            continue

        try:
            with open(console_log_path, 'r', errors='ignore') as f:
                f.seek(last_position)
                content = f.read()
                new_position = f.tell()

                match = pattern.search(content)
                if match:
                    return int(match.group(1)), new_position

                last_position = new_position
        except Exception:
            pass

        time.sleep(0.1)

    return None, last_position


def calibrate_tick_offset(
    console_log_path: Path,
    display: str,
    window_id: str,
    log_position: int,
    calibration_tick: int = 0,
    verbose: bool = False,
) -> tuple[int, int]:
    """Calibrate the tick offset by sending demo_gototick and measuring drift.

    demo_gototick X doesn't land exactly at tick X — there's a consistent
    drift per demo. We measure it by:
    1. Send demo_gototick to a known tick
    2. Pause and read actual tick from console
    3. offset = actual_tick - requested_tick
    4. Resume playback

    All future goto calls subtract this offset: demo_gototick(target - offset).

    Args:
        console_log_path: Path to CS2 console.log
        display: X display string
        window_id: CS2 window ID
        log_position: Current position in console.log
        calibration_tick: Tick to goto for calibration (default 0)
        verbose: Print debug output

    Returns:
        (offset, new_log_position). offset = actual_tick - calibration_tick.
    """
    # Seek to the calibration tick
    send_console_command(f"demo_gototick {calibration_tick}", display, window_id)
    time.sleep(2.0)

    # Pause demo via F7
    send_key("F7", display, window_id)
    time.sleep(1.0)

    # Read the paused tick from console
    actual_tick, log_position = read_paused_tick(console_log_path, log_position, timeout=5.0)

    # Let CS2 settle before unpausing
    time.sleep(0.5)

    # Resume via F6 (demo_resume — idempotent)
    send_key("F6", display, window_id)

    if actual_tick is not None:
        offset = actual_tick - calibration_tick
        if verbose:
            print(f"    Tick calibration: goto {calibration_tick} → landed at {actual_tick}, offset={offset}")
        return offset, log_position
    else:
        if verbose:
            print(f"    Tick calibration failed, using offset=0")
        return 0, log_position


def check_death_in_console(
    console_log_path: Path,
    player_slot: int,
    last_position: int,
) -> tuple[bool, int]:
    """Check console.log for player death (Shutdown prediction).

    Incrementally reads console.log looking for:
    [Prediction] Shutdown prediction for player slot {player_slot}

    Args:
        console_log_path: Path to CS2 console.log
        player_slot: 0-based player slot index
        last_position: File position to start reading from

    Returns:
        (detected, new_position)
    """
    if not console_log_path.exists():
        return False, last_position

    pattern = re.compile(
        rf"\[Prediction\] Shutdown prediction for player slot {player_slot}\b"
    )

    try:
        with open(console_log_path, 'r', errors='ignore') as f:
            f.seek(last_position)
            content = f.read()
            new_position = f.tell()

            if pattern.search(content):
                return True, new_position

            return False, new_position
    except Exception:
        return False, last_position
