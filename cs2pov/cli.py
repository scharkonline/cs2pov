#!/usr/bin/env python3
"""CS2 POV Recorder - Record player POV from CS2 demo files.

Simplified architecture with linear flow:
1. Setup (parse demo, generate config, launch CS2)
2. Recording loop (single-threaded, blocking)
3. Cleanup (stop FFmpeg, kill CS2)
4. Post-processing (trim death periods)
"""

import argparse
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import __version__
from .automation import check_xdotool, send_key, check_demo_ended, wait_for_cs2_window, wait_for_map_load
from .capture import FFmpegCapture, get_default_audio_monitor
from .config import RecordingConfig, generate_recording_cfg
from .exceptions import CS2POVError
from .game import CS2Process, find_cs2_path, get_cfg_dir, get_demo_dir
from .parser import DemoInfo, find_player, get_player_index, parse_demo
from .trim import extract_death_periods, trim_death_periods


@dataclass
class RecordingResult:
    """Result of recording phase with metadata for post-processing."""
    success: bool
    video_path: Path
    console_log_path: Path
    recording_start_time: float
    player_slot: int
    exit_reason: str  # "demo_ended", "timeout", "ffmpeg_stopped", "interrupted"


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


def print_demo_info(demo_info: DemoInfo):
    """Print parsed demo information."""
    print(f"  Map: {demo_info.map_name}")
    print(f"  Ticks: {demo_info.total_ticks}")
    print(f"  Tick rate: {demo_info.tick_rate}")
    print(f"  Players: {len(demo_info.players)}")
    for p in demo_info.players:
        team_str = f" [{p.team}]" if p.team else ""
        print(f"    - {p.name}{team_str} ({p.steamid})")
    print(f"  Rounds: {len(demo_info.rounds)}")


def recording_loop(
    display: str,
    console_log_path: Path,
    cs2_process: CS2Process,
    ffmpeg: FFmpegCapture,
    timeout: float,
    verbose: bool = False,
) -> str:
    """Main recording loop - single-threaded, blocking.

    Args:
        display: X display string
        console_log_path: Path to CS2 console.log
        cs2_process: CS2 process manager
        ffmpeg: FFmpeg capture instance
        timeout: Maximum recording time in seconds
        verbose: Print verbose output

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

        # Check timeout
        if elapsed > timeout:
            print(f"  Timeout reached ({timeout/60:.1f} min)")
            return "timeout"

        # Check if CS2 still running
        if not cs2_process.is_running():
            print("  CS2 exited")
            return "cs2_exited"

        # Check if FFmpeg still running
        if not ffmpeg.is_running():
            print("  FFmpeg stopped unexpectedly")
            return "ffmpeg_stopped"

        # Check for demo end in console.log
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
            last_spec_lock = elapsed

        # Print status every 60 seconds
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
    """Record a player's POV from a demo file.

    This is a blocking function that handles the entire recording process.
    Cleanup is handled in finally block - always runs.
    """
    # Find CS2 installation
    print("Finding CS2 installation...")
    cs2_path = find_cs2_path(cs2_path_override)
    print(f"  Found: {cs2_path}")

    # Parse demo
    print(f"Parsing demo: {demo_path.name}")
    demo_info = parse_demo(demo_path)
    if verbose:
        print_demo_info(demo_info)
    else:
        print(f"  Map: {demo_info.map_name}, {len(demo_info.players)} players")
        print(f"  Ticks: {demo_info.total_ticks}, Rate: {demo_info.tick_rate}/s")

    # Find target player
    player = find_player(demo_info, player_identifier)
    player_index = get_player_index(demo_info, player)
    print(f"Recording POV: {player.name} (SteamID: {player.steamid}, index: {player_index})")

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
    timeout = estimated_duration + 600  # Add 10 min buffer

    print(f"Starting recording (timeout: {timeout/60:.1f} min)...")
    print(f"  Display: {display_str}")
    print(f"  Output: {output_path}")

    # Initialize objects
    cs2_process: Optional[CS2Process] = None
    ffmpeg: Optional[FFmpegCapture] = None
    recording_start_time = 0.0
    exit_reason = "unknown"

    try:
        # Delete old console.log to ensure fresh log for this recording
        if console_log_path.exists():
            console_log_path.unlink()
            if verbose:
                print(f"  Deleted old console.log")

        # Launch CS2
        cs2_process = CS2Process(cs2_path, display_str, log_path=cs2_log_path)
        cs2_process.launch("cs2pov_recording.cfg")
        print("  CS2 launching via Steam...")

        # Wait for CS2 window to appear
        print("  Waiting for CS2 window...")
        window_id = wait_for_cs2_window(display_str, timeout=120)
        if window_id:
            print(f"  CS2 window ready")
        else:
            print("  Warning: CS2 window not detected, continuing anyway")

        # Wait for map to load, then hide demo UI with Shift+F2
        print("  Waiting for map to load...")
        if wait_for_map_load(console_log_path, timeout=120):
            if verbose:
                print("  Map loaded, waiting 10s before hiding UI...")
            time.sleep(10)
            if window_id and send_key("shift+F2", display_str, window_id):
                if verbose:
                    print("  Sent Shift+F2 to hide demo UI")
            elif window_id:
                print("  Warning: Failed to send Shift+F2 to hide demo UI")
        else:
            print("  Warning: Map load not detected, continuing anyway")

        # Determine audio source
        audio_source = None
        if enable_audio:
            audio_source = audio_device or get_default_audio_monitor()
            if audio_source:
                if verbose:
                    print(f"  Audio device: {audio_source}")
            else:
                print("  Warning: Could not detect audio device, recording video only")

        # Start FFmpeg capture (full display + audio)
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

        # Record start time for death period extraction
        recording_start_time = time.time()

        # Run main recording loop (blocking)
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
        # Cleanup - always runs
        print("\nCleaning up...")

        # Stop FFmpeg first (so video file is finalized)
        if ffmpeg:
            print("  Stopping FFmpeg...")
            graceful = ffmpeg.stop(timeout=15)
            if graceful:
                print("  FFmpeg stopped gracefully")
            else:
                print("  FFmpeg force-stopped")
            if ffmpeg.stderr_path and ffmpeg.stderr_path.exists():
                print(f"  FFmpeg log: {ffmpeg.stderr_path}")

        # Then stop CS2
        if cs2_process and cs2_process.is_running():
            print("  Terminating CS2...")
            cs2_process.terminate()

        print("  Cleanup complete")

        # Save console.log with demo-matching name for retention
        if console_log_path.exists():
            saved_log_path = output_path.parent / f"console_{demo_path.stem}.log"
            shutil.copy2(console_log_path, saved_log_path)
            print(f"  Console log saved: {saved_log_path.name}")
            # Update path for post-processing to use the saved copy
            console_log_path = saved_log_path

    # Return result with metadata for post-processing
    success = exit_reason in ("demo_ended", "cs2_exited")
    return RecordingResult(
        success=success,
        video_path=output_path,
        console_log_path=console_log_path,
        recording_start_time=recording_start_time,
        player_slot=player_index,
        exit_reason=exit_reason,
    )


def postprocess_video(
    video_path: Path,
    console_log_path: Path,
    player_slot: int,
    recording_start_time: float,
    verbose: bool = False,
) -> Path:
    """Post-process a recorded video to trim death periods.

    This is a separate phase that can be run independently.
    """
    if not video_path.exists():
        print(f"Error: Video file not found: {video_path}")
        return video_path

    raw_size_mb = video_path.stat().st_size / (1024 * 1024)
    print(f"\nRaw recording: {video_path} ({raw_size_mb:.1f} MB)")

    print("\nPost-processing: extracting death periods...")
    if verbose:
        print(f"  Console log: {console_log_path}")
        print(f"  Player slot: {player_slot}")
        print(f"  Recording start: {recording_start_time}")

    if not console_log_path.exists():
        print(f"  Console log not found, skipping trim")
        return video_path

    death_periods = extract_death_periods(
        log_path=console_log_path,
        player_slot=player_slot,
        recording_start_time=recording_start_time,
        verbose=verbose
    )

    if death_periods:
        print(f"  Found {len(death_periods)} death periods")
        total_dead_time = sum(p.duration for p in death_periods)
        print(f"  Total dead time: {total_dead_time:.1f}s")

        # Rename original to _raw
        raw_path = video_path.parent / f"{video_path.stem}_raw{video_path.suffix}"
        video_path.rename(raw_path)
        print(f"  Raw recording moved to: {raw_path.name}")

        print("  Trimming death periods...")
        success = trim_death_periods(
            input_path=raw_path,
            output_path=video_path,
            death_periods=death_periods,
            verbose=verbose
        )

        if success and video_path.exists():
            final_size_mb = video_path.stat().st_size / (1024 * 1024)
            print(f"\nFinal recording: {video_path} ({final_size_mb:.1f} MB)")
        else:
            print(f"\nTrimming failed, restoring raw recording")
            if not video_path.exists() and raw_path.exists():
                raw_path.rename(video_path)
    else:
        print("  No death periods found, keeping original")
        print(f"\nRecording saved: {video_path} ({raw_size_mb:.1f} MB)")

    return video_path


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Record a player's POV from a CS2 demo file",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  %(prog)s -d match.dem -p "PlayerName" -o recording.mp4
  %(prog)s -d match.dem -p "PlayerName" -o recording.mp4 --no-trim
  %(prog)s -d match.dem -p "PlayerName" -o recording.mp4 --skip-recording \\
    --console-log /path/to/console.log --player-slot 3 --recording-start-time 1706500000
""",
    )

    parser.add_argument("-d", "--demo", required=True, type=Path, help="Path to demo file (.dem)")
    parser.add_argument("-p", "--player", required=True, help="Player to record (name or SteamID)")
    parser.add_argument("-o", "--output", required=True, type=Path, help="Output video file path")
    parser.add_argument("-r", "--resolution", default="1920x1080", help="Recording resolution")
    parser.add_argument("-f", "--framerate", type=int, default=60, help="Recording framerate")
    parser.add_argument("--no-hud", action="store_true", help="Hide HUD elements")
    parser.add_argument("--display", type=int, default=0, help="X display number")
    parser.add_argument("--no-audio", action="store_true", help="Disable audio recording")
    parser.add_argument("--audio-device", help="PulseAudio device for audio capture (auto-detected by default)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--cs2-path", type=Path, help="Path to CS2 installation")
    parser.add_argument("--no-trim", action="store_true", help="Skip post-processing trim")
    parser.add_argument("--skip-recording", action="store_true", help="Skip recording, only post-process")
    parser.add_argument("--console-log", type=Path, help="Console log path (for --skip-recording)")
    parser.add_argument("--player-slot", type=int, help="Player slot (for --skip-recording)")
    parser.add_argument("--recording-start-time", type=float, help="Recording start time (for --skip-recording)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    # Subcommand for listing players
    subparsers = parser.add_subparsers(dest="command")
    list_parser = subparsers.add_parser("list", help="List players in a demo")
    list_parser.add_argument("demo", type=Path, help="Path to demo file")

    args = parser.parse_args()

    # Handle list subcommand
    if args.command == "list":
        try:
            demo_info = parse_demo(args.demo)
            print(f"Demo: {args.demo.name}")
            print_demo_info(demo_info)
            return 0
        except CS2POVError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

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
        print(f"Error: Demo file not found: {demo_path}", file=sys.stderr)
        return 1

    output_path = args.output.resolve()

    try:
        resolution = parse_resolution(args.resolution)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Handle --skip-recording mode
    if args.skip_recording:
        if not args.console_log:
            print("Error: --skip-recording requires --console-log", file=sys.stderr)
            return 1
        if args.player_slot is None:
            print("Error: --skip-recording requires --player-slot", file=sys.stderr)
            return 1
        if args.recording_start_time is None:
            print("Error: --skip-recording requires --recording-start-time", file=sys.stderr)
            return 1
        if not output_path.exists():
            print(f"Error: Video file not found: {output_path}", file=sys.stderr)
            return 1

        print("Post-processing only (--skip-recording mode)")
        postprocess_video(
            video_path=output_path,
            console_log_path=args.console_log.resolve(),
            player_slot=args.player_slot,
            recording_start_time=args.recording_start_time,
            verbose=args.verbose,
        )
        return 0

    # Phase 1-3: Record (with built-in cleanup)
    try:
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
    except CS2POVError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Phase 4: Post-process (completely separate)
    if result.success and not args.no_trim:
        postprocess_video(
            video_path=result.video_path,
            console_log_path=result.console_log_path,
            player_slot=result.player_slot,
            recording_start_time=result.recording_start_time,
            verbose=args.verbose,
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


if __name__ == "__main__":
    sys.exit(main())
