"""Background workers for long-running operations."""

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QThread, Signal, QObject, QTimer

from .config_bridge import gui_jobs_to_config


class ParseWorker(QThread):
    """Parse a demo file by running `cs2pov info --json` as a subprocess."""

    message = Signal(str)
    finished = Signal(object)  # Emits (demo_info_ns, None)
    error = Signal(str)

    def __init__(self, demo_path: Path, player_steamid: int = None, player_name: str = "", parent=None):
        super().__init__(parent)
        self.demo_path = demo_path

    def run(self):
        try:
            cmd = [sys.executable, "-u", "-m", "cs2pov", "info", "--json", str(self.demo_path)]
            self.message.emit(f"Parsing demo: {self.demo_path.name}")

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120
            )

            if result.returncode != 0:
                stderr = result.stderr.strip() if result.stderr else "unknown error"
                self.error.emit(f"Parse failed: {stderr}")
                return

            data = json.loads(result.stdout)

            # Convert JSON dict to attribute-accessible objects matching DemoInfo interface
            players = [
                SimpleNamespace(
                    name=p["name"],
                    steamid=p["steamid"],
                    kills=p.get("kills", 0),
                    assists=p.get("assists", 0),
                )
                for p in data.get("players", [])
            ]

            demo_info = SimpleNamespace(
                map_name=data.get("map", ""),
                tick_rate=data.get("tick_rate", 0),
                total_ticks=int(data.get("duration_seconds", 0) * data.get("tick_rate", 64)),
                players=players,
            )

            self.message.emit(
                f"  Map: {demo_info.map_name}, {len(demo_info.players)} players, "
                f"Tickrate: {demo_info.tick_rate}"
            )

            self.finished.emit((demo_info, None))

        except subprocess.TimeoutExpired:
            self.error.emit("Parse timed out after 120 seconds")
        except json.JSONDecodeError as e:
            self.error.emit(f"Failed to parse CLI output: {e}")
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")


class CLIJobRunner(QObject):
    """Run cs2pov CLI as a subprocess.

    Writes a temporary cs2pov.json config and launches
    `python -u -m cs2pov --config <path>` to execute jobs.
    """

    message = Signal(str)
    job_started = Signal(int)        # 0-based index
    job_finished = Signal(int, bool) # index, success
    all_finished = Signal(int, int)  # succeeded, total

    # Patterns from CLI _run_batch() output
    _RE_JOB_HEADER = re.compile(r"^Job (\d+)/(\d+): .+ \((\w+)\)$")
    _RE_BATCH_DONE = re.compile(r"^Batch complete: (\d+)/(\d+) succeeded$")
    _RE_JOB_SKIP = re.compile(r"^\[SKIP\] Job (\d+):")
    _RE_JOB_FAIL = re.compile(r"^\s+\[FAIL\] Job (\d+):")

    def __init__(self, jobs: list[dict], parent=None):
        super().__init__(parent)
        self._jobs = jobs
        self._proc: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._config_path: str | None = None
        self._current_job: int = -1
        self._job_results: dict[int, bool] = {}  # index -> success
        self._seen_batch_markers = False

    def start(self):
        """Build config, write to temp file, launch subprocess."""
        config = gui_jobs_to_config(self._jobs)

        # Write config to temp file
        fd, self._config_path = tempfile.mkstemp(
            suffix=".json", prefix="cs2pov_gui_"
        )
        with os.fdopen(fd, "w") as f:
            json.dump(config, f, indent=2)

        self.message.emit(f"Config: {self._config_path}")
        self.message.emit(json.dumps(config, indent=2))

        cmd = [sys.executable, "-u", "-m", "cs2pov", "--config", self._config_path]
        self.message.emit(f"Running: {' '.join(cmd)}")

        self._proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )

        # Read output in a background thread to avoid blocking the GUI
        self._reader_thread = threading.Thread(
            target=self._read_output, daemon=True
        )
        self._reader_thread.start()

    def _read_output(self):
        """Read subprocess stdout line-by-line (runs in background thread)."""
        try:
            for raw_line in self._proc.stdout:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
                self._process_line(line)
        except Exception:
            pass
        finally:
            exit_code = self._proc.wait()
            self._on_finished(exit_code)

    def cancel(self):
        """Terminate the subprocess."""
        if self._proc is None:
            return
        self._proc.terminate()
        # Safety net: force-kill the Python process if still alive after 10s
        QTimer.singleShot(10000, self._force_kill)

    def _force_kill(self):
        if self._proc is not None and self._proc.poll() is None:
            self._proc.kill()

    def _process_line(self, line: str):
        """Parse a single output line for job progress markers."""
        self.message.emit(line)

        # Job header: "Job 1/3: demo.dem (pov)"
        m = self._RE_JOB_HEADER.match(line)
        if m:
            self._seen_batch_markers = True
            job_num = int(m.group(1))
            idx = job_num - 1  # 0-based

            # Close out previous job as success (if not already failed)
            if self._current_job >= 0 and self._current_job not in self._job_results:
                self._job_results[self._current_job] = True
                self.job_finished.emit(self._current_job, True)

            self._current_job = idx
            self.job_started.emit(idx)
            return

        # Job skipped: "[SKIP] Job 1: ..."
        m = self._RE_JOB_SKIP.match(line)
        if m:
            self._seen_batch_markers = True
            idx = int(m.group(1)) - 1
            self._job_results[idx] = False
            self.job_started.emit(idx)
            self.job_finished.emit(idx, False)
            return

        # Job failed in summary: "  [FAIL] Job 1: ..."
        m = self._RE_JOB_FAIL.match(line)
        if m:
            idx = int(m.group(1)) - 1
            if idx not in self._job_results:
                self._job_results[idx] = False
                self.job_finished.emit(idx, False)
            elif self._job_results.get(idx) is True:
                # Was marked success but summary says fail — correct it
                self._job_results[idx] = False
                self.job_finished.emit(idx, False)
            return

        # Batch complete: "Batch complete: 2/3 succeeded"
        m = self._RE_BATCH_DONE.match(line)
        if m:
            # Close out the last running job using the summary
            if self._current_job >= 0 and self._current_job not in self._job_results:
                self._job_results[self._current_job] = True
                self.job_finished.emit(self._current_job, True)
            return

    def _on_finished(self, exit_code: int):
        """Handle process completion."""
        total = len(self._jobs)

        if not self._seen_batch_markers:
            # Single-job case: CLI prints no Job X/Y markers
            success = exit_code == 0
            self.job_started.emit(0)
            self._job_results[0] = success
            self.job_finished.emit(0, success)
        else:
            # Close out last job if still open
            if self._current_job >= 0 and self._current_job not in self._job_results:
                success = exit_code == 0
                self._job_results[self._current_job] = success
                self.job_finished.emit(self._current_job, success)

        succeeded = sum(1 for v in self._job_results.values() if v)
        self.all_finished.emit(succeeded, total)

        # Clean up temp config
        if self._config_path:
            try:
                os.unlink(self._config_path)
            except OSError:
                pass

        self._proc = None
