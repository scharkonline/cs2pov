"""Post-processing: trim segments from recorded video."""

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from .exceptions import CaptureError


def _transitions_path(video_path: Path) -> Path:
    """Return the sidecar transitions JSON path for a video."""
    return video_path.parent / f"{video_path.stem}_transitions.json"


def save_transitions(transitions: list, video_path: Path) -> Path:
    """Save GotoTransition list to a sidecar JSON file alongside the video.

    Args:
        transitions: List of GotoTransition objects
        video_path: Path to the video file

    Returns:
        Path to the saved transitions file
    """
    path = _transitions_path(video_path)
    data = [
        {
            "pause_video_time": t.pause_video_time,
            "unpause_video_time": t.unpause_video_time,
            "from_tick": t.from_tick,
            "to_tick": t.to_tick,
        }
        for t in transitions
    ]
    path.write_text(json.dumps(data, indent=2))
    return path


def load_transitions(video_path: Path) -> Optional[list]:
    """Load GotoTransition list from a sidecar JSON file.

    Args:
        video_path: Path to the video file

    Returns:
        List of GotoTransition objects, or None if no sidecar file exists
    """
    from .navigation import GotoTransition

    path = _transitions_path(video_path)
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    if not isinstance(data, list) or not data:
        return None

    return [
        GotoTransition(
            pause_video_time=t["pause_video_time"],
            unpause_video_time=t["unpause_video_time"],
            from_tick=t["from_tick"],
            to_tick=t["to_tick"],
        )
        for t in data
    ]


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
) -> Optional[list[tuple[float, float]]]:
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
        List of keep segments on success, None on failure or nothing to trim
    """
    if not transitions:
        if verbose:
            print("    [Trim] No transitions to trim")
        return None

    # Get video duration
    try:
        video_duration = get_video_duration(input_path)
    except CaptureError as e:
        if verbose:
            print(f"    [Trim] Failed to get video duration: {e}")
        return None

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
        return None

    if verbose:
        total_keep = sum(end - start for start, end in keep_segments)
        total_trim = video_duration - total_keep
        print(f"    [Trim] {len(transitions)} transitions → {len(keep_segments)} keep segments")
        print(f"    [Trim] Keep: {total_keep:.1f}s, Trim: {total_trim:.1f}s")

    success = extract_and_concat_segments(input_path, output_path, keep_segments, verbose)
    return keep_segments if success else None


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


def reconstruct_transitions(alive_segments, video_duration: float) -> list:
    """Reconstruct GotoTransitions from alive segment durations and video duration.

    For tick-nav recordings without a sidecar file, we know:
    - Each alive segment played for its full duration
    - Between segments are goto artifacts of unknown but uniform duration
    - total_artifact_time = video_duration - sum(segment durations)

    Args:
        alive_segments: List of AliveSegment objects from DemoTimeline
        video_duration: Total video duration in seconds

    Returns:
        List of GotoTransition objects (empty if <= 1 segment)
    """
    from .navigation import GotoTransition

    if len(alive_segments) <= 1:
        return []

    total_segment_time = sum(
        seg.end_time - seg.start_time for seg in alive_segments
    )
    num_gaps = len(alive_segments) - 1
    total_artifact_time = max(0.0, video_duration - total_segment_time)
    artifact_per_gap = total_artifact_time / num_gaps

    transitions = []
    pos = 0.0
    for i, seg in enumerate(alive_segments[:-1]):
        seg_duration = seg.end_time - seg.start_time
        pause_time = pos + seg_duration
        unpause_time = pause_time + artifact_per_gap
        transitions.append(GotoTransition(
            pause_video_time=pause_time,
            unpause_video_time=unpause_time,
            from_tick=seg.end_tick,
            to_tick=alive_segments[i + 1].start_tick,
        ))
        pos = unpause_time

    return transitions


def compute_keep_segments(
    alive_segments,
    video_duration: float,
    demo_duration: float,
    startup_time_override: Optional[float] = None,
) -> list[tuple[float, float]]:
    """Convert alive segments to video-time keep segments.

    Args:
        alive_segments: List of AliveSegment objects from DemoTimeline
        video_duration: Total video duration in seconds
        demo_duration: Total demo duration in seconds
        startup_time_override: Manual startup time override

    Returns:
        List of (start, end) tuples in video time (seconds)
    """
    if startup_time_override is not None:
        startup_time = startup_time_override
    else:
        startup_time = max(0.0, video_duration - demo_duration)

    segments = []
    for seg in alive_segments:
        start = max(0.0, startup_time + seg.start_time)
        end = min(video_duration, startup_time + seg.end_time)
        if end > start + 0.5:
            segments.append((start, end))
    return segments
