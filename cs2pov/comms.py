"""Comms audio overlay: trim external audio to match alive segments, then mix onto video."""

import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from .preprocessor import AliveSegment, DemoTimeline


def compute_comms_segments(
    alive_segments: list[AliveSegment],
    round1_freeze_end_time: float,
    r1_sync_time: float = 0.0,
) -> list[tuple[float, float]]:
    """Map alive segments from demo time to comms audio time.

    Comms time = demo_time - round1_freeze_end_time + r1_sync_time.
    r1_sync_time is the timestamp in the comms audio where round 1 starts.
    Segments before comms t=0 are clamped or dropped.

    Args:
        alive_segments: Alive segments from DemoTimeline
        round1_freeze_end_time: Demo time (seconds) of round 1 freeze end
        r1_sync_time: Seconds into comms audio where round 1 begins

    Returns:
        List of (start, end) tuples in comms audio time (seconds)
    """
    segments = []
    for seg in alive_segments:
        comms_start = seg.start_time - round1_freeze_end_time + r1_sync_time
        comms_end = seg.end_time - round1_freeze_end_time + r1_sync_time

        if comms_end <= 0:
            continue
        comms_start = max(0.0, comms_start)
        segments.append((comms_start, comms_end))

    return segments


def _get_round1_freeze_end_time(timeline: DemoTimeline) -> Optional[float]:
    """Get round 1 freeze_end time from timeline.

    Returns the first round's freeze_end_time, skipping rounds without it.
    """
    for r in timeline.rounds:
        if r.freeze_end_time is not None:
            return r.freeze_end_time
    return None


def _has_audio_stream(video_path: Path) -> bool:
    """Check if video file has an audio stream."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-select_streams", "a",
                "-show_entries", "stream=codec_type",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0 and result.stdout.strip() != ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def extract_comms_segments(
    comms_audio_path: Path,
    segments: list[tuple[float, float]],
    output_path: Path,
    verbose: bool = False,
) -> bool:
    """Extract and concatenate segments from comms audio using FFmpeg.

    Extracts each segment as a WAV intermediate, then concatenates them.

    Args:
        comms_audio_path: Path to input comms audio file
        segments: List of (start, end) in comms audio seconds
        output_path: Path for trimmed output audio (WAV)
        verbose: Print debug info

    Returns:
        True on success
    """
    if not segments:
        if verbose:
            print("    [Comms] No segments to extract")
        return False

    segments = sorted(segments, key=lambda s: s[0])

    if verbose:
        total_duration = sum(end - start for start, end in segments)
        print(f"    [Comms] Extracting {len(segments)} audio segments, total: {total_duration:.2f}s")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        concat_file = tmpdir_path / "concat.txt"
        segment_paths: list[Path] = []

        for i, (start, end) in enumerate(segments):
            segment_path = tmpdir_path / f"segment_{i:03d}.wav"
            segment_paths.append(segment_path)
            duration = end - start

            if verbose:
                print(f"    [Comms] Segment {i}: {start:.2f}s - {end:.2f}s ({duration:.2f}s)")

            cmd = [
                "ffmpeg", "-y",
                "-ss", str(start),
                "-i", str(comms_audio_path),
                "-t", str(duration),
                "-c:a", "pcm_s16le",
                "-ar", "48000",
                "-ac", "2",
                str(segment_path),
            ]

            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                if result.returncode != 0:
                    if verbose:
                        print(f"    [Comms] Segment extraction failed: {result.stderr}")
                    return False
            except subprocess.TimeoutExpired:
                if verbose:
                    print("    [Comms] Segment extraction timed out")
                return False

        with open(concat_file, "w") as f:
            for segment_path in segment_paths:
                escaped_path = str(segment_path).replace("'", "'\\''")
                f.write(f"file '{escaped_path}'\n")

        if verbose:
            print(f"    [Comms] Concatenating {len(segment_paths)} audio segments")

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-c:a", "pcm_s16le",
            str(output_path),
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                if verbose:
                    print(f"    [Comms] Concatenation failed: {result.stderr}")
                return False
        except subprocess.TimeoutExpired:
            if verbose:
                print("    [Comms] Concatenation timed out")
            return False

    return True


def overlay_comms_on_video(
    video_path: Path,
    comms_audio_path: Path,
    output_path: Path,
    game_volume: float = 1.0,
    comms_volume: float = 1.0,
    verbose: bool = False,
) -> bool:
    """Mix comms audio onto video's existing game audio using FFmpeg amix.

    Video stream is copied without re-encoding. Audio is re-encoded to AAC.
    If the video has no audio track, comms are added as the sole audio.

    Args:
        video_path: Input video (with or without game audio)
        comms_audio_path: Comms audio (already trimmed to match video timeline)
        output_path: Output video path
        game_volume: Volume multiplier for game audio (default 1.0)
        comms_volume: Volume multiplier for comms audio (default 1.0)
        verbose: Print debug info

    Returns:
        True on success
    """
    has_audio = _has_audio_stream(video_path)

    if has_audio:
        # Mix game audio + comms audio
        filter_complex = (
            f"[0:a]volume={game_volume}[game];"
            f"[1:a]volume={comms_volume}[comms];"
            f"[game][comms]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(comms_audio_path),
            "-filter_complex", filter_complex,
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            str(output_path),
        ]
    else:
        # No game audio — use comms as sole audio track
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(comms_audio_path),
            "-map", "0:v",
            "-map", "1:a",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            str(output_path),
        ]

    if verbose:
        mode = "mixing with game audio" if has_audio else "adding as sole audio"
        print(f"    [Comms] Overlaying comms ({mode})")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            if verbose:
                print(f"    [Comms] Overlay failed: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        if verbose:
            print("    [Comms] Overlay timed out")
        return False

    return True


def apply_comms_overlay(
    video_path: Path,
    comms_audio_path: Path,
    output_path: Path,
    timeline: Optional[DemoTimeline] = None,
    r1_sync_time: float = 0.0,
    game_volume: float = 1.0,
    comms_volume: float = 1.0,
    is_trimmed: bool = True,
    verbose: bool = False,
) -> bool:
    """High-level entry point: trim comms to match alive segments, then overlay onto video.

    If the video is trimmed and a timeline is provided, the comms audio is cut
    to match the same alive segments so it stays in sync. Otherwise, the comms
    are overlaid directly with an offset seek.

    Args:
        video_path: Input video (trimmed or raw)
        comms_audio_path: External comms audio file
        output_path: Final output path
        timeline: DemoTimeline with alive segments and rounds
        r1_sync_time: Seconds into comms audio where round 1 begins
        game_volume: Game audio volume (default 1.0)
        comms_volume: Comms audio volume (default 1.0)
        is_trimmed: Whether the video has been trimmed
        verbose: Print debug info

    Returns:
        True on success
    """
    if is_trimmed and timeline and timeline.alive_segments:
        # Get round 1 freeze-end time as reference
        round1_time = _get_round1_freeze_end_time(timeline)
        if round1_time is None:
            print("  Warning: No round data found, using demo start as reference")
            round1_time = 0.0

        # Compute which parts of comms audio correspond to alive segments
        comms_segments = compute_comms_segments(
            timeline.alive_segments, round1_time, r1_sync_time
        )

        if not comms_segments:
            print("  Warning: No comms segments to extract (all before comms start)")
            return False

        if verbose:
            print(f"    [Comms] {len(comms_segments)} segments mapped from {len(timeline.alive_segments)} alive segments")

        # Extract and concat the matching comms audio segments
        with tempfile.TemporaryDirectory() as tmpdir:
            trimmed_comms = Path(tmpdir) / "comms_trimmed.wav"

            if not extract_comms_segments(comms_audio_path, comms_segments, trimmed_comms, verbose):
                print("  Error: Failed to extract comms segments")
                return False

            # Overlay trimmed comms onto trimmed video
            return overlay_comms_on_video(
                video_path, trimmed_comms, output_path,
                game_volume, comms_volume, verbose,
            )
    else:
        # Untrimmed or no timeline — overlay directly with offset
        if verbose:
            print(f"    [Comms] Direct overlay (r1_sync_time: {r1_sync_time}s)")

        # For direct overlay with offset, seek into comms audio
        has_audio = _has_audio_stream(video_path)

        if has_audio:
            filter_complex = (
                f"[0:a]volume={game_volume}[game];"
                f"[1:a]volume={comms_volume}[comms];"
                f"[game][comms]amix=inputs=2:duration=first:dropout_transition=0[aout]"
            )
            cmd = [
                "ffmpeg", "-y",
                "-i", str(video_path),
                "-ss", str(max(0.0, r1_sync_time)),
                "-i", str(comms_audio_path),
                "-filter_complex", filter_complex,
                "-map", "0:v",
                "-map", "[aout]",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                str(output_path),
            ]
        else:
            cmd = [
                "ffmpeg", "-y",
                "-i", str(video_path),
                "-ss", str(max(0.0, r1_sync_time)),
                "-i", str(comms_audio_path),
                "-map", "0:v",
                "-map", "1:a",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "192k",
                str(output_path),
            ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                if verbose:
                    print(f"    [Comms] Direct overlay failed: {result.stderr}")
                return False
        except subprocess.TimeoutExpired:
            if verbose:
                print("    [Comms] Direct overlay timed out")
            return False

        return True
