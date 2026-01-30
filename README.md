# cs2pov

Counter-Strike 2 POV recording automation. Largely AI-assisted and personalized to my system, maybe I'll make this more ubiquitous in the future.

## Installation

### Requirements
- **Python 3.10+**
- **CS2** installed via Steam, with a Steam login
- **FFmpeg** for video/audio capture
- **xdotool** for window automation
- **PulseAudio** for audio capture (pactl)
- **GPU** with Vulkan support (NVIDIA or AMD)

```bash
## Install Python dependencies:
pip install -e .

## Gentoo (based)
emerge -av media-video/ffmpeg x11-misc/xdotool media-sound/pulseaudio

## Ubuntu/Debian (ig)
apt install ffmpeg xdotool pulseaudio-utils
```

## Quick Start

```bash
# Show demo information (players, deaths, spawns)
cs2pov info /path/to/demo.dem

# Record a player's POV (full pipeline: record + trim)
cs2pov pov -d /path/to/demo.dem -p "PlayerName" -o recording.mp4

# Raw recording only (no trimming)
cs2pov record -d /path/to/demo.dem -p "PlayerName" -o raw.mp4

# Trim an existing recording
cs2pov trim raw.mp4 -d /path/to/demo.dem -p "PlayerName"
```

## Commands

### `cs2pov info` - Show Demo Information

Display demo metadata, player list, and death/spawn statistics for each player.

```bash
cs2pov info demo.dem              # Human-readable output
cs2pov info demo.dem --json       # JSON output
cs2pov info demo.dem -v           # Verbose (includes death period details)
```

### `cs2pov pov` - Full Recording Pipeline

Record a player's POV and automatically trim death periods.

```bash
cs2pov pov -d demo.dem -p "PlayerName" -o recording.mp4
cs2pov pov -d demo.dem -p "PlayerName" -o recording.mp4 --no-trim  # Skip trimming
```

### `cs2pov record` - Raw Recording Only

Record without post-processing. Useful for batch recording then trimming later.

```bash
cs2pov record -d demo.dem -p "PlayerName" -o raw.mp4
```

### `cs2pov trim` - Trim Existing Recording

Remove death periods from a previously recorded video.

```bash
cs2pov trim raw.mp4 -d demo.dem -p "PlayerName"
cs2pov trim raw.mp4 -d demo.dem -p "PlayerName" -o trimmed.mp4
```

## Common Options

### Recording Options (pov, record)

| Argument | Short | Default | Description |
|----------|-------|---------|-------------|
| `--demo` | `-d` | required | Path to demo file (.dem) |
| `--player` | `-p` | required | Player to record (name or SteamID) |
| `--output` | `-o` | required | Output video file path |
| `--resolution` | `-r` | 1920x1080 | Recording resolution |
| `--framerate` | `-f` | 60 | Recording framerate |
| `--no-hud` | | off | Hide HUD elements |
| `--no-audio` | | off | Disable audio recording |
| `--audio-device` | | auto | PulseAudio device for audio capture |
| `--display` | | 0 | X display number |
| `--cs2-path` | | auto | Path to CS2 installation |
| `--verbose` | `-v` | off | Verbose output |

### Player Identification

The `--player` argument accepts multiple formats:

- **Player name**: `"PlayerName"` (case-insensitive, partial match supported)
- **SteamID64**: `76561198012345678`
- **SteamID**: `STEAM_0:1:12345678`
- **SteamID3**: `[U:1:12345678]`

## How It Works

1. **Parse demo** - Extract player list and metadata using demoparser2
2. **Preprocess timeline** - Extract death/spawn events for accurate trimming
3. **Generate config** - Create CS2 CFG file with spectator settings
4. **Copy demo** - Place demo in CS2's replays directory
5. **Launch CS2** - Start CS2 via Steam with the generated config
6. **Wait for first spawn** - Monitor console.log for player spawn
7. **Hide demo UI** - Send Shift+F2 to hide playback controls
8. **Start capture** - Launch FFmpeg to record display + audio (PulseAudio)
9. **Recording loop** - Send F5 periodically to keep spectator locked on target player
10. **Wait for demo end** - Monitor console.log for demo completion
11. **Finalize** - Stop capture and terminate CS2
12. **Post-process** - Trim start and death periods from video using timeline data

## Noteworthy Issues/Workarounds

### Recording Uses Your Real Display

Due to some framebuffer stuff that's above my head, the tool defaults to `--display 0` (your real display) which means CS will take up your main display while recording. I mostly batch these at night so it's not a huge deal to me, but I'm hoping to experiment with headless recording in the future.

### Close CS2 Before Recording

You can't run multiple instances of CS through one account, so make sure it's closed ahead of time.

### Audio Capture

I'm sorry for making you use Pulseaudio. The audio is captured from your default PulseAudio output so this captures all system audio, not just CS2.
