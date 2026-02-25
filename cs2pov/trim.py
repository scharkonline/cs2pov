"""Post-processing: trim death periods from recorded video."""

import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from .exceptions import CaptureError

if TYPE_CHECKING:
    from .preprocessor import DemoTimeline


@dataclass
class DeathPeriod:
    """A period when the player was dead."""
    death_time: float  # Video timestamp in seconds
    respawn_time: float  # Video timestamp in seconds

    @property
    def duration(self) -> float:
        return self.respawn_time - self.death_time


@dataclass
class TrimPeriod:
    """A period to trim from the video."""
    start_time: float  # Video timestamp in seconds
    end_time: float  # Video timestamp in seconds

    @property
    def duration(self) -> float:
        return self.end_time - self.start_time


def parse_log_timestamp(timestamp_str: str, reference_date: Optional[datetime] = None) -> datetime:
    """Parse a console.log timestamp in MM/DD HH:mm:ss format.

    Args:
        timestamp_str: Timestamp string like "01/28 15:30:45"
        reference_date: Reference date for year (defaults to current year)

    Returns:
        datetime object
    """
    if reference_date is None:
        reference_date = datetime.now()

    # Parse MM/DD HH:mm:ss
    parsed = datetime.strptime(timestamp_str, "%m/%d %H:%M:%S")
    # Add the year from reference date
    return parsed.replace(year=reference_date.year)


def extract_death_periods(
    log_path: Path,
    player_slot: int,
    recording_start_time: float,
    verbose: bool = False
) -> list[DeathPeriod]:
    """Extract death periods from console.log.

    Finds periods between "Shutdown prediction for player slot X" and
    "Added TrueView prediction for player slot X" lines.

    Also trims from the start of the recording until the player's POV is
    first selected (first TrueView prediction).

    Args:
        log_path: Path to console.log file
        player_slot: Player slot index (0-based)
        recording_start_time: Unix timestamp when recording started
        verbose: Print debug output

    Returns:
        List of DeathPeriod objects with video-relative timestamps
    """
    if not log_path.exists():
        if verbose:
            print(f"    [Trim] Console log not found: {log_path}")
        return []

    # Patterns for death and respawn
    # Format: MM/DD HH:mm:ss [Prediction] Shutdown prediction for player slot X. ...
    death_pattern = re.compile(
        rf"^(\d{{2}}/\d{{2}} \d{{2}}:\d{{2}}:\d{{2}}).*"
        rf"\[Prediction\] Shutdown prediction for player slot {player_slot}\b"
    )
    respawn_pattern = re.compile(
        rf"^(\d{{2}}/\d{{2}} \d{{2}}:\d{{2}}:\d{{2}}).*"
        rf"\[Prediction\] Added TrueView prediction for player slot {player_slot}\b"
    )

    # Reference date for parsing (use recording start time)
    reference_date = datetime.fromtimestamp(recording_start_time)

    death_periods: list[DeathPeriod] = []
    pending_death_time: Optional[float] = None
    first_pov_time: Optional[float] = None  # Track first time POV is selected

    if verbose:
        print(f"    [Trim] Parsing log: {log_path}")
        print(f"    [Trim] Looking for player slot {player_slot}")

    with open(log_path, 'r', errors='ignore') as f:
        for line in f:
            # Check for death
            death_match = death_pattern.match(line)
            if death_match:
                timestamp_str = death_match.group(1)
                try:
                    log_time = parse_log_timestamp(timestamp_str, reference_date)
                    video_time = log_time.timestamp() - recording_start_time
                    if video_time >= 0:  # Only consider events after recording started
                        pending_death_time = video_time
                        if verbose:
                            print(f"    [Trim] Death at video time {video_time:.2f}s")
                except ValueError as e:
                    if verbose:
                        print(f"    [Trim] Failed to parse timestamp: {timestamp_str}: {e}")
                continue

            # Check for respawn/POV selection
            respawn_match = respawn_pattern.match(line)
            if respawn_match:
                timestamp_str = respawn_match.group(1)
                try:
                    log_time = parse_log_timestamp(timestamp_str, reference_date)
                    video_time = log_time.timestamp() - recording_start_time

                    # Track first POV selection time
                    if first_pov_time is None and video_time >= 0:
                        first_pov_time = video_time
                        if verbose:
                            print(f"    [Trim] First POV selection at video time {video_time:.2f}s")

                    # Handle respawn after death
                    if pending_death_time is not None and video_time > pending_death_time:
                        death_periods.append(DeathPeriod(
                            death_time=pending_death_time,
                            respawn_time=video_time
                        ))
                        if verbose:
                            print(f"    [Trim] Respawn at video time {video_time:.2f}s "
                                  f"(dead for {video_time - pending_death_time:.2f}s)")
                    pending_death_time = None
                except ValueError as e:
                    if verbose:
                        print(f"    [Trim] Failed to parse timestamp: {timestamp_str}: {e}")

    # Add initial period from start until first POV selection
    if first_pov_time is not None and first_pov_time > 0.5:  # Only if > 0.5s to avoid tiny trims
        death_periods.insert(0, DeathPeriod(
            death_time=0.0,
            respawn_time=first_pov_time
        ))
        if verbose:
            print(f"    [Trim] Adding start trim: 0.0s - {first_pov_time:.2f}s")

    if verbose:
        print(f"    [Trim] Found {len(death_periods)} periods to trim")

    return death_periods


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


def trim_death_periods(
    input_path: Path,
    output_path: Path,
    death_periods: list[DeathPeriod],
    verbose: bool = False
) -> bool:
    """Trim death periods from video using FFmpeg concat demuxer.

    Creates segments for "alive" periods and concatenates them.

    Args:
        input_path: Path to input video
        output_path: Path for output video
        death_periods: List of death periods to remove
        verbose: Print debug output

    Returns:
        True if trimming was performed, False if no trimming needed
    """
    if not death_periods:
        if verbose:
            print("    [Trim] No death periods to trim")
        return False

    # Get video duration
    try:
        video_duration = get_video_duration(input_path)
    except CaptureError as e:
        if verbose:
            print(f"    [Trim] Failed to get video duration: {e}")
        return False

    if verbose:
        print(f"    [Trim] Video duration: {video_duration:.2f}s")

    # Sort death periods by start time
    death_periods = sorted(death_periods, key=lambda p: p.death_time)

    # Calculate "alive" segments (inverse of death periods)
    alive_segments: list[tuple[float, float]] = []
    current_pos = 0.0

    for period in death_periods:
        # Skip if death starts before current position (overlapping)
        if period.death_time <= current_pos:
            current_pos = max(current_pos, period.respawn_time)
            continue

        # Add alive segment before this death
        if period.death_time > current_pos:
            alive_segments.append((current_pos, period.death_time))

        current_pos = period.respawn_time

    # Add final segment after last death
    if current_pos < video_duration:
        alive_segments.append((current_pos, video_duration))

    if not alive_segments:
        if verbose:
            print("    [Trim] No alive segments found")
        return False

    if verbose:
        print(f"    [Trim] Found {len(alive_segments)} alive segments")
        total_alive = sum(end - start for start, end in alive_segments)
        total_dead = sum(p.duration for p in death_periods)
        print(f"    [Trim] Total alive time: {total_alive:.2f}s, dead time: {total_dead:.2f}s")

    # Create concat file and segment files in temp directory
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        concat_file = tmpdir_path / "concat.txt"
        segment_paths: list[Path] = []

        # Extract each alive segment
        for i, (start, end) in enumerate(alive_segments):
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
                    print(f"    [Trim] Segment extraction timed out")
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
                print(f"    [Trim] Concatenation timed out")
            return False

    if verbose:
        if output_path.exists():
            output_duration = get_video_duration(output_path)
            print(f"    [Trim] Output video: {output_duration:.2f}s")

    return True


def get_trim_periods_from_timeline(
    timeline: "DemoTimeline",
    first_spawn_video_time: float,
    verbose: bool = False,
) -> list[TrimPeriod]:
    """Get trim periods from preprocessed timeline data.

    Uses the timeline's death/spawn events to compute trim periods.
    The first_spawn_video_time parameter tells us when the first spawn
    occurred in the video, allowing us to align demo times with video times.

    Args:
        timeline: Preprocessed DemoTimeline with death/spawn events
        first_spawn_video_time: Video timestamp when first spawn occurred
        verbose: Print debug output

    Returns:
        List of TrimPeriod objects with video-relative timestamps
    """
    from .preprocessor import get_trim_periods_for_video

    trim_periods = []

    # Get demo-relative trim periods and convert to video-relative
    video_periods = get_trim_periods_for_video(timeline, first_spawn_video_time)

    for start, end in video_periods:
        trim_periods.append(TrimPeriod(start_time=start, end_time=end))
        if verbose:
            print(f"    [Trim] Period: {start:.2f}s - {end:.2f}s ({end - start:.2f}s)")

    if verbose:
        print(f"    [Trim] Found {len(trim_periods)} periods to trim from timeline")

    return trim_periods


def trim_video_with_periods(
    input_path: Path,
    output_path: Path,
    trim_periods: list[TrimPeriod],
    verbose: bool = False,
) -> bool:
    """Trim specified periods from video using FFmpeg concat demuxer.

    This is a generalized version that takes TrimPeriod objects directly.
    Creates segments for periods to keep and concatenates them.

    Args:
        input_path: Path to input video
        output_path: Path for output video
        trim_periods: List of periods to remove from video
        verbose: Print debug output

    Returns:
        True if trimming was performed, False if no trimming needed
    """
    if not trim_periods:
        if verbose:
            print("    [Trim] No periods to trim")
        return False

    # Convert TrimPeriod to DeathPeriod for compatibility with existing logic
    death_periods = [
        DeathPeriod(death_time=p.start_time, respawn_time=p.end_time)
        for p in trim_periods
    ]

    return trim_death_periods(input_path, output_path, death_periods, verbose)


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
