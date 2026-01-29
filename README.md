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
##Install Python dependencies:
pip install -e .

## Gentoo (based)
emerge -av media-video/ffmpeg x11-misc/xdotool media-sound/pulseaudio

## Ubuntu/Debian (ig)
apt install ffmpeg xdotool pulseaudio-utils
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
| `--no-audio` | | off | Disable audio recording |
| `--audio-device` | | auto | PulseAudio device for audio capture |
| `--no-trim` | | off | Skip post-processing, keep full recording |
| `--display` | | 0 | X display number (0 = real display) |
| `--cs2-path` | | auto | Path to CS2 installation (can also be CS2_PATH envvar) |
| `--verbose` | `-v` | off | Verbose output |
| `--version` | | | Show version |

### Player Identification

The `--player` argument accepts multiple formats:

- **Player name**: `"PlayerName"` (case-insensitive, partial match supported)
- **SteamID64**: `76561198012345678`
- **SteamID**: `STEAM_0:1:12345678`
- **SteamID3**: `[U:1:12345678]`

## How It Works

1. **Parse demo** - Extract player list and metadata using demoparser2
2. **Generate config** - Create CS2 CFG file with spectator settings
3. **Copy demo** - Place demo in CS2's replays directory
4. **Launch CS2** - Start CS2 via Steam with the generated config
5. **Wait for map load** - Monitor console.log for map load completion
6. **Hide demo UI** - Send Shift+F2 to hide playback controls
7. **Start capture** - Launch FFmpeg to record display + audio (PulseAudio)
8. **Recording loop** - Send F5 periodically to keep spectator locked on target player
9. **Wait for demo end** - Monitor console.log for demo completion
10. **Finalize** - Stop capture and terminate CS2
11. **Post-process** - Trim start (until POV selected) and death periods from video

## Noteworthy Issues/Workarounds

### Recording Uses Your Real Display

Due to some framebuffer stuff that's above my head, the tool defaults to `--display 0` (your real display) which means CS will take up your main display while recording. I mostly batch these at night so it's not a huge deal to me, but I'm hoping to experiment with headless recording in the future.

### Close CS2 Before Recording

You can't run multiple instances of CS through one account, so make sure it's closed ahead of time.

### Audio Capture

I'm sorry for making you use Pulseaudio. The audio is captured from your default PulseAudio output so this captures all system audio, not just CS2.
