#!/bin/bash
# Cleanup script - removes generated log and video files from project directory

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "Cleaning up files in: $PROJECT_DIR"

# Delete all .log files
find "$PROJECT_DIR" -maxdepth 1 -name "*.log" -type f -delete -print 2>/dev/null | while read f; do
    echo "  Deleted: $(basename "$f")"
done

# Delete all .mp4 files
find "$PROJECT_DIR" -maxdepth 1 -name "*.mp4" -type f -delete -print 2>/dev/null | while read f; do
    echo "  Deleted: $(basename "$f")"
done

echo "Cleanup complete"
