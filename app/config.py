import os


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _env_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes")


class Settings:
    def __init__(self) -> None:
        self.sonarr_url = os.environ.get("SONARR_URL", "")
        self.sonarr_api_key = os.environ.get("SONARR_API_KEY", "")

        self.radarr_url = os.environ.get("RADARR_URL", "")
        self.radarr_api_key = os.environ.get("RADARR_API_KEY", "")

        # No web UI/wizard for this app at all ("just a bot who listens") —
        # everything is env-configured. A blank token just means the bot
        # never logs in; main.py logs that clearly instead of crashing, so
        # /health still comes up for Uptime Kuma while you finish setup.
        self.discord_bot_token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
        # Optional — if set, slash commands sync to this one guild instantly
        # (seconds). Without it they still work, just via Discord's global
        # sync path, which can take up to an hour to propagate on first use.
        self.discord_guild_id = os.environ.get("DISCORD_GUILD_ID", "").strip()

        # Movies: reuses Reclaimarr's own "standard" (non-4K) profile —
        # Reclaimarr's already-running upgrade-on-play workflow bumps a
        # freshly-requested movie to 4K automatically the first time someone
        # actually watches it, so this bot doesn't need any 4K logic at all.
        self.movie_quality_profile_id = _env_int("MOVIE_QUALITY_PROFILE_ID", 0)
        self.movie_root_folder = os.environ.get("MOVIE_ROOT_FOLDER", "")

        self.tv_quality_profile_id = _env_int("TV_QUALITY_PROFILE_ID", 0)
        self.tv_root_folder = os.environ.get("TV_ROOT_FOLDER", "")

        # Anime gets added with seriesType=anime and searchForMissingEpisodes
        # deliberately left off — Sonarr must never blind-grab an anime
        # release itself (that's exactly the wrong-dub/duplicate mess Nyaarr
        # was built to fix). Nyaarr's own background scan already covers
        # every anime-tagged series automatically and takes over from here.
        self.anime_quality_profile_id = _env_int("ANIME_QUALITY_PROFILE_ID", 0)
        self.anime_root_folder = os.environ.get("ANIME_ROOT_FOLDER", "")

        self.max_candidates = _env_int("MAX_CANDIDATES", 5)
        self.poll_interval_seconds = _env_int("POLL_INTERVAL_SECONDS", 60)
        # A request that's gone nowhere after this long gets one final
        # "couldn't find anything" post instead of polling forever.
        self.max_job_duration_hours = _env_float("MAX_JOB_DURATION_HOURS", 6)

        self.dry_run = _env_bool("DRY_RUN", True)

        self.app_port = _env_int("APP_PORT", 8787)

        self.data_dir = os.environ.get("DATA_DIR", "/data")
        self.jobs_file = os.path.join(self.data_dir, "jobs.json")
        self.log_file = os.path.join(self.data_dir, "log.txt")


settings = Settings()
