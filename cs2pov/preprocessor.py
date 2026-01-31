"""Demo preprocessor - extract timeline data using demoparser2.

Uses demoparser2 to extract death/spawn events and round boundaries
directly from the demo file, providing tick-level precision without
depending on console.log parsing.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from demoparser2 import DemoParser

from .exceptions import DemoNotFoundError, DemoParseError


@dataclass
class DeathEvent:
    """A player death event from the demo."""

    tick: int
    time_seconds: float
    attacker_steamid: Optional[int] = None
    weapon: Optional[str] = None
    headshot: bool = False


@dataclass
class SpawnEvent:
    """A player spawn event from the demo."""

    tick: int
    time_seconds: float


@dataclass
class DeathPeriod:
    """A period when the player was dead (between death and respawn)."""

    death: DeathEvent
    spawn: SpawnEvent

    @property
    def duration_ticks(self) -> int:
        return self.spawn.tick - self.death.tick

    @property
    def duration_seconds(self) -> float:
        return self.spawn.time_seconds - self.death.time_seconds


@dataclass
class RoundBoundary:
    """Boundaries for a round in the demo."""

    round_num: int
    prestart_tick: int
    prestart_time: float
    freeze_end_tick: Optional[int] = None
    freeze_end_time: Optional[float] = None
    end_tick: Optional[int] = None
    end_time: Optional[float] = None


@dataclass
class RoundEndPeriod:
    """Dead time between round end and next round start."""

    round_end_tick: int
    round_end_time: float
    next_round_tick: int  # freeze_end - buffer
    next_round_time: float

    @property
    def duration_seconds(self) -> float:
        return self.next_round_time - self.round_end_time


@dataclass
class DemoTimeline:
    """Complete timeline data for a player in a demo."""

    player_steamid: int
    player_name: str
    tickrate: float
    total_ticks: int
    total_duration: float

    deaths: list[DeathEvent] = field(default_factory=list)
    spawns: list[SpawnEvent] = field(default_factory=list)
    death_periods: list[DeathPeriod] = field(default_factory=list)
    rounds: list[RoundBoundary] = field(default_factory=list)
    round_end_periods: list[RoundEndPeriod] = field(default_factory=list)


def preprocess_demo(demo_path: Path, player_steamid: int, player_name: str = "") -> DemoTimeline:
    """Pre-process demo to extract timeline data for a player.

    Uses demoparser2 to extract:
    - player_death events for the target player
    - player_spawn events for the target player
    - Round boundaries (prestart, freeze_end, end)

    Args:
        demo_path: Path to the demo file
        player_steamid: SteamID64 of the player to track
        player_name: Player name (for metadata)

    Returns:
        DemoTimeline with all extracted data

    Raises:
        DemoNotFoundError: If demo file doesn't exist
        DemoParseError: If parsing fails
    """
    if not demo_path.exists():
        raise DemoNotFoundError(f"Demo file not found: {demo_path}")

    try:
        parser = DemoParser(str(demo_path))

        # Get header for tickrate and duration
        header = parser.parse_header()
        tickrate = header.get("playback_ticks_per_second", 64)
        total_ticks = header.get("playback_ticks", 0)
        total_duration = total_ticks / tickrate if tickrate > 0 else 0

        # Extract death events for the player
        deaths = _extract_deaths(parser, player_steamid, tickrate)

        # Extract spawn events for the player
        spawns = _extract_spawns(parser, player_steamid, tickrate)

        # Extract round boundaries
        rounds = _extract_round_boundaries(parser, tickrate)

        # Compute death periods using rounds (death -> 5s before next round freeze_end)
        # This is more reliable than spawn events
        death_periods = compute_death_periods_from_rounds(deaths, rounds, tickrate, buffer_seconds=5.0)

        # Fallback to spawn-based if rounds didn't work
        if not death_periods and deaths and spawns:
            death_periods = compute_death_periods(deaths, spawns)

        # Compute round end periods (round_end -> 5s before next round freeze_end)
        # This trims dead time between rounds when the player survives
        round_end_periods = compute_round_end_periods(rounds, tickrate, buffer_before_next=5.0, buffer_after_end=2.0)

        return DemoTimeline(
            player_steamid=player_steamid,
            player_name=player_name,
            tickrate=tickrate,
            total_ticks=total_ticks,
            total_duration=total_duration,
            deaths=deaths,
            spawns=spawns,
            death_periods=death_periods,
            rounds=rounds,
            round_end_periods=round_end_periods,
        )

    except Exception as e:
        if isinstance(e, (DemoNotFoundError, DemoParseError)):
            raise
        raise DemoParseError(f"Failed to preprocess demo: {e}") from e


def _extract_deaths(parser: DemoParser, player_steamid: int, tickrate: float) -> list[DeathEvent]:
    """Extract death events for a specific player."""
    deaths = []

    try:
        events_df = parser.parse_event("player_death")
        if events_df is None or len(events_df) == 0:
            return deaths

        # Filter for deaths of the target player
        # user_steamid is the player who died
        # Note: event steamids are strings, tick data steamids are uint64
        steamid_str = str(player_steamid)
        player_deaths = events_df[events_df["user_steamid"] == steamid_str]

        for _, row in player_deaths.iterrows():
            tick = int(row["tick"])
            time_seconds = tick / tickrate if tickrate > 0 else 0

            # Extract optional fields
            attacker_steamid = row.get("attacker_steamid")
            if attacker_steamid is not None and attacker_steamid != 0:
                attacker_steamid = int(attacker_steamid)
            else:
                attacker_steamid = None

            weapon = row.get("weapon")
            if weapon is not None:
                weapon = str(weapon)

            headshot = bool(row.get("headshot", False))

            deaths.append(DeathEvent(
                tick=tick,
                time_seconds=time_seconds,
                attacker_steamid=attacker_steamid,
                weapon=weapon,
                headshot=headshot,
            ))

    except Exception:
        # If event parsing fails, return empty list
        pass

    # Sort by tick to ensure chronological order
    deaths.sort(key=lambda d: d.tick)
    return deaths


def _extract_spawns(parser: DemoParser, player_steamid: int, tickrate: float) -> list[SpawnEvent]:
    """Extract spawn events for a specific player."""
    spawns = []

    try:
        events_df = parser.parse_event("player_spawn")
        if events_df is None or len(events_df) == 0:
            return spawns

        # Filter for spawns of the target player
        # Note: event steamids are strings, tick data steamids are uint64
        steamid_str = str(player_steamid)
        player_spawns = events_df[events_df["user_steamid"] == steamid_str]

        for _, row in player_spawns.iterrows():
            tick = int(row["tick"])
            time_seconds = tick / tickrate if tickrate > 0 else 0

            spawns.append(SpawnEvent(
                tick=tick,
                time_seconds=time_seconds,
            ))

    except Exception:
        # If event parsing fails, return empty list
        pass

    # Sort by tick to ensure chronological order
    spawns.sort(key=lambda s: s.tick)
    return spawns


def _extract_round_boundaries(parser: DemoParser, tickrate: float) -> list[RoundBoundary]:
    """Extract round boundary events."""
    rounds = []

    try:
        # Parse each event type individually (parse_event only takes one event name)
        prestart_df = parser.parse_event("round_prestart")
        freeze_end_df = parser.parse_event("round_freeze_end")

        # Try multiple event names for round end (CS2 may use different names)
        round_end_df = parser.parse_event("round_end")
        if round_end_df is None or len(round_end_df) == 0:
            round_end_df = parser.parse_event("round_officially_ended")

        # Convert to lists of (tick, time) tuples
        prestart_list = []
        if prestart_df is not None and len(prestart_df) > 0:
            prestart_df = prestart_df.sort_values("tick")
            prestart_list = [(int(row["tick"]), int(row["tick"]) / tickrate) for _, row in prestart_df.iterrows()]

        freeze_end_list = []
        if freeze_end_df is not None and len(freeze_end_df) > 0:
            freeze_end_df = freeze_end_df.sort_values("tick")
            freeze_end_list = [(int(row["tick"]), int(row["tick"]) / tickrate) for _, row in freeze_end_df.iterrows()]

        round_end_list = []
        if round_end_df is not None and len(round_end_df) > 0:
            round_end_df = round_end_df.sort_values("tick")
            round_end_list = [(int(row["tick"]), int(row["tick"]) / tickrate) for _, row in round_end_df.iterrows()]

        if not prestart_list:
            return rounds

        # Match events to form round boundaries
        for round_num, (prestart_tick, prestart_time) in enumerate(prestart_list, start=1):
            round_boundary = RoundBoundary(
                round_num=round_num,
                prestart_tick=prestart_tick,
                prestart_time=prestart_time,
            )

            # Find freeze_end after this prestart
            for freeze_tick, freeze_time in freeze_end_list:
                if freeze_tick > prestart_tick:
                    round_boundary.freeze_end_tick = freeze_tick
                    round_boundary.freeze_end_time = freeze_time
                    break

            # Find round_end after this prestart (but before next prestart)
            next_prestart_tick = float("inf")
            if round_num < len(prestart_list):
                next_prestart_tick = prestart_list[round_num][0]

            for end_tick, end_time in round_end_list:
                if prestart_tick < end_tick < next_prestart_tick:
                    round_boundary.end_tick = end_tick
                    round_boundary.end_time = end_time
                    break

            rounds.append(round_boundary)

    except Exception:
        # If event parsing fails, return empty list
        pass

    return rounds


def compute_death_periods(deaths: list[DeathEvent], spawns: list[SpawnEvent]) -> list[DeathPeriod]:
    """Match each death with its subsequent spawn to form death periods.

    For each death, finds the next spawn that occurs after it.
    Deaths without a subsequent spawn (e.g., last death of match) are ignored.

    Args:
        deaths: List of death events, sorted by tick
        spawns: List of spawn events, sorted by tick

    Returns:
        List of DeathPeriod objects
    """
    if not deaths or not spawns:
        return []

    death_periods = []
    spawn_idx = 0

    for death in deaths:
        # Find next spawn after this death
        while spawn_idx < len(spawns) and spawns[spawn_idx].tick <= death.tick:
            spawn_idx += 1

        if spawn_idx < len(spawns):
            spawn = spawns[spawn_idx]
            death_periods.append(DeathPeriod(death=death, spawn=spawn))
            spawn_idx += 1

    return death_periods


def compute_death_periods_from_rounds(
    deaths: list[DeathEvent],
    rounds: list[RoundBoundary],
    tickrate: float,
    buffer_seconds: float = 5.0,
) -> list[DeathPeriod]:
    """Match each death with the next round's freeze_end minus a buffer.

    Instead of using spawn events, this uses round boundaries to determine
    when to cut back in: (freeze_end - buffer_seconds).

    Args:
        deaths: List of death events, sorted by tick
        rounds: List of round boundaries, sorted by round_num
        tickrate: Demo tickrate for time calculations
        buffer_seconds: Seconds before freeze_end to cut back in (default 5s)

    Returns:
        List of DeathPeriod objects
    """
    if not deaths or not rounds:
        return []

    # Build list of freeze_end times (when rounds actually start)
    freeze_end_times = []
    for r in rounds:
        if r.freeze_end_tick is not None and r.freeze_end_time is not None:
            freeze_end_times.append((r.freeze_end_tick, r.freeze_end_time))

    if not freeze_end_times:
        return []

    # Sort by tick
    freeze_end_times.sort(key=lambda x: x[0])

    death_periods = []
    freeze_idx = 0

    for death in deaths:
        # Find next freeze_end after this death
        while freeze_idx < len(freeze_end_times) and freeze_end_times[freeze_idx][0] <= death.tick:
            freeze_idx += 1

        if freeze_idx < len(freeze_end_times):
            freeze_tick, freeze_time = freeze_end_times[freeze_idx]

            # Calculate respawn time as (freeze_end - buffer)
            buffer_ticks = int(buffer_seconds * tickrate)
            respawn_tick = freeze_tick - buffer_ticks
            respawn_time = freeze_time - buffer_seconds

            # Only create period if respawn is after death
            if respawn_tick > death.tick:
                # Create a synthetic SpawnEvent for the respawn point
                spawn = SpawnEvent(tick=respawn_tick, time_seconds=respawn_time)
                death_periods.append(DeathPeriod(death=death, spawn=spawn))

    return death_periods


def compute_round_end_periods(
    rounds: list[RoundBoundary],
    tickrate: float,
    buffer_before_next: float = 5.0,
    buffer_after_end: float = 2.0,
) -> list[RoundEndPeriod]:
    """Compute dead time periods between round end and next round start.

    For each round that has an end_tick, creates a period from
    (round_end + buffer_after_end) to (next_round_freeze_end - buffer_before_next).

    Args:
        rounds: List of round boundaries, sorted by round_num
        tickrate: Demo tickrate for time calculations
        buffer_before_next: Seconds before freeze_end to cut back in (default 5s)
        buffer_after_end: Seconds after round_end before starting trim (default 2s)

    Returns:
        List of RoundEndPeriod objects
    """
    if not rounds or len(rounds) < 2:
        return []

    round_end_periods = []
    buffer_before_ticks = int(buffer_before_next * tickrate)
    buffer_after_ticks = int(buffer_after_end * tickrate)

    for i, current_round in enumerate(rounds[:-1]):  # Skip last round (no next round)
        next_round = rounds[i + 1]

        # Need both current round's end and next round's freeze_end
        if current_round.end_tick is None or current_round.end_time is None:
            continue
        if next_round.freeze_end_tick is None or next_round.freeze_end_time is None:
            continue

        # Calculate trim start (round_end + buffer)
        trim_start_tick = current_round.end_tick + buffer_after_ticks
        trim_start_time = current_round.end_time + buffer_after_end

        # Calculate trim end (freeze_end - buffer)
        trim_end_tick = next_round.freeze_end_tick - buffer_before_ticks
        trim_end_time = next_round.freeze_end_time - buffer_before_next

        # Only create period if there's actual dead time to trim
        if trim_end_tick > trim_start_tick:
            round_end_periods.append(RoundEndPeriod(
                round_end_tick=trim_start_tick,
                round_end_time=trim_start_time,
                next_round_tick=trim_end_tick,
                next_round_time=trim_end_time,
            ))

    return round_end_periods


def get_trim_periods(timeline: DemoTimeline) -> list[tuple[float, float]]:
    """Get periods to trim from video based on timeline data.

    Uses first player_spawn as t=0 reference point. This handles the
    variable CS2 startup delay by anchoring to when the player first
    appears in the game.

    Returns (start, end) tuples for periods to remove:
    - Initial period: 0 to first_spawn_time (startup + freeze time)
    - Death periods: death_time to respawn_time for each death

    The returned times are relative to video start (not demo start).
    When recording, the first spawn in the demo corresponds to some
    point in the video. By returning demo-relative times and letting
    the caller handle the offset, we keep this function pure.

    Args:
        timeline: DemoTimeline with death/spawn data

    Returns:
        List of (start_seconds, end_seconds) tuples to trim
    """
    if not timeline.spawns:
        return []

    trim_periods = []

    # First spawn is our reference point
    first_spawn = timeline.spawns[0]
    first_spawn_time = first_spawn.time_seconds

    # Trim from demo start to first spawn (startup, freeze time, etc.)
    # Only add if > 0.5s to avoid tiny trims
    if first_spawn_time > 0.5:
        trim_periods.append((0.0, first_spawn_time))

    # Add death periods (already computed relative to demo start)
    for period in timeline.death_periods:
        # Only include if death is after first spawn
        if period.death.tick > first_spawn.tick:
            trim_periods.append((
                period.death.time_seconds,
                period.spawn.time_seconds,
            ))

    return trim_periods


def get_trim_periods_for_video(
    timeline: DemoTimeline,
    video_start_offset: float = 0.0,
) -> list[tuple[float, float]]:
    """Get trim periods adjusted for video timing.

    When recording a demo, there's a delay between video start and
    demo playback start. This function adjusts the timeline-based
    trim periods to account for that offset.

    Args:
        timeline: DemoTimeline with death/spawn data
        video_start_offset: Seconds from video start to first spawn
            in video. If not provided, assumes first spawn is at
            video_start_offset=0 (i.e., video started at first spawn).

    Returns:
        List of (start_seconds, end_seconds) tuples for video trimming
    """
    demo_periods = get_trim_periods(timeline)
    if not demo_periods:
        return []

    # Get first spawn time in demo (our reference point)
    if not timeline.spawns:
        return []

    first_spawn_demo_time = timeline.spawns[0].time_seconds

    # Adjust periods: demo time -> video time
    # If first spawn is at video_start_offset in the video,
    # then demo_time 0 is at (video_start_offset - first_spawn_demo_time)
    video_periods = []
    for start, end in demo_periods:
        # Convert demo-relative to video-relative
        video_start = start - first_spawn_demo_time + video_start_offset
        video_end = end - first_spawn_demo_time + video_start_offset

        # Only include positive time ranges
        if video_end > 0:
            video_start = max(0.0, video_start)
            video_periods.append((video_start, video_end))

    return video_periods
