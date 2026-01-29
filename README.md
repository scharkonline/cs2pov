# cs2pov

Record a specific player's POV from Counter-Strike 2 demo files.

## Installation

```bash
# Install Python dependencies
pip install -e .

# System dependencies (Gentoo)
emerge -av media-video/ffmpeg

# System dependencies (Debian/Ubuntu)
apt install ffmpeg
```

## Quick Start

```bash
# List players in a demo
cs2pov list /path/to/demo.dem

# Record a player's POV
cs2pov -d /path/to/demo.dem -p "PlayerName" -o recording.mp4
```

## Usage

```
cs2pov [-h] --demo DEMO --player PLAYER --output OUTPUT [options]
```

### Required Arguments

| Argument | Short | Description |
|----------|-------|-------------|
| `--demo` | `-d` | Path to demo file (.dem) |
| `--player` | `-p` | Player to record (name or SteamID) |
| `--output` | `-o` | Output video file path |

### Optional Arguments

| Argument | Short | Default | Description |
|----------|-------|---------|-------------|
| `--resolution` | `-r` | 1920x1080 | Recording resolution |
| `--framerate` | `-f` | 60 | Recording framerate |
| `--no-hud` | | off | Hide HUD elements (except killfeed) |
| `--display` | | 0 | X display number (0 = real display) |
| `--cs2-path` | | auto | Path to CS2 installation |
| `--verbose` | `-v` | off | Verbose output |
| `--version` | | | Show version |

### Player Identification

The `--player` argument accepts multiple formats:

- **Player name**: `"PlayerName"` (case-insensitive, partial match supported)
- **SteamID64**: `76561198012345678`
- **SteamID**: `STEAM_0:1:12345678`
- **SteamID3**: `[U:1:12345678]`

### Subcommands

#### `list` - List players in a demo

```bash
cs2pov list /path/to/demo.dem
```

Shows all players with their names, teams, and SteamIDs.

## Examples

```bash
# Basic recording
cs2pov -d match.dem -p "s1mple" -o s1mple_pov.mp4

# Record by SteamID
cs2pov -d match.dem -p 76561198012345678 -o recording.mp4

# Higher resolution, lower framerate
cs2pov -d match.dem -p "Player" -o out.mp4 -r 2560x1440 -f 30

# Hide HUD, verbose output
cs2pov -d match.dem -p "Player" -o out.mp4 --no-hud -v

# Custom CS2 installation path
cs2pov -d match.dem -p "Player" -o out.mp4 --cs2-path /mnt/games/SteamLibrary/steamapps/common/Counter-Strike\ Global\ Offensive

# Use virtual display (experimental, may not work with Vulkan)
cs2pov -d match.dem -p "Player" -o out.mp4 --display 99
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `CS2_PATH` | Path to CS2 installation (alternative to `--cs2-path`) |

```bash
export CS2_PATH="/mnt/games/SteamLibrary/steamapps/common/Counter-Strike Global Offensive"
cs2pov -d match.dem -p "Player" -o out.mp4
```

## Known Issues

### Recording Uses Your Real Display

CS2 requires Vulkan, and virtual framebuffers (Xvfb) don't support Vulkan. The tool defaults to `--display 0` (your real display), which means:

- CS2 will take over your screen during recording
- You can't use your computer while recording
- No need to restart your window manager or display server

If you try `--display 99` (virtual display), you'll see: "The selected graphics queue does not support presenting a swapchain image"

### Close CS2 Before Recording

Steam prevents running multiple CS2 instances. Close any running CS2 before using cs2pov.

### Camera May Drift During Rounds

CS2 doesn't support tick-accurate command injection (VDM files). The camera is locked to the target player at demo start but may drift during round transitions.

## How It Works

1. **Parse demo** - Extract player list and metadata using demoparser2
2. **Generate config** - Create CS2 CFG file with spectator settings
3. **Copy demo** - Place demo in CS2's replays directory
4. **Start capture** - Launch FFmpeg to record the display
5. **Launch CS2** - Start CS2 with the generated config
6. **Wait** - CS2 plays the demo and exits automatically
7. **Finalize** - Stop capture and save video

## Requirements

- **Python 3.10+**
- **CS2** installed via Steam
- **FFmpeg** for video capture
- **GPU** with Vulkan support (NVIDIA or AMD)

## License

MIT
