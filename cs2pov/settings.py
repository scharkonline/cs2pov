"""Config file support for cs2pov.

Loads cs2pov.json config files with defaults and batch job queues.
Priority: CLI explicit flag > job override > config defaults > hardcoded defaults.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .exceptions import CS2POVError


class ConfigError(CS2POVError):
    """Invalid config file."""
    pass


# Maps 1:1 to CLI flags. These are the final fallback values.
HARDCODED_DEFAULTS: dict[str, Any] = {
    "resolution": "1920x1080",
    "framerate": 60,
    "display": 0,
    "no_hud": False,
    "no_audio": False,
    "audio_device": None,
    "cs2_path": None,
    "tick_nav": False,
    "no_trim": False,
    "verbose": False,
}

# Keys valid in "defaults" and as job overrides (includes per-job fields that can be defaulted)
VALID_DEFAULT_KEYS = set(HARDCODED_DEFAULTS.keys()) | {"player", "demo", "output"}

# Keys required after merging job with defaults (not per-job — defaults can provide them)
REQUIRED_MERGED_KEYS = {"demo", "player", "output"}

# Keys valid in a job entry
VALID_JOB_KEYS = REQUIRED_MERGED_KEYS | VALID_DEFAULT_KEYS | {"type"}

# Valid job types
VALID_JOB_TYPES = {"pov", "record"}

CONFIG_FILENAME = "cs2pov.json"
CURRENT_VERSION = 1


@dataclass
class JobConfig:
    """A single recording job from config."""
    demo: Optional[str] = None
    player: Optional[str] = None
    output: Optional[str] = None
    type: str = "pov"
    overrides: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProjectConfig:
    """Parsed config file."""
    version: int
    defaults: dict[str, Any]
    jobs: list[JobConfig]
    config_path: Path


def find_config(override_path: Optional[Path] = None) -> Optional[Path]:
    """Find config file. Check override path, then cwd/cs2pov.json."""
    if override_path is not None:
        path = Path(override_path).resolve()
        if not path.exists():
            raise ConfigError(f"Config file not found: {path}")
        return path

    cwd_config = Path.cwd() / CONFIG_FILENAME
    if cwd_config.exists():
        return cwd_config

    return None


def load_config(path: Path) -> ProjectConfig:
    """Parse and validate a cs2pov.json config file."""
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in {path}: {e}")

    if not isinstance(raw, dict):
        raise ConfigError(f"Config must be a JSON object, got {type(raw).__name__}")

    # Version check
    version = raw.get("version")
    if version is None:
        raise ConfigError("Config missing 'version' field")
    if not isinstance(version, int) or version < 1:
        raise ConfigError(f"Invalid config version: {version}")
    if version > CURRENT_VERSION:
        raise ConfigError(
            f"Config version {version} is newer than supported ({CURRENT_VERSION}). "
            f"Please update cs2pov."
        )

    # Parse defaults
    defaults_raw = raw.get("defaults", {})
    if not isinstance(defaults_raw, dict):
        raise ConfigError("'defaults' must be an object")

    unknown_keys = set(defaults_raw.keys()) - VALID_DEFAULT_KEYS
    if unknown_keys:
        raise ConfigError(f"Unknown keys in 'defaults': {', '.join(sorted(unknown_keys))}")

    defaults = dict(defaults_raw)

    # Validate types in defaults
    _validate_setting_types(defaults, "defaults")

    # Parse jobs
    jobs_raw = raw.get("jobs", [])
    if not isinstance(jobs_raw, list):
        raise ConfigError("'jobs' must be an array")

    jobs: list[JobConfig] = []
    for i, job_raw in enumerate(jobs_raw):
        if not isinstance(job_raw, dict):
            raise ConfigError(f"Job {i+1} must be an object")

        unknown = set(job_raw.keys()) - VALID_JOB_KEYS
        if unknown:
            raise ConfigError(f"Job {i+1} has unknown keys: {', '.join(sorted(unknown))}")

        # Extract and validate job type
        job_type = job_raw.get("type", "pov")
        if not isinstance(job_type, str):
            raise ConfigError(f"Job {i+1}: 'type' must be a string")
        if job_type not in VALID_JOB_TYPES:
            raise ConfigError(
                f"Job {i+1}: invalid type '{job_type}', must be one of: {', '.join(sorted(VALID_JOB_TYPES))}"
            )

        overrides = {k: v for k, v in job_raw.items()
                     if k not in REQUIRED_MERGED_KEYS and k != "type"}
        _validate_setting_types(overrides, f"job {i+1}")

        jobs.append(JobConfig(
            demo=job_raw.get("demo"),
            player=job_raw.get("player"),
            output=job_raw.get("output"),
            type=job_type,
            overrides=overrides,
        ))

    return ProjectConfig(
        version=version,
        defaults=defaults,
        jobs=jobs,
        config_path=path.resolve(),
    )


def _validate_setting_types(settings: dict[str, Any], context: str) -> None:
    """Validate types of setting values."""
    type_checks: dict[str, tuple[type, ...]] = {
        "resolution": (str,),
        "framerate": (int,),
        "display": (int,),
        "no_hud": (bool,),
        "no_audio": (bool,),
        "audio_device": (str, type(None)),
        "cs2_path": (str, type(None)),
        "tick_nav": (bool,),
        "no_trim": (bool,),
        "verbose": (bool,),
        "player": (str,),
        "demo": (str,),
        "output": (str,),
    }

    for key, value in settings.items():
        expected = type_checks.get(key)
        if expected and not isinstance(value, expected):
            expected_names = " or ".join(t.__name__ for t in expected)
            raise ConfigError(
                f"In {context}: '{key}' must be {expected_names}, got {type(value).__name__}"
            )


def resolve_job(job: JobConfig, defaults: dict[str, Any]) -> dict[str, Any]:
    """Merge job overrides onto defaults. Returns flat dict with all keys."""
    merged = dict(defaults)
    merged.update(job.overrides)
    # Job fields override defaults (only set if job provides them)
    if job.demo is not None:
        merged["demo"] = job.demo
    if job.player is not None:
        merged["player"] = job.player
    if job.output is not None:
        merged["output"] = job.output
    return merged


def resolve_paths(job_dict: dict[str, Any], config_dir: Path) -> dict[str, Any]:
    """Make relative paths in job_dict absolute against config_dir."""
    result = dict(job_dict)

    for key in ("demo", "output"):
        if key in result and result[key] is not None:
            p = Path(result[key])
            if not p.is_absolute():
                result[key] = str((config_dir / p).resolve())

    if "cs2_path" in result and result["cs2_path"] is not None:
        p = Path(result["cs2_path"])
        if not p.is_absolute():
            result["cs2_path"] = str((config_dir / p).resolve())

    return result


def merge_args_with_config(args, job_dict: dict[str, Any]):
    """Layer CLI > config > hardcoded onto args namespace.

    For each config-overridable key:
    - If CLI explicitly set it (not None), keep CLI value
    - Else if config provides it, use config value
    - Else use hardcoded default
    """
    # Keys that should be Path objects when set
    path_keys = {"cs2_path"}

    for key, hardcoded in HARDCODED_DEFAULTS.items():
        cli_val = getattr(args, key, None)
        config_val = job_dict.get(key)

        if cli_val is not None:
            # CLI wins - already set
            continue
        elif config_val is not None:
            if key in path_keys and isinstance(config_val, str):
                setattr(args, key, Path(config_val))
            else:
                setattr(args, key, config_val)
        else:
            setattr(args, key, hardcoded)

    # Set demo/player/output from job if not on CLI
    for key in ("demo", "player", "output"):
        cli_val = getattr(args, key, None)
        job_val = job_dict.get(key)
        if cli_val is None and job_val is not None:
            # Convert path strings to Path objects for demo/output
            if key in ("demo", "output"):
                setattr(args, key, Path(job_val))
            else:
                setattr(args, key, job_val)

    return args


def generate_default_config(cs2_path: Optional[str] = None) -> dict:
    """Generate a template cs2pov.json config."""
    config: dict[str, Any] = {
        "version": CURRENT_VERSION,
        "defaults": {
            "resolution": "1920x1080",
            "framerate": 60,
            "tick_nav": False,
            "verbose": False,
        },
        "jobs": [
            {
                "demo": "./demos/example.dem",
                "player": "PlayerName",
                "output": "./recordings/example.mp4",
            }
        ],
    }

    if cs2_path:
        config["defaults"]["cs2_path"] = cs2_path

    return config
