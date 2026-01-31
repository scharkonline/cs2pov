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
from .automation import send_key, check_demo_ended, wait_for_cs2_window, wait_for_demo_ready, parse_demo_end_info
from .capture import FFmpegCapture, get_default_audio_monitor
from .config import RecordingConfig, generate_recording_cfg
from .exceptions import CS2POVError
from .game import CS2Process, find_cs2_path, get_cfg_dir, get_demo_dir
from .parser import DemoInfo, PlayerInfo, find_player, get_player_index, parse_demo
from .preprocessor import DemoTimeline, preprocess_demo, get_trim_periods
from .trim import extract_death_periods


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class RecordingResult:
    """Result of recording phase with metadata for post-processing."""
    success: bool
    video_path: Path
    console_log_path: Path
    recording_start_time: float
    player_slot: int
    exit_reason: str  # "demo_ended", "timeout", "ffmpeg_stopped", "interrupted"
    timeline: Optional[DemoTimeline] = None


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
        print(f"  Will fall back to console.log parsing for trim")
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
    recording_start_time = 0.0
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

        # Wait for demo to be ready
        print("  Waiting for demo to load...")
        if wait_for_demo_ready(console_log_path, timeout=180):
            print("  Demo ready")
            if verbose:
                print("  Waiting 10s before hiding demo UI...")
            time.sleep(10)
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

        recording_start_time = time.time()

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

    success = exit_reason in ("demo_ended", "cs2_exited")
    return RecordingResult(
        success=success,
        video_path=output_path,
        console_log_path=console_log_path,
        recording_start_time=recording_start_time,
        player_slot=player_index,
        exit_reason=exit_reason,
        timeline=timeline,
    )


def postprocess_video(
    video_path: Path,
    console_log_path: Path,
    player_slot: int,
    recording_start_time: float,
    verbose: bool = False,
    timeline: Optional[DemoTimeline] = None,
    startup_time_override: Optional[float] = None,
) -> Path:
    """Post-process a recorded video to keep only alive segments.

    New simplified approach:
    1. Calculate startup_time = video_duration - demo_duration
    2. Convert alive_segments from demo time to video time
    3. Extract and concatenate only the alive segments

    Args:
        startup_time_override: If provided, use this value instead of calculating
            startup_time. Useful when the automatic calculation is wrong.
    """
    from .trim import get_video_duration, extract_and_concat_segments

    if not video_path.exists():
        print(f"Error: Video file not found: {video_path}")
        return video_path

    raw_size_mb = video_path.stat().st_size / (1024 * 1024)
    print(f"\nRaw recording: {video_path} ({raw_size_mb:.1f} MB)")

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

    # Fall back to console.log parsing (legacy method)
    if not video_segments:
        if timeline is not None:
            print("  No alive segments found, falling back to console.log")
        else:
            print("  Using console.log parsing (legacy method)")

        if verbose:
            print(f"  Console log: {console_log_path}")
            print(f"  Player slot: {player_slot}")
            print(f"  Recording start: {recording_start_time}")

        if not console_log_path.exists():
            print("  Console log not found, skipping trim")
            print(f"\nRecording saved: {video_path} ({raw_size_mb:.1f} MB)")
            return video_path

        # Use legacy death period extraction
        death_periods = extract_death_periods(
            log_path=console_log_path,
            player_slot=player_slot,
            recording_start_time=recording_start_time,
            verbose=verbose
        )

        if death_periods:
            # Convert death periods to video segments (inverse)
            try:
                video_duration = get_video_duration(video_path)
            except Exception as e:
                print(f"  Error getting video duration: {e}")
                return video_path

            # Sort death periods and compute alive segments
            death_periods_sorted = sorted(death_periods, key=lambda p: p.death_time)
            current_pos = 0.0

            for dp in death_periods_sorted:
                if dp.death_time > current_pos:
                    video_segments.append((current_pos, dp.death_time))
                current_pos = max(current_pos, dp.respawn_time)

            # Add final segment
            if current_pos < video_duration:
                video_segments.append((current_pos, video_duration))

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
  cs2pov info demo.dem                     # Show demo information
  cs2pov info demo.dem --json              # Output as JSON
  cs2pov pov -d demo.dem -p "Player" -o out.mp4
  cs2pov record -d demo.dem -p "Player" -o raw.mp4
  cs2pov trim raw.mp4 -d demo.dem -p "Player"
""",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    # Shared argument groups
    demo_args = argparse.ArgumentParser(add_help=False)
    demo_args.add_argument("-d", "--demo", required=True, type=Path,
                           help="Demo file (.dem)")

    player_args = argparse.ArgumentParser(add_help=False)
    player_args.add_argument("-p", "--player", required=True,
                             help="Player name or SteamID")

    output_args = argparse.ArgumentParser(add_help=False)
    output_args.add_argument("-o", "--output", required=True, type=Path,
                             help="Output video file")

    recording_args = argparse.ArgumentParser(add_help=False)
    recording_args.add_argument("-r", "--resolution", default="1920x1080",
                                help="Recording resolution (default: 1920x1080)")
    recording_args.add_argument("-f", "--framerate", type=int, default=60,
                                help="Recording framerate (default: 60)")
    recording_args.add_argument("--display", type=int, default=0,
                                help="X display number (default: 0)")
    recording_args.add_argument("--no-hud", action="store_true",
                                help="Hide HUD elements")
    recording_args.add_argument("--no-audio", action="store_true",
                                help="Disable audio recording")
    recording_args.add_argument("--audio-device",
                                help="PulseAudio device (auto-detected)")
    recording_args.add_argument("--cs2-path", type=Path,
                                help="Custom CS2 installation path")

    verbose_args = argparse.ArgumentParser(add_help=False)
    verbose_args.add_argument("-v", "--verbose", action="store_true",
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
    pov_parser.add_argument("--no-trim", action="store_true",
                            help="Skip post-processing trim")

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
    # Fallback options
    trim_parser.add_argument("--console-log", type=Path,
                             help="Console.log file (fallback when demo unavailable)")
    trim_parser.add_argument("--player-slot", type=int,
                             help="Player slot, 0-based (fallback)")
    trim_parser.add_argument("--recording-start-time", type=float,
                             help="Recording start timestamp (fallback)")
    trim_parser.add_argument("--startup-time", type=float,
                             help="Override startup time (seconds from video start to demo start)")

    return parser


# =============================================================================
# Command Handlers
# =============================================================================

def cmd_info(args) -> int:
    """Handle 'info' command - show demo information."""
    demo_path = args.demo.resolve()
    if not demo_path.exists():
        print(f"Error: Demo not found: {demo_path}", file=sys.stderr)
        return 1

    # Parse basic demo info
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


def cmd_pov(args) -> int:
    """Handle 'pov' command - full recording pipeline."""
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
    )

    # Post-process
    if result.success and not args.no_trim:
        postprocess_video(
            video_path=result.video_path,
            console_log_path=result.console_log_path,
            player_slot=result.player_slot,
            recording_start_time=result.recording_start_time,
            verbose=args.verbose,
            timeline=result.timeline,
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


def cmd_record(args) -> int:
    """Handle 'record' command - raw recording without trimming."""
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


def cmd_trim(args) -> int:
    """Handle 'trim' command - post-process existing video."""
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

    # Parse demo and find player
    try:
        demo_info = parse_demo(demo_path)
        player = find_player(demo_info, args.player)
        player_slot = get_player_index(demo_info, player)
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
        print("Falling back to console.log method")

    # Validate fallback parameters if needed
    if timeline is None:
        if args.console_log is None:
            print("Error: --console-log required when demo preprocessing fails", file=sys.stderr)
            return 1
        if args.player_slot is None:
            print("Error: --player-slot required when demo preprocessing fails", file=sys.stderr)
            return 1
        if args.recording_start_time is None:
            print("Error: --recording-start-time required when demo preprocessing fails", file=sys.stderr)
            return 1
        player_slot = args.player_slot

    # Copy video to output path first (postprocess_video expects to rename)
    if video_path != output_path:
        shutil.copy2(video_path, output_path)

    # Run trimming
    postprocess_video(
        video_path=output_path,
        console_log_path=args.console_log.resolve() if args.console_log else Path("/dev/null"),
        player_slot=player_slot,
        recording_start_time=args.recording_start_time or 0.0,
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
        if args.command == "info":
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
