"""Post-processing: trim segments from recorded video."""

import subprocess
import tempfile
from pathlib import Path

from .exceptions import CaptureError


def get_video_duration(video_path: Path) -> float:
    """Get video duration in seconds using ffprobe.

    Args:
        video_path: Path to video file

    Returns:
        Duration in seconds

    Raises:
        CaptureError: If ffprobe fails
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path)
            ],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            return float(result.stdout.strip())
        raise CaptureError(f"ffprobe failed: {result.stderr}")
    except FileNotFoundError:
        raise CaptureError("ffprobe not found (part of ffmpeg)")
    except (ValueError, subprocess.TimeoutExpired) as e:
        raise CaptureError(f"Failed to get video duration: {e}")


def trim_goto_transitions(
    input_path: Path,
    output_path: Path,
    transitions: list,
    verbose: bool = False,
) -> bool:
    """Trim goto transition artifacts from a tick-nav recording.

    When using tick-based navigation, each death triggers a pause/seek/unpause
    cycle. The video between pause_video_time and unpause_video_time contains
    the seek artifact and should be removed.

    This converts transitions into "keep segments" (the gaps between transitions)
    and uses extract_and_concat_segments to do the actual trimming.

    Args:
        input_path: Path to input video
        output_path: Path for output video
        transitions: List of GotoTransition objects
        verbose: Print debug output

    Returns:
        True if trimming was performed, False on failure or nothing to trim
    """
    if not transitions:
        if verbose:
            print("    [Trim] No transitions to trim")
        return False

    # Get video duration
    try:
        video_duration = get_video_duration(input_path)
    except CaptureError as e:
        if verbose:
            print(f"    [Trim] Failed to get video duration: {e}")
        return False

    # Build keep segments from the gaps between transitions
    keep_segments: list[tuple[float, float]] = []
    current_pos = 0.0

    # Sort transitions by pause time
    sorted_transitions = sorted(transitions, key=lambda t: t.pause_video_time)

    for t in sorted_transitions:
        # Keep segment before this transition's pause
        if t.pause_video_time > current_pos + 0.5:
            keep_segments.append((current_pos, t.pause_video_time))

        # Skip past the transition artifact
        current_pos = max(current_pos, t.unpause_video_time)

    # Keep segment after last transition
    if current_pos < video_duration - 0.5:
        keep_segments.append((current_pos, video_duration))

    if not keep_segments:
        if verbose:
            print("    [Trim] No segments to keep after transition removal")
        return False

    if verbose:
        total_keep = sum(end - start for start, end in keep_segments)
        total_trim = video_duration - total_keep
        print(f"    [Trim] {len(transitions)} transitions → {len(keep_segments)} keep segments")
        print(f"    [Trim] Keep: {total_keep:.1f}s, Trim: {total_trim:.1f}s")

    return extract_and_concat_segments(input_path, output_path, keep_segments, verbose)


def extract_and_concat_segments(
    input_path: Path,
    output_path: Path,
    segments: list[tuple[float, float]],
    verbose: bool = False,
) -> bool:
    """Extract and concatenate video segments using FFmpeg.

    This is a simplified interface that takes the segments to KEEP directly,
    rather than computing them from periods to remove.

    Args:
        input_path: Path to input video
        output_path: Path for output video
        segments: List of (start, end) tuples in video time (seconds)
        verbose: Print debug output

    Returns:
        True if trimming was performed, False on failure
    """
    if not segments:
        if verbose:
            print("    [Trim] No segments to extract")
        return False

    # Sort segments by start time
    segments = sorted(segments, key=lambda s: s[0])

    if verbose:
        total_duration = sum(end - start for start, end in segments)
        print(f"    [Trim] Extracting {len(segments)} segments, total duration: {total_duration:.2f}s")

    # Create concat file and segment files in temp directory
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        concat_file = tmpdir_path / "concat.txt"
        segment_paths: list[Path] = []

        # Extract each segment
        for i, (start, end) in enumerate(segments):
            segment_path = tmpdir_path / f"segment_{i:03d}.mp4"
            segment_paths.append(segment_path)

            duration = end - start
            if verbose:
                print(f"    [Trim] Extracting segment {i}: {start:.2f}s - {end:.2f}s ({duration:.2f}s)")

            cmd = [
                "ffmpeg",
                "-y",
                "-ss", str(start),
                "-i", str(input_path),
                "-t", str(duration),
                "-c", "copy",  # No re-encoding
                "-avoid_negative_ts", "make_zero",
                str(segment_path)
            ]

            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=300  # 5 min per segment
                )
                if result.returncode != 0:
                    if verbose:
                        print(f"    [Trim] Segment extraction failed: {result.stderr}")
                    return False
            except subprocess.TimeoutExpired:
                if verbose:
                    print("    [Trim] Segment extraction timed out")
                return False

        # Create concat file
        with open(concat_file, 'w') as f:
            for segment_path in segment_paths:
                # Escape single quotes in path
                escaped_path = str(segment_path).replace("'", "'\\''")
                f.write(f"file '{escaped_path}'\n")

        if verbose:
            print(f"    [Trim] Concatenating {len(segment_paths)} segments")

        # Concatenate segments
        cmd = [
            "ffmpeg",
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",
            str(output_path)
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600  # 10 min for concat
            )
            if result.returncode != 0:
                if verbose:
                    print(f"    [Trim] Concatenation failed: {result.stderr}")
                return False
        except subprocess.TimeoutExpired:
            if verbose:
                print("    [Trim] Concatenation timed out")
            return False

    if verbose:
        if output_path.exists():
            output_duration = get_video_duration(output_path)
            print(f"    [Trim] Output video: {output_duration:.2f}s")

    return True
