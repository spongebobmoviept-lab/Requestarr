import json
import os

from .config import settings

EDITABLE_KEYS = [
    "movie_quality_profile_id",
    "movie_root_folder",
    "tv_quality_profile_id",
    "tv_root_folder",
    "anime_quality_profile_id",
    "anime_root_folder",
    "max_candidates",
    "poll_interval_seconds",
    "max_job_duration_hours",
    "dry_run",
]

_overrides_path = os.path.join(settings.data_dir, "settings_overrides.json")


def load_overrides() -> None:
    if not os.path.exists(_overrides_path):
        return
    with open(_overrides_path, "r", encoding="utf-8") as f:
        overrides = json.load(f)
    for key, value in overrides.items():
        if key in EDITABLE_KEYS:
            setattr(settings, key, value)


def _persist_all() -> None:
    os.makedirs(settings.data_dir, exist_ok=True)
    payload = {key: getattr(settings, key) for key in EDITABLE_KEYS}
    tmp_path = _overrides_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp_path, _overrides_path)


def current_editable() -> dict:
    return {key: getattr(settings, key) for key in EDITABLE_KEYS}


def save_overrides(update: dict) -> dict:
    for key, value in update.items():
        if key in EDITABLE_KEYS:
            setattr(settings, key, value)
    _persist_all()
    return current_editable()
