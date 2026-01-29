"""Demo parsing with demoparser2."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import re

from demoparser2 import DemoParser

from .exceptions import DemoNotFoundError, DemoParseError, PlayerNotFoundError


@dataclass
class PlayerInfo:
    """Information about a player in the demo."""

    steamid: int
    name: str
    team: Optional[str] = None


@dataclass
class RoundInfo:
    """Information about a round in the demo."""

    round_num: int
    start_tick: int
    end_tick: Optional[int] = None


@dataclass
class DemoInfo:
    """Parsed demo file information."""

    path: Path
    map_name: str
    total_ticks: int
    tick_rate: float
    players: list[PlayerInfo] = field(default_factory=list)
    rounds: list[RoundInfo] = field(default_factory=list)


def parse_demo(demo_path: Path) -> DemoInfo:
    """Parse demo file and extract metadata, players, and round info.

    Args:
        demo_path: Path to the demo file

    Returns:
        DemoInfo with parsed demo data

    Raises:
        DemoNotFoundError: If demo file doesn't exist
        DemoParseError: If parsing fails
    """
    if not demo_path.exists():
        raise DemoNotFoundError(f"Demo file not found: {demo_path}")

    try:
        parser = DemoParser(str(demo_path))

        # Parse header for basic info
        header = parser.parse_header()
        map_name = header.get("map_name", "unknown")
        tick_rate = header.get("playback_ticks_per_second", 64)
        total_ticks = header.get("playback_ticks", 0)

        # Get unique players from tick data
        # parse_ticks returns a DataFrame with steamid and name columns
        tick_df = parser.parse_ticks(["team_num"])
        players = _extract_players(tick_df)

        # Get round events
        rounds = _extract_rounds(parser)

        return DemoInfo(
            path=demo_path,
            map_name=map_name,
            total_ticks=total_ticks,
            tick_rate=tick_rate,
            players=players,
            rounds=rounds,
        )
    except Exception as e:
        if isinstance(e, (DemoNotFoundError, DemoParseError)):
            raise
        raise DemoParseError(f"Failed to parse demo: {e}") from e


def _extract_players(tick_df) -> list[PlayerInfo]:
    """Extract unique players from tick data DataFrame."""
    players = []
    seen_steamids = set()

    # tick_df has columns: tick, steamid, name, and any requested fields
    if tick_df is None or len(tick_df) == 0:
        return players

    # Get unique steamid/name combinations
    for _, row in tick_df.drop_duplicates(subset=["steamid"]).iterrows():
        steamid = row.get("steamid")
        if steamid and steamid not in seen_steamids:
            seen_steamids.add(steamid)
            name = row.get("name", f"Player_{steamid}")
            team = None
            if "team_num" in row:
                team_num = row["team_num"]
                if team_num == 2:
                    team = "T"
                elif team_num == 3:
                    team = "CT"
            players.append(PlayerInfo(steamid=steamid, name=name, team=team))

    return players


def _extract_rounds(parser: DemoParser) -> list[RoundInfo]:
    """Extract round boundaries from demo events."""
    rounds = []

    try:
        # Parse round_start and round_end events
        events_df = parser.parse_event("round_start", "round_end")

        if events_df is None or len(events_df) == 0:
            return rounds

        # Group events by type
        round_starts = events_df[events_df["event_name"] == "round_start"].sort_values(
            "tick"
        )
        round_ends = events_df[events_df["event_name"] == "round_end"].sort_values(
            "tick"
        )

        # Match starts with ends
        round_num = 1
        for _, start_row in round_starts.iterrows():
            start_tick = start_row["tick"]

            # Find matching end (first end after this start)
            end_tick = None
            for _, end_row in round_ends.iterrows():
                if end_row["tick"] > start_tick:
                    end_tick = end_row["tick"]
                    break

            rounds.append(
                RoundInfo(round_num=round_num, start_tick=start_tick, end_tick=end_tick)
            )
            round_num += 1

    except Exception:
        # If event parsing fails, return empty rounds list
        pass

    return rounds


def find_player(demo_info: DemoInfo, identifier: str) -> PlayerInfo:
    """Find a player by name or SteamID.

    Args:
        demo_info: Parsed demo information
        identifier: Player name or SteamID string

    Returns:
        PlayerInfo for the matched player

    Raises:
        PlayerNotFoundError: If player not found
    """
    if not demo_info.players:
        raise PlayerNotFoundError("No players found in demo")

    # Try parsing as SteamID first
    steamid = _parse_steamid(identifier)
    if steamid is not None:
        for player in demo_info.players:
            if player.steamid == steamid:
                return player
        raise PlayerNotFoundError(f"Player with SteamID {steamid} not found in demo")

    # Try name match (case-insensitive)
    identifier_lower = identifier.lower()
    for player in demo_info.players:
        if player.name.lower() == identifier_lower:
            return player

    # Try partial name match
    for player in demo_info.players:
        if identifier_lower in player.name.lower():
            return player

    # List available players in error message
    player_list = ", ".join(f"{p.name} ({p.steamid})" for p in demo_info.players)
    raise PlayerNotFoundError(
        f"Player '{identifier}' not found. Available players: {player_list}"
    )


def _parse_steamid(identifier: str) -> Optional[int]:
    """Parse various SteamID formats to SteamID64.

    Supports:
    - Raw SteamID64: 76561198012345678
    - STEAM_X:Y:Z format: STEAM_0:1:12345678
    - [U:1:X] format: [U:1:12345678]
    """
    # Try raw int64
    try:
        steamid = int(identifier)
        if steamid > 76561197960265728:  # Base SteamID64 value
            return steamid
    except ValueError:
        pass

    # Try STEAM_X:Y:Z format
    match = re.match(r"STEAM_(\d):(\d):(\d+)", identifier, re.IGNORECASE)
    if match:
        y = int(match.group(2))
        z = int(match.group(3))
        return 76561197960265728 + z * 2 + y

    # Try [U:1:X] format
    match = re.match(r"\[U:1:(\d+)\]", identifier)
    if match:
        account_id = int(match.group(1))
        return 76561197960265728 + account_id

    return None


def get_player_index(demo_info: DemoInfo, player: PlayerInfo) -> int:
    """Get the player's index in the player list (0-based).

    This is used for spec_player command which takes a slot number.
    Note: The actual spec_player slot may differ from this index
    depending on how CS2 assigns slots during demo playback.
    """
    for i, p in enumerate(demo_info.players):
        if p.steamid == player.steamid:
            return i
    return 0
