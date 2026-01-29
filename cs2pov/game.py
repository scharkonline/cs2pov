"""CS2 game process management."""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .exceptions import CS2NotFoundError, CS2LaunchError


# Common Steam library paths on Linux
STEAM_PATHS = [
    Path.home() / ".steam/steam/steamapps/common/Counter-Strike Global Offensive",
    Path.home() / ".local/share/Steam/steamapps/common/Counter-Strike Global Offensive",
    Path("/opt/steam/steamapps/common/Counter-Strike Global Offensive"),
    Path("/mnt/games/SteamLibrary/steamapps/common/Counter-Strike Global Offensive"),
]

# CS2 Steam App ID
CS2_APP_ID = 730


def validate_cs2_path(path: Path) -> Path:
    """Validate that a path contains a CS2 installation.

    Args:
        path: Path to validate

    Returns:
        The validated path

    Raises:
        CS2NotFoundError: If path doesn't contain CS2
    """
    cs2_binary = path / "game/bin/linuxsteamrt64/cs2"
    if cs2_binary.exists():
        return path
    raise CS2NotFoundError(
        f"CS2 not found at: {path}\n"
        f"Expected binary at: {cs2_binary}"
    )


def find_cs2_path(custom_path: Optional[Path] = None) -> Path:
    """Locate CS2 installation directory.

    Args:
        custom_path: Optional custom path to CS2 installation

    Returns:
        Path to CS2 installation

    Raises:
        CS2NotFoundError: If CS2 installation not found
    """
    # Check custom path first
    if custom_path:
        return validate_cs2_path(custom_path)

    # Check environment variable
    env_path = os.environ.get("CS2_PATH")
    if env_path:
        return validate_cs2_path(Path(env_path))

    # Search standard paths
    for path in STEAM_PATHS:
        cs2_binary = path / "game/bin/linuxsteamrt64/cs2"
        if cs2_binary.exists():
            return path

    raise CS2NotFoundError(
        "CS2 installation not found. Checked paths:\n"
        + "\n".join(f"  - {p}" for p in STEAM_PATHS)
        + "\n\nSpecify with --cs2-path or CS2_PATH environment variable"
    )


def get_demo_dir(cs2_path: Path) -> Path:
    """Get the demo directory path.

    Args:
        cs2_path: CS2 installation path

    Returns:
        Path to the replays directory
    """
    return cs2_path / "game/csgo/replays"


def get_cfg_dir(cs2_path: Path) -> Path:
    """Get the cfg directory path.

    Args:
        cs2_path: CS2 installation path

    Returns:
        Path to the cfg directory
    """
    return cs2_path / "game/csgo/cfg"


class CS2Process:
    """Manages CS2 game process for demo playback."""

    def __init__(self, cs2_path: Path, display: str, log_path: Optional[Path] = None):
        """Initialize CS2 process manager.

        Args:
            cs2_path: Path to CS2 installation
            display: X display to use (e.g., ":99")
            log_path: Optional path for CS2 log output
        """
        self.cs2_path = cs2_path
        self.display = display
        self.log_path = log_path
        self.process: Optional[subprocess.Popen] = None
        self._log_file: Optional[object] = None

    def launch(self, cfg_name: str, extra_args: Optional[list[str]] = None) -> subprocess.Popen:
        """Launch CS2 via Steam with recording configuration.

        Args:
            cfg_name: Name of CFG file to execute (without path)
            extra_args: Additional command line arguments

        Returns:
            The subprocess.Popen object
        """
        steam_path = shutil.which("steam")
        if not steam_path:
            raise CS2LaunchError("Steam executable not found in PATH")

        env = os.environ.copy()
        env["DISPLAY"] = self.display

        # Use steam -applaunch which reliably passes arguments to the game
        cmd = [
            steam_path,
            "-applaunch", str(CS2_APP_ID),
            "-novid",
            "-console",
            "-condebug",  # Enable console logging to console.log
            "-fullscreen",
            "-w", "1920",
            "-h", "1080",
            "+exec", cfg_name,
        ]
        if extra_args:
            cmd.extend(extra_args)

        # Set up logging if path provided
        if self.log_path:
            self._log_file = open(self.log_path, "w")
            stdout_dest = self._log_file
            stderr_dest = self._log_file
        else:
            stdout_dest = subprocess.DEVNULL
            stderr_dest = subprocess.DEVNULL

        # Launch CS2 via Steam
        # Steam will launch CS2 with proper authentication
        self.process = subprocess.Popen(
            cmd,
            env=env,
            stdout=stdout_dest,
            stderr=stderr_dest,
        )

        return self.process

    def find_cs2_process(self) -> Optional[int]:
        """Find the actual CS2 process PID (launched by Steam).

        Returns:
            PID of CS2 process, or None if not found
        """
        # Try multiple patterns to find CS2
        patterns = [
            ["pgrep", "-x", "cs2"],  # Exact match on process name
            ["pgrep", "-f", "cs2_linux64"],  # Match on command line
            ["pgrep", "-f", "/cs2$"],  # Match ending with /cs2
        ]

        for pattern in patterns:
            try:
                result = subprocess.run(
                    pattern,
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0 and result.stdout.strip():
                    # Return first matching PID
                    pids = result.stdout.strip().split()
                    if pids:
                        return int(pids[0])
            except Exception:
                continue

        return None

    def wait_for_exit(
        self,
        timeout: Optional[float] = None,
        poll_interval: float = 2.0,
        status_callback: Optional[callable] = None
    ) -> int:
        """Wait for CS2 to exit (demo playback complete).

        Since CS2 is launched via Steam, we monitor the CS2 process directly
        rather than the Steam launcher process.

        Args:
            timeout: Maximum time to wait in seconds (for demo playback, not startup)
            poll_interval: How often to check if CS2 is running
            status_callback: Optional function called each iteration, receives elapsed time.
                             Should return False to abort waiting.

        Returns:
            0 when CS2 exits

        Raises:
            subprocess.TimeoutExpired: If timeout is reached
        """
        import time

        # Wait for CS2 to actually start (Steam takes a moment)
        cs2_pid = None
        startup_timeout = 120  # Give CS2 up to 2 minutes to start
        startup_start = time.time()

        print("    Waiting for CS2 to start...")
        while cs2_pid is None:
            cs2_pid = self.find_cs2_process()
            if cs2_pid is None:
                if time.time() - startup_start > startup_timeout:
                    raise subprocess.TimeoutExpired(
                        cmd="cs2", timeout=startup_timeout
                    )
                time.sleep(poll_interval)

        print(f"    CS2 detected (PID: {cs2_pid})")
        print(f"    Monitoring CS2 process... (timeout: {timeout}s)" if timeout else "    Monitoring CS2 process...")

        # Now wait for CS2 to exit - timeout starts from HERE, not from startup
        playback_start = time.time()
        consecutive_not_found = 0  # Track consecutive "not found" to avoid false positives
        last_status_time = playback_start

        while True:
            current_pid = self.find_cs2_process()

            if current_pid is None:
                consecutive_not_found += 1
                # Require 3 consecutive checks to confirm CS2 is really gone
                if consecutive_not_found >= 3:
                    print("    CS2 process ended")
                    return 0
            else:
                consecutive_not_found = 0

            if timeout is not None:
                elapsed = time.time() - playback_start
                if elapsed >= timeout:
                    raise subprocess.TimeoutExpired(cmd="cs2", timeout=timeout)

                # Print status every 60 seconds
                if time.time() - last_status_time >= 60:
                    remaining = timeout - elapsed
                    print(f"    Still recording... ({elapsed/60:.1f} min elapsed, {remaining/60:.1f} min remaining)")
                    last_status_time = time.time()

            # Call status callback if provided
            if status_callback:
                elapsed = time.time() - playback_start
                if status_callback(elapsed) is False:
                    print("    Recording aborted by callback")
                    return 1

            time.sleep(poll_interval)

    def is_running(self) -> bool:
        """Check if CS2 is still running."""
        return self.find_cs2_process() is not None

    def terminate(self):
        """Force terminate CS2 by PID (safe, won't affect our process)."""
        import time

        # Close log file if open
        if self._log_file:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None

        # Find CS2's actual PID
        cs2_pid = self.find_cs2_process()
        if cs2_pid is None:
            self.process = None
            return

        # Try graceful termination with SIGTERM first
        try:
            os.kill(cs2_pid, 15)  # SIGTERM
        except (ProcessLookupError, PermissionError):
            pass

        # Wait a moment for graceful shutdown
        time.sleep(3)

        # If still running, use SIGKILL
        if self.is_running():
            cs2_pid = self.find_cs2_process()
            if cs2_pid:
                try:
                    os.kill(cs2_pid, 9)  # SIGKILL
                except (ProcessLookupError, PermissionError):
                    pass

        self.process = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.terminate()
        return False
