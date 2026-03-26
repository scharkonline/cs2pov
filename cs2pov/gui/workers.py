"""Background workers for long-running operations."""

import builtins
import json
import os
import re
import sys
import tempfile
import traceback
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QThread, Signal, QObject, QProcess, QTimer

from .config_bridge import gui_jobs_to_config


@contextmanager
def capture_prints(callback):
    """Temporarily redirect builtins.print to a callback.

    Only redirects prints to stdout/None (default). Prints to stderr or
    other file objects pass through to the original print.
    """
    original = builtins.print

    def redirect(*args, **kwargs):
        if kwargs.get("file") not in (None, sys.stdout):
            original(*args, **kwargs)
            return
        callback(" ".join(str(a) for a in args))

    builtins.print = redirect
    try:
        yield
    finally:
        builtins.print = original


class ParseWorker(QThread):
    """Parse a demo file in the background."""

    message = Signal(str)
    finished = Signal(object)  # Emits (DemoInfo, DemoTimeline | None)
    error = Signal(str)

    def __init__(self, demo_path: Path, player_steamid: int = None, player_name: str = "", parent=None):
        super().__init__(parent)
        self.demo_path = demo_path
        self.player_steamid = player_steamid
        self.player_name = player_name

    def run(self):
        try:
            from ..parser import parse_demo
            from ..preprocessor import preprocess_demo
            from ..loading import _set_gui_mode

            _set_gui_mode(True)

            with capture_prints(self.message.emit):
                self.message.emit(f"Parsing demo: {self.demo_path.name}")
                demo_info = parse_demo(self.demo_path)
                self.message.emit(
                    f"  Map: {demo_info.map_name}, {len(demo_info.players)} players, "
                    f"Ticks: {demo_info.total_ticks}, Rate: {demo_info.tick_rate}"
                )

                timeline = None
                if self.player_steamid is not None:
                    self.message.emit(f"Preprocessing timeline for {self.player_name or self.player_steamid}...")
                    timeline = preprocess_demo(self.demo_path, self.player_steamid, self.player_name)
                    self.message.emit(f"  {len(timeline.alive_segments)} alive segments, {len(timeline.deaths)} deaths")

                self.finished.emit((demo_info, timeline))

        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}")
        finally:
            from ..loading import _set_gui_mode
            _set_gui_mode(False)


class CLIJobRunner(QObject):
    """Run cs2pov CLI as a subprocess via QProcess.

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
        self._process: QProcess | None = None
        self._config_path: str | None = None
        self._current_job: int = -1
        self._job_results: dict[int, bool] = {}  # index -> success
        self._seen_batch_markers = False
        self._line_buffer = ""

    def start(self):
        """Build config, write to temp file, launch QProcess."""
        config = gui_jobs_to_config(self._jobs)

        # Write config to temp file
        fd, self._config_path = tempfile.mkstemp(
            suffix=".json", prefix="cs2pov_gui_"
        )
        with os.fdopen(fd, "w") as f:
            json.dump(config, f, indent=2)

        self.message.emit(f"Config: {self._config_path}")

        # Set up QProcess
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._on_output)
        self._process.finished.connect(self._on_finished)

        # Ensure unbuffered Python output
        env = QProcess.systemEnvironment()
        env.append("PYTHONUNBUFFERED=1")
        self._process.setEnvironment(env)

        args = ["-u", "-m", "cs2pov", "--config", self._config_path]
        self.message.emit(f"Running: {sys.executable} {' '.join(args)}")
        self._process.start(sys.executable, args)

    def cancel(self):
        """Terminate the subprocess."""
        if self._process is None:
            return
        self._process.terminate()
        # Force kill after 5 seconds if still running
        QTimer.singleShot(5000, self._force_kill)

    def _force_kill(self):
        if self._process is not None and self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()

    def _on_output(self):
        """Read and parse subprocess output line by line."""
        data = self._process.readAllStandardOutput().data().decode("utf-8", errors="replace")
        self._line_buffer += data

        while "\n" in self._line_buffer:
            line, self._line_buffer = self._line_buffer.split("\n", 1)
            self._process_line(line)

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

    def _on_finished(self, exit_code: int, exit_status: QProcess.ExitStatus):
        """Handle process completion."""
        # Flush remaining buffer
        if self._line_buffer.strip():
            self._process_line(self._line_buffer.strip())
            self._line_buffer = ""

        total = len(self._jobs)

        if not self._seen_batch_markers:
            # Single-job case: CLI prints no Job X/Y markers
            success = (exit_code == 0 and exit_status == QProcess.ExitStatus.NormalExit)
            self.job_started.emit(0)
            self._job_results[0] = success
            self.job_finished.emit(0, success)
        else:
            # Close out last job if still open
            if self._current_job >= 0 and self._current_job not in self._job_results:
                success = (exit_code == 0)
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

        self._process = None


class TrimWorker(QThread):
    """Run post-processing trim in the background."""

    message = Signal(str)
    finished = Signal(object)  # Emits (output_path, keep_segments) or None
    error = Signal(str)

    def __init__(
        self,
        video_path: Path,
        demo_path: Path,
        player_identifier: str,
        output_path: Path = None,
        tick_nav: bool = False,
        startup_time: float = None,
        verbose: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.video_path = video_path
        self.demo_path = demo_path
        self.player_identifier = player_identifier
        self.output_path = output_path
        self.tick_nav = tick_nav
        self.startup_time = startup_time
        self.verbose = verbose

    def run(self):
        try:
            from ..parser import parse_demo, find_player
            from ..preprocessor import preprocess_demo
            from ..cli import postprocess_video
            from ..trim import load_transitions
            from ..loading import _set_gui_mode

            _set_gui_mode(True)

            with capture_prints(self.message.emit):
                self.message.emit(f"Parsing demo: {self.demo_path.name}")
                demo_info = parse_demo(self.demo_path)
                player = find_player(demo_info, self.player_identifier)
                self.message.emit(f"Player: {player.name} ({player.steamid})")

                self.message.emit("Preprocessing timeline...")
                timeline = preprocess_demo(self.demo_path, player.steamid, player.name)

                transitions = None
                if self.tick_nav:
                    transitions = load_transitions(self.video_path)

                # Determine output path
                video_path = self.video_path
                if self.output_path:
                    # Copy input to output location, trim will work on it
                    import shutil
                    shutil.copy2(self.video_path, self.output_path)
                    video_path = self.output_path

                # Build a fake console log path (postprocess_video needs it for demo end detection)
                console_log_path = self.video_path.parent / f"console_{self.demo_path.stem}.log"

                self.message.emit("Trimming...")
                result_path, keep_segments = postprocess_video(
                    video_path=video_path,
                    console_log_path=console_log_path,
                    verbose=self.verbose,
                    timeline=timeline,
                    startup_time_override=self.startup_time,
                    transitions=transitions,
                )

                self.message.emit(f"Done: {result_path}")
                self.finished.emit((result_path, keep_segments))

        except Exception as e:
            tb = traceback.format_exc()
            self.error.emit(f"{type(e).__name__}: {e}\n{tb}")
        finally:
            from ..loading import _set_gui_mode
            _set_gui_mode(False)
