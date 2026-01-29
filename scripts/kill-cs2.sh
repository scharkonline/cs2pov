#!/bin/bash
# Kill all running CS2 processes AND clean up Steam lock files
# Usage: kill-cs2.sh [-f|--force]

FORCE=0
if [[ "$1" == "-f" || "$1" == "--force" ]]; then
    FORCE=1
fi

echo "=== Finding CS2 processes ==="

# Get CS2 PIDs
PIDS=$(pgrep -f "cs2" 2>/dev/null)

if [ -z "$PIDS" ]; then
    echo "No CS2 processes found"
else
    for pid in $PIDS; do
        echo ""
        echo "Process $pid:"
        ps -p "$pid" -o pid,ppid,state,cmd --no-headers 2>/dev/null

        # Check process state
        STATE=$(ps -p "$pid" -o state= 2>/dev/null)
        PPID=$(ps -p "$pid" -o ppid= 2>/dev/null | tr -d ' ')

        if [ "$STATE" = "Z" ]; then
            echo "  -> ZOMBIE process, killing parent (PID $PPID)"
            sudo kill -9 "$PPID" 2>/dev/null
        else
            echo "  -> Sending SIGKILL"
            sudo kill -9 "$pid" 2>/dev/null

            # Check if it survived
            sleep 0.5
            if kill -0 "$pid" 2>/dev/null; then
                echo "  -> Still alive! Trying parent (PID $PPID)"
                sudo kill -9 "$PPID" 2>/dev/null
            else
                echo "  -> Killed"
            fi
        fi
    done
fi

echo ""
echo "=== Killing Steam reaper/overlay ==="
sudo pkill -9 -f "reaper.*730" 2>/dev/null
sudo pkill -9 -f "gameoverlayui.*730" 2>/dev/null

echo ""
echo "=== Cleaning lock files ==="

# Find CS2 installation
CS2_PATHS=(
    "${HOME}/.steam/steam/steamapps/common/Counter-Strike Global Offensive"
    "${HOME}/.local/share/Steam/steamapps/common/Counter-Strike Global Offensive"
    "/mnt/games/SteamLibrary/steamapps/common/Counter-Strike Global Offensive"
)

for cs2path in "${CS2_PATHS[@]}"; do
    if [ -d "$cs2path" ]; then
        echo "Checking: $cs2path"
        find "$cs2path" -name "*.lock" -o -name ".lock" 2>/dev/null | while read lock; do
            echo "  Removing: $lock"
            rm -f "$lock"
        done
    fi
done

# Temp lock files
rm -f /tmp/steam_730.lock /tmp/.steam_730_lock 2>/dev/null

echo ""
echo "=== Cleaning shared memory ==="
rm -f /dev/shm/*steam* /dev/shm/*valve* /dev/shm/*source* 2>/dev/null
echo "Cleared /dev/shm"

# System V shared memory
if [ "$FORCE" -eq 1 ]; then
    SHMIDS=$(ipcs -m 2>/dev/null | grep "$(whoami)" | awk '{print $2}')
    for shmid in $SHMIDS; do
        ipcrm -m "$shmid" 2>/dev/null && echo "Removed shm segment $shmid"
    done
fi

echo ""
echo "=== Final check ==="
REMAINING=$(pgrep -f "cs2" 2>/dev/null)
if [ -n "$REMAINING" ]; then
    echo "WARNING: CS2 processes still running:"
    ps -p $(echo $REMAINING | tr ' ' ',') -o pid,ppid,state,cmd --no-headers
    echo ""
    echo "These may be unkillable zombies. Try logging out and back in,"
    echo "or reboot if necessary."
    exit 1
else
    echo "All CS2 processes killed"
fi
