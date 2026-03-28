"""FFmpeg video and audio capture - simplified, full-display only."""

import subprocess
import time
from pathlib import Path
from typing import Optional

from .exceptions import CaptureError
from .platform import IS_WINDOWS, IS_LINUX, ensure_ffmpeg


def get_default_audio_monitor() -> Optional[str]:
    """Detect the PulseAudio monitor device for the default output sink.

    The monitor device captures all audio being played (game audio, etc.)
    rather than microphone input. Linux only.

    Returns:
        Monitor device name (e.g., 'alsa_output.pci-xxx.monitor') or None if detection fails
    """
    if IS_WINDOWS:
        return None  # Windows audio handled by WasapiCapture

    try:
        # Get the default output sink name
        result = subprocess.run(
            ["pactl", "get-default-sink"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            sink_name = result.stdout.strip()
            # The monitor source is the sink name + ".monitor"
            return f"{sink_name}.monitor"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


class FFmpegCapture:
    """Manages FFmpeg video capture - x11grab on Linux, gdigrab on Windows."""

    def __init__(
        self,
        display: str,
        output_path: Path,
        resolution: tuple[int, int] = (1920, 1080),
        framerate: int = 60,
        audio_device: Optional[str] = None,
        enable_audio: bool = True,
    ):
        self.display = display
        self.output_path = output_path
        self.resolution = resolution
        self.framerate = framerate
        self.audio_device = audio_device
        self.enable_audio = enable_audio
        self.process: Optional[subprocess.Popen] = None
        self.stderr_path: Optional[Path] = None  # For debugging

    def start(self):
        """Start FFmpeg capture.

        Raises:
            CaptureError: If FFmpeg fails to start
        """
        video_size = f"{self.resolution[0]}x{self.resolution[1]}"

        # Save stderr to file for debugging
        self.stderr_path = self.output_path.parent / f"{self.output_path.stem}_ffmpeg.log"

        ffmpeg_bin = ensure_ffmpeg()

        if IS_WINDOWS:
            cmd = self._build_cmd_windows(ffmpeg_bin, video_size)
        else:
            cmd = self._build_cmd_linux(ffmpeg_bin, video_size)

        try:
            # Write stderr to file for debugging
            self._stderr_file = open(self.stderr_path, 'w')
            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=self._stderr_file,
            )
        except FileNotFoundError:
            raise CaptureError(
                "FFmpeg not found. "
                + ("Run cs2pov once to auto-download." if IS_WINDOWS else "Install with: apt install ffmpeg")
            )

        # Brief check that it started
        time.sleep(0.3)
        if self.process.poll() is not None:
            self._stderr_file.close()
            stderr = self.stderr_path.read_text() if self.stderr_path.exists() else ""
            raise CaptureError(f"FFmpeg failed to start: {stderr}")

    def _build_cmd_linux(self, ffmpeg_bin: str, video_size: str) -> list[str]:
        """Build FFmpeg command for Linux (x11grab + PulseAudio)."""
        input_source = f"{self.display}+0,0"

        # Determine audio device
        audio_source = None
        if self.enable_audio:
            if self.audio_device:
                audio_source = self.audio_device
            else:
                audio_source = get_default_audio_monitor()

        cmd = [
            ffmpeg_bin,
            "-y",
            "-thread_queue_size", "4096",
            "-f", "x11grab",
            "-draw_mouse", "0",
            "-video_size", video_size,
            "-framerate", str(self.framerate),
            "-i", input_source,
        ]

        if audio_source:
            cmd.extend([
                "-thread_queue_size", "4096",
                "-f", "pulse",
                "-i", audio_source,
            ])

        cmd.extend([
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-g", str(self.framerate * 2),
        ])

        if audio_source:
            cmd.extend(["-c:a", "aac", "-b:a", "192k"])

        cmd.extend([
            "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
            str(self.output_path),
        ])
        return cmd

    def _build_cmd_windows(self, ffmpeg_bin: str, video_size: str) -> list[str]:
        """Build FFmpeg command for Windows (gdigrab, no audio — WASAPI handled separately)."""
        cmd = [
            ffmpeg_bin,
            "-y",
            "-f", "gdigrab",
            "-draw_mouse", "0",
            "-video_size", video_size,
            "-framerate", str(self.framerate),
            "-i", "desktop",
            # Video encoding
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "23",
            "-pix_fmt", "yuv420p",
            "-g", str(self.framerate * 2),
            # Output
            "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
            str(self.output_path),
        ]
        return cmd

    def is_running(self) -> bool:
        """Check if FFmpeg is still running."""
        return self.process is not None and self.process.poll() is None

    def stop(self, timeout: float = 30) -> bool:
        """Stop FFmpeg capture gracefully.

        Args:
            timeout: Maximum time to wait for FFmpeg to finish

        Returns:
            True if stopped gracefully, False if force-killed
        """
        if not self.process:
            return True

        graceful = True

        # Send 'q' to quit gracefully (allows proper file finalization)
        if self.process.stdin:
            try:
                self.process.stdin.write(b"q\n")
                self.process.stdin.flush()
                self.process.stdin.close()
            except (BrokenPipeError, OSError):
                pass

        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            graceful = False
            if IS_LINUX:
                # Try SIGINT first (like Ctrl+C) - FFmpeg handles this gracefully
                import signal
                self.process.send_signal(signal.SIGINT)
                try:
                    self.process.wait(timeout=5)
                    graceful = True
                except subprocess.TimeoutExpired:
                    pass

            if not graceful:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()

        # Close stderr file
        if hasattr(self, '_stderr_file') and self._stderr_file:
            try:
                self._stderr_file.close()
            except Exception:
                pass

        self.process = None
        return graceful
