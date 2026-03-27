"""WASAPI loopback audio capture for Windows.

Captures system audio (game audio) via sounddevice with WASAPI loopback,
records to a temporary WAV file, and provides muxing with video via FFmpeg.
"""

import subprocess
import tempfile
import threading
import wave
from pathlib import Path
from typing import Optional

import numpy as np


class WasapiCapture:
    """Capture system audio via WASAPI loopback on Windows.

    Usage:
        capture = WasapiCapture()
        capture.start()
        # ... record video ...
        capture.stop()
        capture.mux_audio(video_path)  # combines audio + video
    """

    def __init__(self, sample_rate: int = 44100, channels: int = 2):
        self.sample_rate = sample_rate
        self.channels = channels
        self._audio_path: Optional[Path] = None
        self._stream = None
        self._frames: list[np.ndarray] = []
        self._recording = False
        self._lock = threading.Lock()

    @property
    def audio_path(self) -> Optional[Path]:
        """Path to the recorded audio file, available after stop()."""
        return self._audio_path

    def start(self) -> Path:
        """Start capturing audio. Returns the path where audio will be saved."""
        import sounddevice as sd

        # Create temp file for output
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="cs2pov_audio_")
        import os
        os.close(fd)
        self._audio_path = Path(path)
        self._frames = []
        self._recording = True

        # Get default output device for loopback
        device = sd.query_devices(kind="output")
        device_index = device["index"]

        # Use actual device sample rate for best compatibility
        actual_sr = int(device["default_samplerate"])
        if actual_sr > 0:
            self.sample_rate = actual_sr

        actual_channels = int(device["max_output_channels"])
        if actual_channels > 0:
            self.channels = min(self.channels, actual_channels)

        def callback(indata, frames, time_info, status):
            if self._recording:
                with self._lock:
                    self._frames.append(indata.copy())

        self._stream = sd.InputStream(
            device=device_index,
            channels=self.channels,
            samplerate=self.sample_rate,
            dtype="float32",
            callback=callback,
            extra_settings=sd.WasapiSettings(loopback=True),
        )
        self._stream.start()

        print(f"    WASAPI loopback capture started (device: {device['name']})")
        return self._audio_path

    def stop(self):
        """Stop capturing and write audio to WAV file."""
        self._recording = False

        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        if not self._frames or self._audio_path is None:
            return

        # Concatenate all frames and write WAV
        with self._lock:
            audio_data = np.concatenate(self._frames, axis=0)
            self._frames = []

        # Convert float32 [-1.0, 1.0] to int16
        audio_int16 = np.clip(audio_data * 32767, -32768, 32767).astype(np.int16)

        with wave.open(str(self._audio_path), "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(self.sample_rate)
            wf.writeframes(audio_int16.tobytes())

        print(f"    Audio saved: {self._audio_path} ({len(audio_data) / self.sample_rate:.1f}s)")

    def mux_audio(self, video_path: Path, ffmpeg_path: str = "ffmpeg") -> bool:
        """Mux the captured audio into the video file.

        Replaces the video file with a version that includes audio.

        Args:
            video_path: Path to the video file (will be modified in place).
            ffmpeg_path: Path to ffmpeg binary.

        Returns:
            True if muxing succeeded.
        """
        if self._audio_path is None or not self._audio_path.is_file():
            return False

        if not video_path.is_file():
            return False

        # Output to a temp file, then replace original
        temp_output = video_path.with_suffix(".muxed.mp4")

        cmd = [
            ffmpeg_path, "-y",
            "-i", str(video_path),
            "-i", str(self._audio_path),
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(temp_output),
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300,
            )
            if result.returncode != 0:
                print(f"    Audio mux failed: {result.stderr[:200]}")
                temp_output.unlink(missing_ok=True)
                return False

            # Replace original with muxed version
            video_path.unlink()
            temp_output.rename(video_path)
            print(f"    Audio muxed into: {video_path}")
            return True
        except Exception as e:
            print(f"    Audio mux error: {e}")
            temp_output.unlink(missing_ok=True)
            return False

    def cleanup(self):
        """Remove temporary audio file."""
        if self._audio_path and self._audio_path.is_file():
            try:
                self._audio_path.unlink()
            except Exception:
                pass
