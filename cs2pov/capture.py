"""FFmpeg video and audio capture - simplified, full-display only."""

import signal
import subprocess
import time
from pathlib import Path
from typing import Optional

from .exceptions import CaptureError


def get_default_audio_monitor() -> Optional[str]:
    """Detect the PulseAudio monitor device for the default output sink.

    The monitor device captures all audio being played (game audio, etc.)
    rather than microphone input.

    Returns:
        Monitor device name (e.g., 'alsa_output.pci-xxx.monitor') or None if detection fails
    """
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
    """Manages FFmpeg x11grab video and PulseAudio capture - full display only."""

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

        # Always capture full display at origin
        # CS2 runs fullscreen, and window-specific capture breaks when geometry changes
        input_source = f"{self.display}+0,0"

        # Save stderr to file for debugging
        self.stderr_path = self.output_path.parent / f"{self.output_path.stem}_ffmpeg.log"

        # Determine audio device if audio is enabled
        audio_source = None
        if self.enable_audio:
            if self.audio_device:
                audio_source = self.audio_device
            else:
                # Auto-detect the monitor device for the default output sink
                audio_source = get_default_audio_monitor()

        # Fragmented MP4 for resilience - playable even if interrupted
        # Large thread_queue_size to prevent frame drops
        # Keyframe every 2 seconds for better seeking/trimming
        cmd = [
            "ffmpeg",
            "-y",  # Overwrite output
            # Video input (x11grab)
            "-thread_queue_size", "4096",  # Large buffer
            "-f", "x11grab",
            "-draw_mouse", "0",  # Hide cursor
            "-video_size", video_size,
            "-framerate", str(self.framerate),
            "-i", input_source,
        ]

        # Add audio input if available
        if audio_source:
            cmd.extend([
                # Audio input (PulseAudio)
                "-thread_queue_size", "4096",
                "-f", "pulse",
                "-i", audio_source,
            ])

        # Video encoding settings
        cmd.extend([
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-g", str(self.framerate * 2),  # Keyframe every 2 seconds
        ])

        # Audio encoding settings (if audio source available)
        if audio_source:
            cmd.extend([
                "-c:a", "aac",
                "-b:a", "192k",
            ])

        # Output settings
        cmd.extend([
            "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
            str(self.output_path),
        ])

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
            raise CaptureError("FFmpeg not found. Install with: apt install ffmpeg")

        # Brief check that it started
        time.sleep(0.3)
        if self.process.poll() is not None:
            self._stderr_file.close()
            stderr = self.stderr_path.read_text() if self.stderr_path.exists() else ""
            raise CaptureError(f"FFmpeg failed to start: {stderr}")

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
            # Try SIGINT first (like Ctrl+C) - FFmpeg handles this gracefully
            self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(timeout=5)
                graceful = True
            except subprocess.TimeoutExpired:
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
