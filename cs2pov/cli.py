#!/usr/bin/env python3
"""CS2 POV Recorder - Record player POV from CS2 demo files.

Subcommand-based CLI:
  pov     - Full pipeline (record + trim)
  info    - Show demo information
  record  - Raw recording only
  trim    - Post-process existing video
"""

import argparse
import json
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import __version__
from .automation import send_key, send_console_command, check_demo_ended, wait_for_cs2_window, wait_for_demo_ready, parse_demo_end_info
from .loading import LoadingAnimation
from .capture import FFmpegCapture, get_default_audio_monitor
from .config import RecordingConfig, generate_recording_cfg
from .exceptions import CS2POVError
from .game import CS2Process, find_cs2_path, get_cfg_dir, get_demo_dir
from .settings import (
    ConfigError, find_config, load_config, resolve_job, resolve_paths,
    merge_args_with_config, generate_default_config, HARDCODED_DEFAULTS,
)
from .navigation import GotoTransition, NavigationState, recording_loop_tick_nav
from .parser import DemoInfo, PlayerInfo, find_player, get_player_index, parse_demo
from .preprocessor import DemoTimeline, preprocess_demo, get_trim_periods


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class RecordingResult:
    """Result of recording phase with metadata for post-processing."""
    success: bool
    video_path: Path
    console_log_path: Path
    exit_reason: str  # "demo_ended", "timeout", "ffmpeg_stopped", "interrupted", "segments_complete"
    timeline: Optional[DemoTimeline] = None
    transitions: Optional[list[GotoTransition]] = None


# =============================================================================
# Shared Utilities
# =============================================================================

def check_dependencies() -> list[str]:
    """Check for required system dependencies."""
    missing = []
    if not shutil.which("ffmpeg"):
        missing.append("FFmpeg - Install with: apt install ffmpeg")
    if not shutil.which("xdotool"):
        missing.append("xdotool - Install with: apt install xdotool")
    return missing


def parse_resolution(resolution_str: str) -> tuple[int, int]:
    """Parse resolution string like '1920x1080' to tuple."""
    try:
        width, height = resolution_str.lower().split("x")
        return (int(width), int(height))
    except ValueError:
        raise ValueError(f"Invalid resolution format: {resolution_str}. Use WxH (e.g., 1920x1080)")


# =============================================================================
# Recording Functions
# =============================================================================

def recording_loop(
    display: str,
    console_log_path: Path,
    cs2_process: CS2Process,
    ffmpeg: FFmpegCapture,
    timeout: float,
    verbose: bool = False,
) -> str:
    """Main recording loop - single-threaded, blocking.

    Returns:
        Exit reason: "demo_ended", "cs2_exited", "timeout", "ffmpeg_stopped"
    """
    start_time = time.time()
    last_spec_lock = 0
    log_position = 0
    window_id = None
    window_found_logged = False
    last_status_time = start_time

    print("  Recording loop started")

    while True:
        elapsed = time.time() - start_time

        if elapsed > timeout:
            print(f"  Timeout reached ({timeout/60:.1f} min)")
            return "timeout"

        if not cs2_process.is_running():
            print("  CS2 exited")
            return "cs2_exited"

        if not ffmpeg.is_running():
            print("  FFmpeg stopped unexpectedly")
            return "ffmpeg_stopped"

        demo_ended, log_position = check_demo_ended(console_log_path, log_position)
        if demo_ended:
            print("  Demo end detected in console.log")
            return "demo_ended"

        # Send spec_lock (F5) every 3 seconds
        if elapsed - last_spec_lock >= 3.0:
            if window_id is None:
                from .automation import find_cs2_window
                window_id = find_cs2_window(display)
                if window_id and not window_found_logged:
                    if verbose:
                        print(f"    CS2 window found: {window_id}")
                    window_found_logged = True

            if window_id:
                send_key("F5", display, window_id)
                if verbose:
                    print(f"    Sending F5 (spec_lock)")
            last_spec_lock = elapsed

        if elapsed - last_status_time >= 60:
            print(f"    Still recording... ({elapsed/60:.1f} min)")
            last_status_time = elapsed

        time.sleep(0.5)


def record_demo(
    demo_path: Path,
    player_identifier: str,
    output_path: Path,
    resolution: tuple[int, int] = (1920, 1080),
    framerate: int = 60,
    hide_hud: bool = True,
    display_num: int = 0,
    verbose: bool = False,
    cs2_path_override: Path | None = None,
    enable_audio: bool = True,
    audio_device: str | None = None,
    tick_nav: bool = False,
) -> RecordingResult:
    """Record a player's POV from a demo file."""
    # Find CS2 installation
    print("Finding CS2 installation...")
    cs2_path = find_cs2_path(cs2_path_override)
    print(f"  Found: {cs2_path}")

    # Parse demo
    print(f"Parsing demo: {demo_path.name}")
    demo_info = parse_demo(demo_path)
    print(f"  Map: {demo_info.map_name}, {len(demo_info.players)} players")
    print(f"  Ticks: {demo_info.total_ticks}, Rate: {demo_info.tick_rate}/s")

    # Find target player
    player = find_player(demo_info, player_identifier)
    player_index = get_player_index(demo_info, player)
    # player_slot is user_id + 1 for spec_player command
    player_slot = (player.user_id + 1) if player.user_id is not None else (player_index + 1)
    print(f"Recording POV: {player.name} (SteamID: {player.steamid}, slot: {player_slot})")

    # Preprocess demo for timeline data
    print("Preprocessing demo for timeline data...")
    try:
        timeline = preprocess_demo(demo_path, player.steamid, player.name)
        print(f"  Found {len(timeline.deaths)} deaths, {len(timeline.rounds)} rounds")
        print(f"  {len(timeline.alive_segments)} alive segments to keep")
        if verbose and timeline.alive_segments:
            total_alive = sum(seg.duration_seconds for seg in timeline.alive_segments)
            print(f"  Total alive time: {total_alive:.1f}s")
    except Exception as e:
        print(f"  Warning: Preprocessing failed: {e}")
        print(f"  Trimming will be skipped (no timeline data)")
        timeline = None

    # Prepare directories
    demo_dir = get_demo_dir(cs2_path)
    demo_dir.mkdir(parents=True, exist_ok=True)
    cfg_dir = get_cfg_dir(cs2_path)
    cfg_dir.mkdir(parents=True, exist_ok=True)

    # Copy demo to CS2 replays directory if needed
    target_demo = demo_dir / demo_path.name
    if not target_demo.exists():
        print(f"Copying demo to: {target_demo}")
        shutil.copy2(demo_path, target_demo)
    elif not target_demo.samefile(demo_path):
        print(f"Updating demo at: {target_demo}")
        shutil.copy2(demo_path, target_demo)

    # Generate recording config
    cfg_path = cfg_dir / "cs2pov_recording.cfg"
    config = RecordingConfig(
        demo_name=demo_path.stem,
        player_index=player_index,
        player_name=player.name,
        player_steamid=player.steamid,
        player_slot=player_slot,
        resolution=resolution,
        hide_hud=hide_hud,
        tick_navigation=tick_nav,
    )
    generate_recording_cfg(config, cfg_path)
    print(f"Generated config: {cfg_path.name}")

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Setup paths
    display_str = f":{display_num}"
    console_log_path = cs2_path / "game/csgo/console.log"
    cs2_log_path = output_path.parent / f"{output_path.stem}_cs2.log"

    # Calculate timeout
    if demo_info.tick_rate > 0 and demo_info.total_ticks > 0:
        estimated_duration = demo_info.total_ticks / demo_info.tick_rate
    else:
        estimated_duration = 3600
    timeout = estimated_duration + 600

    print(f"Starting recording (timeout: {timeout/60:.1f} min)...")
    print(f"  Display: {display_str}")
    print(f"  Output: {output_path}")

    cs2_process: Optional[CS2Process] = None
    ffmpeg: Optional[FFmpegCapture] = None
    exit_reason = "unknown"

    try:
        # Delete old console.log
        if console_log_path.exists():
            console_log_path.unlink()
            if verbose:
                print(f"  Deleted old console.log")

        # Launch CS2
        cs2_process = CS2Process(cs2_path, display_str, log_path=cs2_log_path)
        cs2_process.launch("cs2pov_recording.cfg")
        print("  CS2 launching via Steam...")

        # Wait for CS2 window
        print("  Waiting for CS2 window...")
        window_id = wait_for_cs2_window(display_str, timeout=120)
        if window_id:
            print(f"  CS2 window ready")
        else:
            print("  Warning: CS2 window not detected, continuing anyway")

        # Determine tick-nav mode early (needed for startup sequence)
        use_tick_nav = (
            tick_nav
            and timeline is not None
            and timeline.alive_segments
        )

        # Wait for demo to be ready
        print("  Waiting for demo to load...")
        if wait_for_demo_ready(console_log_path, timeout=180):
            print("  Demo ready")
            if verbose:
                print("  Waiting 20s before hiding demo UI...")
            time.sleep(20)
            # In tick-nav mode, defer Shift+F2 until after the seek (gototick resets UI state)
            if not use_tick_nav:
                if window_id and send_key("shift+F2", display_str, window_id):
                    if verbose:
                        print("  Sent Shift+F2 to hide demo UI")
                elif window_id:
                    print("  Warning: Failed to send Shift+F2 to hide demo UI")
        else:
            print("  Warning: Demo ready state not detected, continuing anyway")

        # Determine audio source
        audio_source = None
        if enable_audio:
            audio_source = audio_device or get_default_audio_monitor()
            if audio_source:
                if verbose:
                    print(f"  Audio device: {audio_source}")
            else:
                print("  Warning: Could not detect audio device, recording video only")

        # In tick-nav mode: pause, seek to first segment, then start FFmpeg
        # This synchronizes the demo clock with the recording clock from frame 1
        if use_tick_nav and window_id:
            first_tick = timeline.alive_segments[0].start_tick
            if verbose:
                print(f"  Tick-nav: seeking to first segment start tick {first_tick}")
            send_key("F7", display_str, window_id)  # Pause demo
            time.sleep(0.5)
            send_console_command(f"demo_gototick {first_tick}", display_str, window_id)
            time.sleep(2.0)  # Wait for seek to complete
            send_key("F5", display_str, window_id)  # Re-lock spectator
            # Send Shift+F2 after seek (gototick resets UI state, swallowing earlier sends)
            if send_key("shift+F2", display_str, window_id):
                if verbose:
                    print("  Sent Shift+F2 to hide demo UI")
            else:
                print("  Warning: Failed to send Shift+F2 to hide demo UI")

        # Start FFmpeg capture
        ffmpeg = FFmpegCapture(
            display=display_str,
            output_path=output_path,
            resolution=resolution,
            framerate=framerate,
            audio_device=audio_source if enable_audio else None,
            enable_audio=enable_audio and audio_source is not None,
        )
        ffmpeg.start()
        if ffmpeg.enable_audio:
            print(f"  FFmpeg capture started (video + audio)")
        else:
            print(f"  FFmpeg capture started (video only)")

        # Unpause after FFmpeg is rolling (tick-nav only)
        if use_tick_nav and window_id:
            send_key("F6", display_str, window_id)  # Resume demo
            if verbose:
                print("  Tick-nav: demo unpaused, recording synchronized")

        transitions = None

        if use_tick_nav:
            nav_state = NavigationState(
                timeline=timeline,
            )
            exit_reason, transitions = recording_loop_tick_nav(
                display=display_str,
                console_log_path=console_log_path,
                cs2_process=cs2_process,
                ffmpeg=ffmpeg,
                state=nav_state,
                timeout=timeout,
                verbose=verbose,
            )
        else:
            if tick_nav and (timeline is None or not timeline.alive_segments):
                print("  Warning: --tick-nav requires alive segments, falling back to standard recording")
            exit_reason = recording_loop(
                display=display_str,
                console_log_path=console_log_path,
                cs2_process=cs2_process,
                ffmpeg=ffmpeg,
                timeout=timeout,
                verbose=verbose,
            )

    except KeyboardInterrupt:
        print("\n  Recording interrupted by user")
        exit_reason = "interrupted"

    finally:
        print("\nCleaning up...")

        if ffmpeg:
            print("  Stopping FFmpeg...")
            graceful = ffmpeg.stop(timeout=15)
            if graceful:
                print("  FFmpeg stopped gracefully")
            else:
                print("  FFmpeg force-stopped")
            if ffmpeg.stderr_path and ffmpeg.stderr_path.exists():
                print(f"  FFmpeg log: {ffmpeg.stderr_path}")

        if cs2_process and cs2_process.is_running():
            print("  Terminating CS2...")
            cs2_process.terminate()

        print("  Cleanup complete")

        if console_log_path.exists():
            saved_log_path = output_path.parent / f"console_{demo_path.stem}.log"
            shutil.copy2(console_log_path, saved_log_path)
            print(f"  Console log saved: {saved_log_path.name}")
            console_log_path = saved_log_path

    success = exit_reason in ("demo_ended", "cs2_exited", "segments_complete")
    return RecordingResult(
        success=success,
        video_path=output_path,
        console_log_path=console_log_path,
        exit_reason=exit_reason,
        timeline=timeline,
        transitions=transitions,
    )


def postprocess_video(
    video_path: Path,
    console_log_path: Path,
    verbose: bool = False,
    timeline: Optional[DemoTimeline] = None,
    startup_time_override: Optional[float] = None,
    transitions: Optional[list[GotoTransition]] = None,
) -> Path:
    """Post-process a recorded video to keep only alive segments.

    If transitions are provided (from tick-nav recording), uses lightweight
    transition-based trimming instead of the full alive-segment approach.

    Otherwise uses the standard approach:
    1. Calculate startup_time = video_duration - demo_duration
    2. Convert alive_segments from demo time to video time
    3. Extract and concatenate only the alive segments

    Args:
        console_log_path: Path to console log (used for demo end detection/duration)
        startup_time_override: If provided, use this value instead of calculating
            startup_time. Useful when the automatic calculation is wrong.
        transitions: If provided, use transition-based trimming (from --tick-nav)
    """
    from .trim import get_video_duration, extract_and_concat_segments, trim_goto_transitions

    if not video_path.exists():
        print(f"Error: Video file not found: {video_path}")
        return video_path

    raw_size_mb = video_path.stat().st_size / (1024 * 1024)
    print(f"\nRaw recording: {video_path} ({raw_size_mb:.1f} MB)")

    # Tick-nav transition-based trimming (lightweight)
    if transitions:
        print(f"\nPost-processing: trimming {len(transitions)} goto transitions...")
        raw_path = video_path.parent / f"{video_path.stem}_raw{video_path.suffix}"
        video_path.rename(raw_path)
        print(f"  Raw recording moved to: {raw_path.name}")

        success = trim_goto_transitions(
            input_path=raw_path,
            output_path=video_path,
            transitions=transitions,
            verbose=verbose,
        )

        if success and video_path.exists():
            final_size_mb = video_path.stat().st_size / (1024 * 1024)
            print(f"\nFinal recording: {video_path} ({final_size_mb:.1f} MB)")
        else:
            print("\nTransition trimming failed, restoring raw recording")
            if not video_path.exists() and raw_path.exists():
                raw_path.rename(video_path)

        return video_path

    print("\nPost-processing: calculating segments to keep...")

    video_segments: list[tuple[float, float]] = []

    # Prefer timeline data with alive_segments
    if timeline is not None and timeline.alive_segments:
        print("  Using alive segments from timeline (demoparser2)")
        if verbose:
            print(f"  Alive segments: {len(timeline.alive_segments)}")
            print(f"  Deaths: {len(timeline.deaths)}, Rounds: {len(timeline.rounds)}")
            print(f"  Tickrate: {timeline.tickrate}, Total ticks: {timeline.total_ticks}")
            for i, seg in enumerate(timeline.alive_segments):
                print(f"    Segment {i+1}: R{seg.round_num} {seg.start_time:.2f}s - {seg.end_time:.2f}s "
                      f"({seg.duration_seconds:.2f}s, ended: {seg.reason_ended})")

        # Step 1: Get video duration
        try:
            video_duration = get_video_duration(video_path)
        except Exception as e:
            print(f"  Error getting video duration: {e}")
            print(f"\nRecording saved: {video_path} ({raw_size_mb:.1f} MB)")
            return video_path

        # Step 2: Get demo duration for startup_time calculation
        demo_duration = 0.0

        # Method 1: From console.log demo end marker (most accurate)
        demo_end_info = parse_demo_end_info(console_log_path)
        if demo_end_info and timeline.tickrate > 0:
            demo_duration = demo_end_info.tick / timeline.tickrate
            if verbose:
                print(f"  Demo duration source: console.log end marker")

        # Method 2: From timeline total_duration (from demo header)
        if demo_duration == 0.0 and timeline.total_duration > 0:
            demo_duration = timeline.total_duration
            if verbose:
                print(f"  Demo duration source: demo header")

        # Method 3: From last round end (more accurate than alive segments)
        if demo_duration == 0.0 and timeline.rounds:
            # Find the last round with an end_tick
            for r in reversed(timeline.rounds):
                if r.end_tick is not None:
                    demo_duration = r.end_time + 5.0  # Small buffer after round ends
                    if verbose:
                        print(f"  Demo duration source: last round end + 5s")
                    break

        # Method 4: From last alive segment end (fallback)
        if demo_duration == 0.0 and timeline.alive_segments:
            last_segment = timeline.alive_segments[-1]
            demo_duration = last_segment.end_time + 5.0  # Small buffer
            if verbose:
                print(f"  Demo duration source: last alive segment + 5s")

        if verbose:
            print(f"  Video duration: {video_duration:.2f}s")
            print(f"  Demo duration: {demo_duration:.2f}s")

        if demo_duration == 0.0:
            print("  Error: Could not determine demo duration")
            print(f"\nRecording saved: {video_path} ({raw_size_mb:.1f} MB)")
            return video_path

        # Step 3: Calculate startup time (recording before demo started)
        if startup_time_override is not None:
            startup_time = startup_time_override
            if verbose:
                print(f"  Startup time: {startup_time:.2f}s (manual override)")
        else:
            startup_time = video_duration - demo_duration
            if startup_time < 0:
                print(f"  Warning: Negative startup time ({startup_time:.2f}s), using 0")
                startup_time = 0.0
            if verbose:
                print(f"  Startup time: {startup_time:.2f}s (calculated)")

        # Step 4: Convert alive segments to video time
        for seg in timeline.alive_segments:
            video_start = startup_time + seg.start_time
            video_end = startup_time + seg.end_time

            # Clamp to video bounds
            video_start = max(0.0, video_start)
            video_end = min(video_duration, video_end)

            # Only include if segment has meaningful duration
            if video_end > video_start + 0.5:
                video_segments.append((video_start, video_end))
                if verbose:
                    print(f"  Keep: {video_start:.2f}s - {video_end:.2f}s ({video_end - video_start:.2f}s)")

    # Execute trimming
    if video_segments:
        total_keep_time = sum(end - start for start, end in video_segments)
        try:
            video_duration = get_video_duration(video_path)
            total_trim_time = video_duration - total_keep_time
        except:
            total_trim_time = 0.0

        print(f"  Found {len(video_segments)} segments to keep")
        print(f"  Total keep time: {total_keep_time:.1f}s, trim time: {total_trim_time:.1f}s")

        raw_path = video_path.parent / f"{video_path.stem}_raw{video_path.suffix}"
        video_path.rename(raw_path)
        print(f"  Raw recording moved to: {raw_path.name}")

        print("  Extracting and concatenating segments...")
        success = extract_and_concat_segments(
            input_path=raw_path,
            output_path=video_path,
            segments=video_segments,
            verbose=verbose
        )

        if success and video_path.exists():
            final_size_mb = video_path.stat().st_size / (1024 * 1024)
            print(f"\nFinal recording: {video_path} ({final_size_mb:.1f} MB)")
        else:
            print("\nTrimming failed, restoring raw recording")
            if not video_path.exists() and raw_path.exists():
                raw_path.rename(video_path)
    else:
        print("  No segments to extract, keeping original")
        print(f"\nRecording saved: {video_path} ({raw_size_mb:.1f} MB)")

    return video_path


# =============================================================================
# Info Output Functions
# =============================================================================

def print_demo_info_extended(
    demo_info: DemoInfo,
    timelines: dict[int, DemoTimeline],
    verbose: bool = False
):
    """Print comprehensive demo information."""
    # Get rounds and duration from timeline data (more reliable than header)
    rounds_count = 0
    max_tick = 0
    tickrate = demo_info.tick_rate or 64

    for timeline in timelines.values():
        if timeline.rounds:
            rounds_count = max(rounds_count, len(timeline.rounds))
        # Find max tick from spawns/deaths to calculate duration
        for spawn in timeline.spawns:
            max_tick = max(max_tick, spawn.tick)
        for death in timeline.deaths:
            max_tick = max(max_tick, death.tick)
        if timeline.tickrate > 0:
            tickrate = timeline.tickrate

    # Calculate duration from max tick
    if max_tick > 0 and tickrate > 0:
        duration = max_tick / tickrate
        duration_str = f"{duration:.1f}s ({duration/60:.1f} min)"
    elif demo_info.total_ticks > 0 and tickrate > 0:
        duration = demo_info.total_ticks / tickrate
        duration_str = f"{duration:.1f}s ({duration/60:.1f} min)"
    else:
        duration_str = "unknown"

    print(f"Demo: {demo_info.path.name}")
    print(f"  Map: {demo_info.map_name}")
    print(f"  Tick rate: {tickrate}/s")
    print(f"  Duration: {duration_str}")
    print(f"  Rounds: {rounds_count}")

    print(f"\nPlayers ({len(demo_info.players)}):")
    for player in demo_info.players:
        timeline = timelines.get(player.steamid)

        print(f"  {player.name}")
        print(f"    SteamID: {player.steamid}")
        print(f"    Kills: {player.kills}  Deaths: {len(timeline.deaths) if timeline else 0}  Assists: {player.assists}")

        if timeline:
            alive_time = sum(seg.duration_seconds for seg in timeline.alive_segments)
            print(f"    Alive segments: {len(timeline.alive_segments)}, Alive time: {alive_time:.1f}s")

            if verbose and timeline.alive_segments:
                print(f"    Alive segments:")
                for i, seg in enumerate(timeline.alive_segments, 1):
                    print(f"      {i:2}. R{seg.round_num:2} {seg.start_time:7.1f}s - {seg.end_time:7.1f}s "
                          f"({seg.duration_seconds:5.1f}s) [{seg.reason_ended}]")


def format_info_json(
    demo_info: DemoInfo,
    timelines: dict[int, DemoTimeline]
) -> dict:
    """Format demo info as JSON-serializable dict."""
    # Get rounds and duration from timeline data (more reliable than header)
    rounds_count = 0
    max_tick = 0
    tickrate = demo_info.tick_rate or 64

    for timeline in timelines.values():
        if timeline.rounds:
            rounds_count = max(rounds_count, len(timeline.rounds))
        for spawn in timeline.spawns:
            max_tick = max(max_tick, spawn.tick)
        for death in timeline.deaths:
            max_tick = max(max_tick, death.tick)
        if timeline.tickrate > 0:
            tickrate = timeline.tickrate

    # Calculate duration from max tick
    if max_tick > 0 and tickrate > 0:
        duration = max_tick / tickrate
    elif demo_info.total_ticks > 0 and tickrate > 0:
        duration = demo_info.total_ticks / tickrate
    else:
        duration = 0

    players_data = []
    for player in demo_info.players:
        timeline = timelines.get(player.steamid)
        player_data = {
            "name": player.name,
            "steamid": player.steamid,
            "kills": player.kills,
            "assists": player.assists,
        }

        if timeline:
            player_data["deaths"] = len(timeline.deaths)
            player_data["alive_time_seconds"] = sum(seg.duration_seconds for seg in timeline.alive_segments)
            player_data["alive_segments"] = [
                {
                    "round_num": seg.round_num,
                    "start_tick": seg.start_tick,
                    "start_time": seg.start_time,
                    "end_tick": seg.end_tick,
                    "end_time": seg.end_time,
                    "duration_seconds": seg.duration_seconds,
                    "reason_ended": seg.reason_ended,
                }
                for seg in timeline.alive_segments
            ]

        players_data.append(player_data)

    return {
        "demo": demo_info.path.name,
        "map": demo_info.map_name,
        "tick_rate": tickrate,
        "duration_seconds": duration,
        "rounds": rounds_count,
        "players": players_data,
    }


# =============================================================================
# Argument Parser
# =============================================================================

def create_parser() -> argparse.ArgumentParser:
    """Create argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="cs2pov",
        description="Record player POV from CS2 demo files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  cs2pov                                   # Run all jobs from cs2pov.json
  cs2pov init                              # Create cs2pov.json config
  cs2pov info demo.dem                     # Show demo information
  cs2pov info demo.dem --json              # Output as JSON
  cs2pov pov -d demo.dem -p "Player" -o out.mp4
  cs2pov pov                               # Run batch pov jobs from cs2pov.json
  cs2pov record -d demo.dem -p "Player" -o raw.mp4
  cs2pov trim raw.mp4 -d demo.dem -p "Player"
""",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", type=Path, default=None,
                        help="Path to cs2pov.json config file (default: auto-detect)")

    subparsers = parser.add_subparsers(dest="command", required=False, metavar="COMMAND")

    # Shared argument groups
    demo_args = argparse.ArgumentParser(add_help=False)
    demo_args.add_argument("-d", "--demo", default=None, type=Path,
                           help="Demo file (.dem)")

    player_args = argparse.ArgumentParser(add_help=False)
    player_args.add_argument("-p", "--player", default=None,
                             help="Player name or SteamID")

    output_args = argparse.ArgumentParser(add_help=False)
    output_args.add_argument("-o", "--output", default=None, type=Path,
                             help="Output video file")

    recording_args = argparse.ArgumentParser(add_help=False)
    recording_args.add_argument("-r", "--resolution", default=None,
                                help="Recording resolution (default: 1920x1080)")
    recording_args.add_argument("-f", "--framerate", type=int, default=None,
                                help="Recording framerate (default: 60)")
    recording_args.add_argument("--display", type=int, default=None,
                                help="X display number (default: 0)")
    recording_args.add_argument("--no-hud", action="store_true", default=None,
                                help="Hide HUD elements")
    recording_args.add_argument("--hud", action="store_false", dest="no_hud",
                                help="Show HUD (override config no_hud)")
    recording_args.add_argument("--no-audio", action="store_true", default=None,
                                help="Disable audio recording")
    recording_args.add_argument("--audio", action="store_false", dest="no_audio",
                                help="Enable audio (override config no_audio)")
    recording_args.add_argument("--audio-device", default=None,
                                help="PulseAudio device (auto-detected)")
    recording_args.add_argument("--cs2-path", type=Path, default=None,
                                help="Custom CS2 installation path")
    recording_args.add_argument("--tick-nav", action=argparse.BooleanOptionalAction, default=None,
                                help="Enable tick-based navigation (skip deaths in real-time)")

    verbose_args = argparse.ArgumentParser(add_help=False)
    verbose_args.add_argument("-v", "--verbose", action=argparse.BooleanOptionalAction, default=None,
                              help="Verbose output")

    # INFO command
    info_parser = subparsers.add_parser(
        "info",
        parents=[verbose_args],
        help="Show demo information and player timeline data",
        description="Display demo metadata, player list, and death/spawn statistics.",
    )
    info_parser.add_argument("demo", type=Path, help="Demo file (.dem)")
    info_parser.add_argument("--json", action="store_true",
                             help="Output as JSON")

    # POV command (full pipeline)
    pov_parser = subparsers.add_parser(
        "pov",
        parents=[demo_args, player_args, output_args, recording_args, verbose_args],
        help="Record and trim player POV (full pipeline)",
        description="Record a player's POV from a demo and trim death periods.",
    )
    pov_parser.add_argument("--no-trim", action="store_true", default=None,
                            help="Skip post-processing trim")
    pov_parser.add_argument("--trim", action="store_false", dest="no_trim",
                            help="Enable trimming (override config no_trim)")

    # RECORD command
    subparsers.add_parser(
        "record",
        parents=[demo_args, player_args, output_args, recording_args, verbose_args],
        help="Record player POV without trimming",
        description="Record a player's POV from a demo without post-processing.",
    )

    # TRIM command
    trim_parser = subparsers.add_parser(
        "trim",
        parents=[demo_args, player_args, verbose_args],
        help="Trim death periods from recorded video",
        description="Post-process an existing recording to remove death periods.",
    )
    trim_parser.add_argument("video", type=Path, help="Input video file")
    trim_parser.add_argument("-o", "--output", type=Path,
                             help="Output file (default: adds _trimmed suffix)")
    trim_parser.add_argument("--startup-time", type=float,
                             help="Override startup time (seconds from video start to demo start)")

    # INIT command
    subparsers.add_parser(
        "init",
        help="Create a cs2pov.json config file in the current directory",
        description="Generate a template cs2pov.json config with defaults and example jobs.",
    )

    return parser


# =============================================================================
# Command Handlers
# =============================================================================

def _validate_required_args(args, command: str) -> Optional[str]:
    """Validate that required args (demo, player, output) are present after merging.

    Returns error message or None if valid.
    """
    missing = []
    if getattr(args, "demo", None) is None:
        missing.append("-d/--demo")
    if getattr(args, "player", None) is None:
        missing.append("-p/--player")
    if getattr(args, "output", None) is None:
        missing.append("-o/--output")

    if missing:
        return f"Missing required arguments for '{command}': {', '.join(missing)}"
    return None


def _job_runner_for_type(job_type: str):
    """Return the single-job runner for a given job type."""
    if job_type == "record":
        return _run_single_record
    return _run_single_pov


def _run_batch(args, command: str, run_single_job=None) -> int:
    """Run single or batch jobs with config support.

    Args:
        args: Parsed argparse namespace
        command: Command name for error messages
        run_single_job: Callable(args) -> int for single job. If None,
            dispatches per job using the job's 'type' field from config.
    """
    from copy import copy

    # Load config
    try:
        config_path = find_config(getattr(args, "config", None))
        project = load_config(config_path) if config_path else None
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    config_dir = config_path.parent if config_path else Path.cwd()

    # Determine job list and per-job types
    job_types: list[str] = []
    if project and project.jobs and getattr(args, "demo", None) is None:
        # Batch mode: use jobs from config
        job_types = [j.type for j in project.jobs]
        jobs = [resolve_job(j, project.defaults) for j in project.jobs]
        jobs = [resolve_paths(j, config_dir) for j in jobs]
        print(f"Running {len(jobs)} jobs from {config_path.name}\n")
    else:
        # Single mode: config defaults + CLI args
        jobs = [project.defaults.copy() if project else {}]
        job_types = [command]  # Use the command as the type
        if project:
            jobs[0] = resolve_paths(jobs[0], config_dir)

    results: list[tuple[int, str, Optional[str]]] = []  # (index, demo_name, error)

    for i, job in enumerate(jobs):
        merged = merge_args_with_config(copy(args), job)
        job_type = job_types[i]

        # Validate required fields
        err = _validate_required_args(merged, job_type)
        if err:
            if len(jobs) == 1:
                print(f"Error: {err}", file=sys.stderr)
                if project:
                    print(f"Hint: Add jobs to {config_path.name} or pass -d, -p, -o flags", file=sys.stderr)
                else:
                    print(f"Hint: Create a cs2pov.json with 'cs2pov init' or pass -d, -p, -o flags", file=sys.stderr)
                return 1
            demo_name = job.get("demo", f"job {i+1}")
            print(f"[SKIP] Job {i+1}: {demo_name} - {err}")
            results.append((i, str(demo_name), err))
            continue

        demo_name = str(merged.demo)
        if len(jobs) > 1:
            print(f"{'='*60}")
            print(f"Job {i+1}/{len(jobs)}: {Path(demo_name).name} ({job_type})")
            print(f"{'='*60}\n")

        # Dispatch to the appropriate runner
        runner = run_single_job if run_single_job else _job_runner_for_type(job_type)

        try:
            ret = runner(merged)
            if ret == 0:
                results.append((i, demo_name, None))
            else:
                results.append((i, demo_name, f"Exit code {ret}"))
        except CS2POVError as e:
            print(f"Error: {e}", file=sys.stderr)
            results.append((i, demo_name, str(e)))
        except Exception as e:
            print(f"Unexpected error: {e}", file=sys.stderr)
            results.append((i, demo_name, str(e)))

        # Sleep between batch jobs
        if len(jobs) > 1 and i < len(jobs) - 1:
            print(f"\nWaiting 10s before next job...\n")
            time.sleep(10)

    # Print batch summary
    if len(jobs) > 1:
        succeeded = sum(1 for _, _, err in results if err is None)
        print(f"\n{'='*60}")
        print(f"Batch complete: {succeeded}/{len(results)} succeeded")
        for i, demo_name, err in results:
            if err:
                print(f"  [FAIL] Job {i+1}: {Path(demo_name).name} - {err}")
        print(f"{'='*60}")
        return 0 if succeeded == len(results) else 1

    # Single job
    if results and results[0][2] is not None:
        return 1
    return 0


def cmd_init(args) -> int:
    """Handle 'init' command - create cs2pov.json config."""
    config_path = Path.cwd() / "cs2pov.json"

    if config_path.exists():
        print(f"Config already exists: {config_path}")
        response = input("Overwrite? [y/N] ").strip().lower()
        if response != "y":
            print("Aborted.")
            return 0

    # Try to auto-detect CS2 path
    cs2_path_str = None
    try:
        cs2_path = find_cs2_path()
        cs2_path_str = str(cs2_path)
        print(f"Detected CS2 path: {cs2_path}")
    except CS2POVError:
        print("CS2 path not auto-detected. You can set it manually in the config.")

    config = generate_default_config(cs2_path=cs2_path_str)
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    print(f"Created {config_path}")
    print(f"\nEdit the 'jobs' array to add your recording jobs, then run:")
    print(f"  cs2pov pov")
    return 0


def cmd_info(args) -> int:
    """Handle 'info' command - show demo information."""
    demo_path = args.demo.resolve()
    if not demo_path.exists():
        print(f"Error: Demo not found: {demo_path}", file=sys.stderr)
        return 1

    # Parse basic demo info
    with LoadingAnimation():
        try:
            demo_info = parse_demo(demo_path)
        except CS2POVError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

        # Preprocess for timeline data (per-player)
        player_timelines: dict[int, DemoTimeline] = {}
        for player in demo_info.players:
            try:
                timeline = preprocess_demo(demo_path, player.steamid, player.name)
                player_timelines[player.steamid] = timeline
            except Exception:
                pass

    if args.json:
        output = format_info_json(demo_info, player_timelines)
        print(json.dumps(output, indent=2))
    else:
        print_demo_info_extended(demo_info, player_timelines, verbose=args.verbose)

    return 0


def _run_single_pov(args) -> int:
    """Run a single POV recording job (record + trim)."""
    # Check dependencies
    missing = check_dependencies()
    if missing:
        print("Missing dependencies:", file=sys.stderr)
        for dep in missing:
            print(f"  - {dep}", file=sys.stderr)
        return 1

    # Validate inputs
    demo_path = args.demo.resolve()
    if not demo_path.exists():
        print(f"Error: Demo not found: {demo_path}", file=sys.stderr)
        return 1

    try:
        resolution = parse_resolution(args.resolution)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    output_path = args.output.resolve()

    with LoadingAnimation():
        # Record
        result = record_demo(
            demo_path=demo_path,
            player_identifier=args.player,
            output_path=output_path,
            resolution=resolution,
            framerate=args.framerate,
            hide_hud=args.no_hud,
            display_num=args.display,
            verbose=args.verbose,
            cs2_path_override=args.cs2_path,
            enable_audio=not args.no_audio,
            audio_device=args.audio_device,
            tick_nav=args.tick_nav,
        )

        # Post-process
        if result.success and not args.no_trim:
            postprocess_video(
                video_path=result.video_path,
                console_log_path=result.console_log_path,
                verbose=args.verbose,
                timeline=result.timeline,
                transitions=result.transitions,
            )
        elif result.success:
            size_mb = result.video_path.stat().st_size / (1024 * 1024)
            print(f"\nRecording saved: {result.video_path} ({size_mb:.1f} MB)")
        else:
            print(f"\nRecording ended with: {result.exit_reason}")
            if result.video_path.exists():
                size_mb = result.video_path.stat().st_size / (1024 * 1024)
                print(f"Partial recording available: {result.video_path} ({size_mb:.1f} MB)")

    return 0 if result.success else 1


def cmd_pov(args) -> int:
    """Handle 'pov' command - full recording pipeline with batch support."""
    return _run_batch(args, "pov", _run_single_pov)


def _run_single_record(args) -> int:
    """Run a single raw recording job (no trim)."""
    # Check dependencies
    missing = check_dependencies()
    if missing:
        print("Missing dependencies:", file=sys.stderr)
        for dep in missing:
            print(f"  - {dep}", file=sys.stderr)
        return 1

    # Validate inputs
    demo_path = args.demo.resolve()
    if not demo_path.exists():
        print(f"Error: Demo not found: {demo_path}", file=sys.stderr)
        return 1

    try:
        resolution = parse_resolution(args.resolution)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    output_path = args.output.resolve()

    with LoadingAnimation():
        # Record only
        result = record_demo(
            demo_path=demo_path,
            player_identifier=args.player,
            output_path=output_path,
            resolution=resolution,
            framerate=args.framerate,
            hide_hud=args.no_hud,
            display_num=args.display,
            verbose=args.verbose,
            cs2_path_override=args.cs2_path,
            enable_audio=not args.no_audio,
            audio_device=args.audio_device,
            tick_nav=args.tick_nav,
        )

        if result.success:
            size_mb = result.video_path.stat().st_size / (1024 * 1024)
            print(f"\nRaw recording saved: {result.video_path} ({size_mb:.1f} MB)")
            print(f"\nTo trim later, run:")
            print(f"  cs2pov trim \"{result.video_path}\" -d \"{demo_path}\" -p \"{args.player}\"")
        else:
            print(f"\nRecording ended with: {result.exit_reason}")
            if result.video_path.exists():
                size_mb = result.video_path.stat().st_size / (1024 * 1024)
                print(f"Partial recording available: {result.video_path} ({size_mb:.1f} MB)")

    return 0 if result.success else 1


def cmd_record(args) -> int:
    """Handle 'record' command - raw recording with batch support."""
    return _run_batch(args, "record", _run_single_record)


def cmd_run(args) -> int:
    """Handle bare 'cs2pov' command - run all jobs from config with per-job type dispatch."""
    config_path = find_config(getattr(args, "config", None))
    if config_path is None:
        print("Error: No cs2pov.json config found.", file=sys.stderr)
        print("Hint: Create one with 'cs2pov init' or specify with --config", file=sys.stderr)
        return 1

    project = load_config(config_path)
    if not project.jobs:
        print(f"Error: No jobs defined in {config_path.name}", file=sys.stderr)
        print("Hint: Add jobs to the 'jobs' array in your config file", file=sys.stderr)
        return 1

    return _run_batch(args, "pov")


def _apply_config_defaults(args) -> None:
    """Apply config defaults to args for commands that don't use _run_batch.

    Non-fatal: config errors are silently ignored.
    """
    try:
        config_path = find_config(getattr(args, "config", None))
        if config_path:
            project = load_config(config_path)
            for key, value in project.defaults.items():
                if key in HARDCODED_DEFAULTS and getattr(args, key, None) is None:
                    setattr(args, key, value)
    except ConfigError:
        pass

    # Fill remaining None values with hardcoded defaults
    for key, value in HARDCODED_DEFAULTS.items():
        if getattr(args, key, None) is None:
            setattr(args, key, value)


def cmd_trim(args) -> int:
    """Handle 'trim' command - post-process existing video."""
    _apply_config_defaults(args)

    video_path = args.video.resolve()
    if not video_path.exists():
        print(f"Error: Video not found: {video_path}", file=sys.stderr)
        return 1

    demo_path = args.demo.resolve()
    if not demo_path.exists():
        print(f"Error: Demo not found: {demo_path}", file=sys.stderr)
        return 1

    # Determine output path
    if args.output:
        output_path = args.output.resolve()
    else:
        output_path = video_path.parent / f"{video_path.stem}_trimmed{video_path.suffix}"

    with LoadingAnimation():
        # Parse demo and find player
        try:
            demo_info = parse_demo(demo_path)
            player = find_player(demo_info, args.player)
        except CS2POVError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

        # Get timeline from demo
        timeline = None
        try:
            timeline = preprocess_demo(demo_path, player.steamid, player.name)
            print(f"Using demo timeline: {len(timeline.deaths)} deaths, {len(timeline.rounds)} rounds")
            print(f"  {len(timeline.alive_segments)} alive segments to keep")
        except Exception as e:
            print(f"Warning: Could not preprocess demo: {e}")

        if timeline is None:
            print("Error: Demo preprocessing failed, cannot trim without timeline data", file=sys.stderr)
            return 1

        # Copy video to output path first (postprocess_video expects to rename)
        if video_path != output_path:
            shutil.copy2(video_path, output_path)

        # Run trimming
        postprocess_video(
            video_path=output_path,
            console_log_path=Path("/dev/null"),
            verbose=args.verbose,
            timeline=timeline,
            startup_time_override=args.startup_time,
        )

    return 0


# =============================================================================
# Entry Point
# =============================================================================

def main() -> int:
    """Main entry point."""
    parser = create_parser()
    args = parser.parse_args()

    try:
        if args.command is None:
            return cmd_run(args)
        elif args.command == "init":
            return cmd_init(args)
        elif args.command == "info":
            return cmd_info(args)
        elif args.command == "pov":
            return cmd_pov(args)
        elif args.command == "record":
            return cmd_record(args)
        elif args.command == "trim":
            return cmd_trim(args)
        else:
            parser.print_help()
            return 1
    except CS2POVError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
