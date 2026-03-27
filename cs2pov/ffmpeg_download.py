"""Auto-download static FFmpeg build for Windows.

Downloads from BtbN/FFmpeg-Builds GitHub releases and extracts
ffmpeg.exe + ffprobe.exe to the target directory.
"""

import io
import urllib.request
import zipfile
from pathlib import Path

FFMPEG_RELEASE_URL = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/"
    "latest/ffmpeg-master-latest-win64-gpl.zip"
)


def download_ffmpeg(target_dir: Path) -> Path:
    """Download a static FFmpeg build and extract binaries.

    Args:
        target_dir: Directory to place ffmpeg.exe and ffprobe.exe.

    Returns:
        The target_dir path (containing ffmpeg.exe and ffprobe.exe).
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    ffmpeg_exe = target_dir / "ffmpeg.exe"
    ffprobe_exe = target_dir / "ffprobe.exe"

    if ffmpeg_exe.is_file() and ffprobe_exe.is_file():
        return target_dir

    print("FFmpeg not found. Downloading static build (this is a one-time setup)...")

    try:
        req = urllib.request.Request(
            FFMPEG_RELEASE_URL,
            headers={"User-Agent": "cs2pov"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = resp.headers.get("Content-Length")
            if total:
                total = int(total)

            data = bytearray()
            chunk_size = 1024 * 256  # 256 KB chunks
            downloaded = 0

            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                data.extend(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    mb = downloaded / (1024 * 1024)
                    total_mb = total / (1024 * 1024)
                    print(f"\r  Downloading: {mb:.1f}/{total_mb:.1f} MB ({pct}%)", end="", flush=True)
                else:
                    mb = downloaded / (1024 * 1024)
                    print(f"\r  Downloading: {mb:.1f} MB", end="", flush=True)

            print()  # newline after progress

    except Exception as e:
        raise RuntimeError(
            f"Failed to download FFmpeg: {e}\n"
            f"You can manually download from {FFMPEG_RELEASE_URL}\n"
            f"and place ffmpeg.exe + ffprobe.exe in: {target_dir}"
        ) from e

    print("  Extracting ffmpeg.exe and ffprobe.exe...")

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            # The zip contains a top-level directory like ffmpeg-master-latest-win64-gpl/
            # with bin/ffmpeg.exe and bin/ffprobe.exe inside
            extracted = {"ffmpeg.exe": False, "ffprobe.exe": False}

            for member in zf.namelist():
                basename = Path(member).name
                if basename in extracted and not extracted[basename]:
                    with zf.open(member) as src, open(target_dir / basename, "wb") as dst:
                        dst.write(src.read())
                    extracted[basename] = True

                if all(extracted.values()):
                    break

            if not all(extracted.values()):
                missing = [k for k, v in extracted.items() if not v]
                raise RuntimeError(f"Could not find {missing} in the downloaded archive")

    except zipfile.BadZipFile as e:
        raise RuntimeError(f"Downloaded file is not a valid zip archive: {e}") from e

    print(f"  FFmpeg installed to: {target_dir}")
    return target_dir
