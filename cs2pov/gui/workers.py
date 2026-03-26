"""Background workers for long-running operations."""

import builtins
import sys
import time
import traceback
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QThread, Signal, QObject


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


class RecordWorker(QThread):
    """Run the full recording pipeline in the background."""

    message = Signal(str)
    finished = Signal(object)  # Emits RecordingResult or None
    error = Signal(str)

    def __init__(
        self,
        demo_path: Path,
        player_identifier: str,
        output_path: Path,
        resolution: tuple = (1920, 1080),
        framerate: int = 60,
        hide_hud: bool = True,
        display_num: int = 0,
        enable_audio: bool = True,
        tick_nav: bool = False,
        do_trim: bool = True,
        cs2_path_override: Path = None,
        verbose: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.demo_path = demo_path
        self.player_identifier = player_identifier
        self.output_path = output_path
        self.resolution = resolution
        self.framerate = framerate
        self.hide_hud = hide_hud
        self.display_num = display_num
        self.enable_audio = enable_audio
        self.tick_nav = tick_nav
        self.do_trim = do_trim
        self.cs2_path_override = cs2_path_override
        self.verbose = verbose

    def run(self):
        try:
            from ..cli import record_demo, postprocess_video
            from ..loading import _set_gui_mode

            _set_gui_mode(True)

            with capture_prints(self.message.emit):
                result = record_demo(
                    demo_path=self.demo_path,
                    player_identifier=self.player_identifier,
                    output_path=self.output_path,
                    resolution=self.resolution,
                    framerate=self.framerate,
                    hide_hud=self.hide_hud,
                    display_num=self.display_num,
                    verbose=self.verbose,
                    cs2_path_override=self.cs2_path_override,
                    enable_audio=self.enable_audio,
                    tick_nav=self.tick_nav,
                )

                if result.success and self.do_trim:
                    self.message.emit("\nPost-processing: trimming death periods...")
                    postprocess_video(
                        video_path=result.video_path,
                        console_log_path=result.console_log_path,
                        verbose=self.verbose,
                        timeline=result.timeline,
                        transitions=result.transitions,
                    )

                self.finished.emit(result)

        except Exception as e:
            tb = traceback.format_exc()
            self.error.emit(f"{type(e).__name__}: {e}\n{tb}")
        finally:
            from ..loading import _set_gui_mode
            _set_gui_mode(False)


class BatchJobWorker(QThread):
    """Run a list of jobs sequentially."""

    message = Signal(str)
    job_started = Signal(int)  # job index (0-based)
    job_finished = Signal(int, bool)  # index, success
    job_error = Signal(int, str)  # index, error_message
    all_finished = Signal(int, int)  # succeeded_count, total_count

    def __init__(self, jobs: list, parent=None):
        super().__init__(parent)
        self.jobs = jobs

    def run(self):
        succeeded = 0
        last_was_recording = False

        for i, job in enumerate(self.jobs):
            try:
                from ..loading import _set_gui_mode
                _set_gui_mode(True)

                job_type = job["type"]
                prefix = f"[Job {i + 1}]"

                # Sleep between recording jobs to let CS2 fully exit
                if last_was_recording and job_type in ("pov", "record"):
                    self.message.emit(f"{prefix} Waiting 10s for CS2 to exit...")
                    time.sleep(10)

                # Platform guard for recording jobs
                if job_type in ("pov", "record") and sys.platform != "linux":
                    self.job_error.emit(i, "Recording only available on Linux")
                    last_was_recording = False
                    continue

                self.job_started.emit(i)
                self.message.emit(f"{prefix} Starting {job_type} job...")

                with capture_prints(lambda msg, p=prefix: self.message.emit(f"{p} {msg}")):
                    if job_type in ("pov", "record"):
                        self._run_recording(job, prefix)
                        last_was_recording = True
                    elif job_type == "trim":
                        self._run_trim(job, prefix)
                        last_was_recording = False
                    elif job_type == "comms":
                        self._run_comms(job, prefix)
                        last_was_recording = False
                    else:
                        raise ValueError(f"Unknown job type: {job_type}")

                succeeded += 1
                self.job_finished.emit(i, True)
                self.message.emit(f"{prefix} Completed successfully.")

            except Exception as e:
                tb = traceback.format_exc()
                self.job_error.emit(i, f"{type(e).__name__}: {e}")
                self.message.emit(f"[Job {i + 1}] FAILED: {e}\n{tb}")
                self.job_finished.emit(i, False)
                last_was_recording = job.get("type") in ("pov", "record")
            finally:
                from ..loading import _set_gui_mode
                _set_gui_mode(False)

        self.all_finished.emit(succeeded, len(self.jobs))

    def _run_recording(self, job: dict, prefix: str):
        from ..cli import record_demo, postprocess_video

        player = job.get("player")
        if player is None:
            raise ValueError("No player selected")

        player_id = str(player.steamid)
        demo_path = Path(job["demo_path"]).resolve()
        output_path = Path(job["output_path"]).resolve()

        result = record_demo(
            demo_path=demo_path,
            player_identifier=player_id,
            output_path=output_path,
            resolution=job.get("resolution", (1920, 1080)),
            framerate=job.get("framerate", 60),
            hide_hud=job.get("hide_hud", True),
            display_num=job.get("display_num", 0),
            enable_audio=job.get("enable_audio", True),
            tick_nav=job.get("tick_nav", False),
        )

        if result.success and job.get("do_trim", False):
            self.message.emit(f"{prefix} Post-processing: trimming...")
            postprocess_video(
                video_path=result.video_path,
                console_log_path=result.console_log_path,
                timeline=result.timeline,
                transitions=result.transitions,
            )

        if not result.success:
            raise RuntimeError(f"Recording failed: {result.exit_reason}")

    def _run_trim(self, job: dict, prefix: str):
        from ..parser import parse_demo, find_player
        from ..preprocessor import preprocess_demo
        from ..cli import postprocess_video
        from ..trim import load_transitions

        demo_path = Path(job["demo_path"]).resolve()
        video_path = Path(job["video_path"]).resolve()

        player = job.get("player")
        if player is None:
            raise ValueError("No player selected")

        demo_info = parse_demo(demo_path)
        player_info = find_player(demo_info, str(player.steamid))
        timeline = preprocess_demo(demo_path, player_info.steamid, player_info.name)

        transitions = None
        if job.get("tick_nav"):
            transitions = load_transitions(video_path)

        output_path = job.get("output_path")
        target = video_path
        if output_path:
            import shutil
            target = Path(output_path).resolve()
            shutil.copy2(video_path, target)

        console_log_path = video_path.parent / f"console_{demo_path.stem}.log"

        postprocess_video(
            video_path=target,
            console_log_path=console_log_path,
            timeline=timeline,
            startup_time_override=job.get("startup_time"),
            transitions=transitions,
        )

    def _run_comms(self, job: dict, prefix: str):
        from ..parser import parse_demo, find_player
        from ..preprocessor import preprocess_demo
        from ..comms import apply_comms_overlay

        demo_path = Path(job["demo_path"]).resolve()
        video_path = Path(job["video_path"]).resolve()
        comms_audio_path = Path(job["comms_audio_path"]).resolve()

        player = job.get("player")
        if player is None:
            raise ValueError("No player selected")

        demo_info = parse_demo(demo_path)
        player_info = find_player(demo_info, str(player.steamid))
        timeline = preprocess_demo(demo_path, player_info.steamid, player_info.name)

        output_path = job.get("output_path")
        if not output_path:
            output_path = str(video_path.parent / f"{video_path.stem}_comms{video_path.suffix}")
        output_path = Path(output_path).resolve()

        apply_comms_overlay(
            video_path=video_path,
            comms_audio_path=comms_audio_path,
            output_path=output_path,
            timeline=timeline,
            r1_sync_time=job.get("r1_sync_time", 0.0),
            game_volume=job.get("game_volume", 1.0),
            comms_volume=job.get("comms_volume", 1.0),
        )


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
