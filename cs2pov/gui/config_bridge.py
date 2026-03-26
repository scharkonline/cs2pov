"""Convert GUI job dicts to cs2pov.json config format."""

from pathlib import Path


def gui_jobs_to_config(jobs: list[dict]) -> dict:
    """Convert a list of GUI job dicts (from JobCard.to_job_dict()) to cs2pov.json schema.

    Returns a config dict with version, empty defaults, and fully-specified jobs.
    """
    config_jobs = []

    for job in jobs:
        job_type = job.get("type", "pov")
        entry: dict = {"type": job_type}

        # Demo path — all job types use this
        if job.get("demo_path"):
            entry["demo"] = str(Path(job["demo_path"]).resolve())

        # Player — stored as PlayerInfo object, emit steamid string
        player = job.get("player")
        if player is not None:
            entry["player"] = str(player.steamid)

        # Output path
        if job.get("output_path"):
            entry["output"] = str(Path(job["output_path"]).resolve())

        if job_type in ("pov", "record"):
            # Resolution: (1920, 1080) -> "1920x1080"
            res = job.get("resolution", (1920, 1080))
            entry["resolution"] = f"{res[0]}x{res[1]}"

            entry["framerate"] = job.get("framerate", 60)
            entry["display"] = job.get("display_num", 0)
            entry["no_hud"] = job.get("hide_hud", True)
            entry["no_audio"] = not job.get("enable_audio", True)
            entry["tick_nav"] = job.get("tick_nav", False)

            # For pov type, respect the trim checkbox; record never trims
            if job_type == "pov":
                entry["no_trim"] = not job.get("do_trim", True)
            else:
                entry["no_trim"] = True

        elif job_type == "trim":
            if job.get("video_path"):
                entry["video"] = str(Path(job["video_path"]).resolve())
            entry["tick_nav"] = job.get("tick_nav", False)
            startup = job.get("startup_time")
            if startup is not None:
                entry["startup_time"] = startup

        elif job_type == "comms":
            if job.get("video_path"):
                entry["video"] = str(Path(job["video_path"]).resolve())
            if job.get("comms_audio_path"):
                entry["audio"] = str(Path(job["comms_audio_path"]).resolve())
            entry["comms_r1_sync_time"] = job.get("r1_sync_time", 0.0)
            entry["game_volume"] = job.get("game_volume", 1.0)
            entry["comms_volume"] = job.get("comms_volume", 1.0)

        config_jobs.append(entry)

    return {
        "version": 1,
        "defaults": {},
        "jobs": config_jobs,
    }
