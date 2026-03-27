"""Platform detection and cross-platform constants.

Single source of truth for OS detection. Import IS_WINDOWS / IS_LINUX
from here instead of checking sys.platform directly.
"""

import os
import shutil
import sys
from pathlib import Path

IS_WINDOWS: bool = sys.platform == "win32"
IS_LINUX: bool = sys.platform == "linux"

NULL_PATH: Path = Path("NUL") if IS_WINDOWS else Path("/dev/null")

APPDATA_DIR: Path | None = (
    Path(os.environ.get("APPDATA", "")) / "cs2pov" if IS_WINDOWS else None
)

# Cached paths — populated lazily by ensure_ffmpeg() / ensure_ffprobe()
_ffmpeg_path: str | None = None
_ffprobe_path: str | None = None


def ensure_ffmpeg() -> str:
    """Return the path to an ffmpeg binary, downloading if needed on Windows.

    On Linux, returns "ffmpeg" (expected on PATH).
    On Windows, checks %APPDATA%/cs2pov/ffmpeg/ first, downloads if absent.
    """
    global _ffmpeg_path
    if _ffmpeg_path is not None:
        return _ffmpeg_path

    if IS_LINUX:
        _ffmpeg_path = "ffmpeg"
        return _ffmpeg_path

    # Windows: check for bundled/downloaded ffmpeg
    assert APPDATA_DIR is not None
    local_ffmpeg = APPDATA_DIR / "ffmpeg" / "ffmpeg.exe"
    if local_ffmpeg.is_file():
        _ffmpeg_path = str(local_ffmpeg)
        return _ffmpeg_path

    # Check PATH as fallback before downloading
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        _ffmpeg_path = system_ffmpeg
        return _ffmpeg_path

    # Auto-download
    from .ffmpeg_download import download_ffmpeg

    ffmpeg_dir = download_ffmpeg(APPDATA_DIR / "ffmpeg")
    _ffmpeg_path = str(ffmpeg_dir / "ffmpeg.exe")
    return _ffmpeg_path


def ensure_ffprobe() -> str:
    """Return the path to an ffprobe binary, downloading if needed on Windows.

    On Linux, returns "ffprobe" (expected on PATH).
    On Windows, checks %APPDATA%/cs2pov/ffmpeg/ first, downloads if absent.
    """
    global _ffprobe_path
    if _ffprobe_path is not None:
        return _ffprobe_path

    if IS_LINUX:
        _ffprobe_path = "ffprobe"
        return _ffprobe_path

    # Windows: check for bundled/downloaded ffprobe
    assert APPDATA_DIR is not None
    local_ffprobe = APPDATA_DIR / "ffmpeg" / "ffprobe.exe"
    if local_ffprobe.is_file():
        _ffprobe_path = str(local_ffprobe)
        return _ffprobe_path

    # Check PATH as fallback before downloading
    system_ffprobe = shutil.which("ffprobe")
    if system_ffprobe:
        _ffprobe_path = system_ffprobe
        return _ffprobe_path

    # Auto-download (same archive contains both ffmpeg and ffprobe)
    from .ffmpeg_download import download_ffmpeg

    ffmpeg_dir = download_ffmpeg(APPDATA_DIR / "ffmpeg")
    _ffprobe_path = str(ffmpeg_dir / "ffprobe.exe")
    return _ffprobe_path
