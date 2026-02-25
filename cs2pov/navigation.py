"""Tick-based demo navigation - skip death periods in real-time.

Instead of recording the full demo and trimming in post-processing,
this module detects deaths via console.log and uses demo_gototick to
skip to the next alive segment. Transition artifacts (the pause/seek/unpause
period) are trimmed in lightweight post-processing.
"""

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .automation import (
    check_death_in_console,
    check_demo_ended_tick_aware,
    calibrate_tick_offset,
    find_cs2_window,
    send_console_command,
    send_key,
)
from .capture import FFmpegCapture
from .game import CS2Process
from .preprocessor import DemoTimeline


@dataclass
class GotoTransition:
    """A demo_gototick transition during recording.

    Records the video timestamps of when the demo was paused and unpaused,
    so post-processing can cut out the transition artifact.
    """
    pause_video_time: float    # Seconds since recording start
    unpause_video_time: float  # Seconds since recording start
    from_tick: int
    to_tick: int


@dataclass
class NavigationState:
    """State for tick-based navigation during recording."""
    timeline: DemoTimeline
    player_slot: int              # 0-based, for console.log pattern matching
    tick_offset: int = 0          # Calibrated offset (actual - expected)
    current_segment_index: int = 0
    transitions: list[GotoTransition] = field(default_factory=list)
    recording_start_time: float = 0.0
    buffer_seconds: float = 5.0


def compute_goto_tick(target_start_tick: int, tick_offset: int) -> int:
    """Compute the adjusted tick for demo_gototick.

    Args:
        target_start_tick: The desired target tick (from alive segment)
        tick_offset: Calibrated offset to subtract

    Returns:
        Adjusted tick value for demo_gototick command
    """
    return max(0, target_start_tick - tick_offset)


def handle_death(
    state: NavigationState,
    display: str,
    window_id: str,
    console_log_path: Path,
    log_position: int,
    verbose: bool = False,
) -> tuple[bool, int]:
    """Handle a detected player death by seeking to next alive segment.

    Sequence:
    1. Pause demo (F7)
    2. Find next alive segment
    3. demo_gototick to next segment start
    4. Wait for seek to complete
    5. Re-lock spectator (F5)
    6. Unpause (F6)

    Args:
        state: Current navigation state
        display: X display string
        window_id: CS2 window ID
        console_log_path: Path to console.log
        log_position: Current log file position
        verbose: Print debug output

    Returns:
        (has_more_segments, new_log_position)
        False means demo is effectively over (no more alive segments)
    """
    pause_video_time = time.time() - state.recording_start_time

    # Pause demo
    send_key("F7", display, window_id)
    time.sleep(0.3)

    # Advance to next segment
    state.current_segment_index += 1

    if state.current_segment_index >= len(state.timeline.alive_segments):
        if verbose:
            print(f"    No more alive segments, recording complete")
        # Record transition even for the final death (trim trailing dead time)
        state.transitions.append(GotoTransition(
            pause_video_time=pause_video_time,
            unpause_video_time=pause_video_time,  # No unpause, we're done
            from_tick=0,
            to_tick=0,
        ))
        return False, log_position

    next_segment = state.timeline.alive_segments[state.current_segment_index]
    target_tick = next_segment.start_tick
    adjusted_tick = compute_goto_tick(target_tick, state.tick_offset)

    if verbose:
        print(f"    Death detected! Seeking to segment {state.current_segment_index + 1} "
              f"(tick {target_tick}, adjusted {adjusted_tick})")

    # Send demo_gototick command
    send_console_command(f"demo_gototick {adjusted_tick}", display, window_id)

    # Wait for seek to complete
    time.sleep(2.0)

    # Re-lock spectator
    send_key("F5", display, window_id)
    time.sleep(0.3)

    # Unpause
    send_key("F6", display, window_id)

    unpause_video_time = time.time() - state.recording_start_time

    # Record transition
    state.transitions.append(GotoTransition(
        pause_video_time=pause_video_time,
        unpause_video_time=unpause_video_time,
        from_tick=state.timeline.alive_segments[state.current_segment_index - 1].end_tick
            if state.current_segment_index > 0 else 0,
        to_tick=target_tick,
    ))

    if verbose:
        print(f"    Transition: paused at {pause_video_time:.1f}s, "
              f"unpaused at {unpause_video_time:.1f}s "
              f"({unpause_video_time - pause_video_time:.1f}s gap)")

    return True, log_position


def recording_loop_tick_nav(
    display: str,
    console_log_path: Path,
    cs2_process: CS2Process,
    ffmpeg: FFmpegCapture,
    state: NavigationState,
    timeout: float,
    verbose: bool = False,
) -> tuple[str, list[GotoTransition]]:
    """Recording loop with tick-based navigation (skips death periods).

    Replaces the standard recording_loop when --tick-nav is enabled.
    Detects deaths via console.log and uses demo_gototick to skip to
    the next alive segment.

    Args:
        display: X display string
        console_log_path: Path to CS2 console.log
        cs2_process: Running CS2 process
        ffmpeg: Running FFmpeg capture
        state: Navigation state with timeline and calibration data
        timeout: Maximum recording time in seconds
        verbose: Print debug output

    Returns:
        (exit_reason, transitions) where exit_reason is one of:
        "demo_ended", "segments_complete", "cs2_exited", "timeout", "ffmpeg_stopped"
    """
    start_time = time.time()
    state.recording_start_time = start_time
    last_spec_lock = 0.0
    log_position = 0
    death_log_position = 0
    window_id = None
    window_found_logged = False
    last_status_time = start_time

    print("  Recording loop started (tick navigation enabled)")

    # Find CS2 window
    window_id = find_cs2_window(display)
    if window_id:
        if verbose:
            print(f"    CS2 window found: {window_id}")
    else:
        print("    Warning: CS2 window not found for tick navigation")

    # TODO: Tick offset calibration disabled — demo_gototick appears to land
    # accurately without correction. Re-enable if drift is observed.
    # if window_id:
    #     print("  Calibrating tick offset...")
    #     state.tick_offset, log_position = calibrate_tick_offset(
    #         console_log_path, display, window_id, log_position, verbose
    #     )
    #     death_log_position = log_position
    #     print(f"  Tick offset: {state.tick_offset}")
    state.tick_offset = 0

    # Compute minimum tick that indicates a real demo end (not a navigation pause).
    # Any "paused on tick X" where X is below this is from our own calibration/navigation.
    last_segment = state.timeline.alive_segments[-1]
    min_end_tick = last_segment.end_tick
    if verbose:
        print(f"    Demo end threshold: tick >= {min_end_tick}")

    print(f"  {len(state.timeline.alive_segments)} alive segments to navigate")

    while True:
        elapsed = time.time() - start_time

        if elapsed > timeout:
            print(f"  Timeout reached ({timeout / 60:.1f} min)")
            return "timeout", state.transitions

        if not cs2_process.is_running():
            print("  CS2 exited")
            return "cs2_exited", state.transitions

        if not ffmpeg.is_running():
            print("  FFmpeg stopped unexpectedly")
            return "ffmpeg_stopped", state.transitions

        # Check for demo end — only if paused tick is past our last alive segment
        demo_ended, log_position = check_demo_ended_tick_aware(
            console_log_path, log_position, min_end_tick
        )
        if demo_ended:
            print("  Demo end detected in console.log")
            return "demo_ended", state.transitions

        # Check for death (only if we have a window to navigate with)
        if window_id:
            death_detected, death_log_position = check_death_in_console(
                console_log_path, state.player_slot, death_log_position
            )
            if death_detected:
                has_more, death_log_position = handle_death(
                    state, display, window_id, console_log_path,
                    death_log_position, verbose
                )
                if not has_more:
                    print("  All alive segments complete")
                    return "segments_complete", state.transitions

        # Send spec_lock (F5) every 3 seconds
        if elapsed - last_spec_lock >= 3.0:
            if window_id is None:
                window_id = find_cs2_window(display)
                if window_id and not window_found_logged:
                    if verbose:
                        print(f"    CS2 window found: {window_id}")
                    window_found_logged = True

            if window_id:
                send_key("F5", display, window_id)
            last_spec_lock = elapsed

        if elapsed - last_status_time >= 60:
            seg = state.current_segment_index + 1
            total = len(state.timeline.alive_segments)
            print(f"    Still recording... ({elapsed / 60:.1f} min, "
                  f"segment {seg}/{total})")
            last_status_time = elapsed

        time.sleep(0.5)
