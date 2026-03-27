"""Comms audio overlay: trim external audio to match alive segments, then mix onto video."""

import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from .platform import ensure_ffmpeg, ensure_ffprobe
from .preprocessor import AliveSegment, DemoTimeline, RoundBoundary


def compute_comms_segments(
    alive_segments: list[AliveSegment],
    rounds: list[RoundBoundary],
    round1_freeze_end_time: float,
    r1_sync_time: float = 0.0,
    tick_nav: bool = False,
    keep_durations: Optional[list[float]] = None,
) -> list[tuple[float, float]]:
    """Map alive segments from demo time to comms audio time.

    Comms time = demo_time - round1_freeze_end_time + r1_sync_time.
    r1_sync_time is the timestamp in the comms audio where round 1 starts.
    Segments before comms t=0 are clamped or dropped.

    When keep_durations is provided (from trim's actual keep segments), those
    durations override the computed end times. This ensures comms segments
    match the exact durations in the trimmed video.

    When tick_nav=True and keep_durations is not available, segment end times
    are extended to match the actual navigation behavior as a fallback.

    Args:
        alive_segments: Alive segments from DemoTimeline
        rounds: Round boundaries from DemoTimeline
        round1_freeze_end_time: Demo time (seconds) of round 1 freeze end
        r1_sync_time: Seconds into comms audio where round 1 begins
        tick_nav: Whether tick-nav mode was used (extends segment boundaries)
        keep_durations: Durations from trim's actual keep segments (overrides end time calc)

    Returns:
        List of (start, end) tuples in comms audio time (seconds)
    """
    segments = []
    for i, seg in enumerate(alive_segments):
        comms_start = seg.start_time - round1_freeze_end_time + r1_sync_time

        if keep_durations and i < len(keep_durations):
            # Use trim's actual duration — guarantees sync with trimmed video
            comms_end = comms_start + keep_durations[i]
        else:
            # Fallback: compute end from demo timeline
            end_time = seg.end_time

            if tick_nav:
                # Match navigation.py segment duration extensions
                if seg.reason_ended == "round_end":
                    next_seg = alive_segments[i + 1] if i + 1 < len(alive_segments) else None
                    if next_seg:
                        for r in rounds:
                            if r.round_num == next_seg.round_num and r.prestart_time is not None:
                                end_time = r.prestart_time - 0.2
                                break
                elif seg.reason_ended == "death":
                    end_time += 0.9

            comms_end = end_time - round1_freeze_end_time + r1_sync_time

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
                ensure_ffprobe(),
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
                ensure_ffmpeg(), "-y",
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
            ensure_ffmpeg(), "-y",
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
    tempo: Optional[float] = None,
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
        tempo: Optional atempo correction factor (e.g. 1.005 to speed up slightly)
        verbose: Print debug info

    Returns:
        True on success
    """
    has_audio = _has_audio_stream(video_path)

    # Build comms filter chain: volume → optional atempo
    comms_filters = f"volume={comms_volume}"
    if tempo is not None:
        comms_filters += f",atempo={tempo}"

    if has_audio:
        # Mix game audio + comms audio
        filter_complex = (
            f"[0:a]volume={game_volume}[game];"
            f"[1:a]{comms_filters}[comms];"
            f"[game][comms]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )
        cmd = [
            ensure_ffmpeg(), "-y",
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
        # No game audio — apply filters to comms and use as sole audio
        cmd = [
            ensure_ffmpeg(), "-y",
            "-i", str(video_path),
            "-i", str(comms_audio_path),
            "-filter_complex", f"[1:a]{comms_filters}[aout]",
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            str(output_path),
        ]

    if verbose:
        mode = "mixing with game audio" if has_audio else "adding as sole audio"
        tempo_info = f", atempo={tempo:.6f}" if tempo else ""
        print(f"    [Comms] Overlaying comms ({mode}{tempo_info})")

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
    tick_nav: bool = False,
    keep_segments: Optional[list[tuple[float, float]]] = None,
) -> bool:
    """High-level entry point: trim comms to match alive segments, then overlay onto video.

    If the video is trimmed and a timeline is provided, the comms audio is cut
    to match the same alive segments so it stays in sync. When keep_segments
    is provided (from trim's actual output), those durations are used instead
    of recomputing from the timeline, preventing audio drift.

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
        tick_nav: Whether tick-nav mode was used (fallback for segment extensions)
        keep_segments: Actual keep segments from trim (overrides duration calculation)

    Returns:
        True on success
    """
    if is_trimmed and timeline and timeline.alive_segments:
        # Get round 1 freeze-end time as reference
        round1_time = _get_round1_freeze_end_time(timeline)
        if round1_time is None:
            print("  Warning: No round data found, using demo start as reference")
            round1_time = 0.0

        # Extract durations from trim's keep segments if available
        keep_durations = None
        if keep_segments:
            keep_durations = [end - start for start, end in keep_segments]
            if verbose:
                print(f"    [Comms] Using {len(keep_durations)} keep segment durations from trim")

        # Compute which parts of comms audio correspond to alive segments
        comms_segments = compute_comms_segments(
            timeline.alive_segments, timeline.rounds, round1_time,
            r1_sync_time, tick_nav=tick_nav, keep_durations=keep_durations,
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

            # Compute atempo correction for residual drift
            tempo = None
            try:
                from .trim import get_video_duration
                comms_dur = get_video_duration(trimmed_comms)
                video_dur = get_video_duration(video_path)
                if comms_dur > 0 and video_dur > 0 and abs(comms_dur - video_dur) > 1.0:
                    tempo = comms_dur / video_dur
                    if verbose:
                        print(f"    [Comms] Atempo correction: {tempo:.6f} "
                              f"(comms={comms_dur:.2f}s, video={video_dur:.2f}s, "
                              f"drift={comms_dur - video_dur:+.2f}s)")
                elif verbose:
                    print(f"    [Comms] No atempo needed "
                          f"(comms={comms_dur:.2f}s, video={video_dur:.2f}s, "
                          f"drift={comms_dur - video_dur:+.2f}s)")
            except Exception as e:
                if verbose:
                    print(f"    [Comms] Could not compute atempo: {e}")

            # Overlay trimmed comms onto trimmed video
            return overlay_comms_on_video(
                video_path, trimmed_comms, output_path,
                game_volume, comms_volume, tempo, verbose,
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
                ensure_ffmpeg(), "-y",
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
                ensure_ffmpeg(), "-y",
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
