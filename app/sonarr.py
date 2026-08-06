from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from .config import settings
from .logger import log, with_retry


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=f"{settings.sonarr_url}/api/v3",
        timeout=30,
        headers={"X-Api-Key": settings.sonarr_api_key},
    )


def _poster_from_raw(raw: dict) -> Optional[str]:
    for image in raw.get("images", []):
        if image.get("coverType") == "poster":
            return image.get("remoteUrl") or image.get("url")
    return None


async def test_connection(url: str, api_key: str) -> dict:
    """Ad-hoc connectivity check for the setup wizard, using credentials the
    caller just typed in rather than whatever's already saved. Returns both
    quality profiles and root folders so the wizard's pickers can be
    populated from the same round-trip — needed twice here (TV and anime
    both go through Sonarr, with independently chosen profile/root pairs).
    """
    async with httpx.AsyncClient(base_url=f"{url.rstrip('/')}/api/v3", timeout=10, headers={"X-Api-Key": api_key}) as client:
        status_resp = await client.get("/system/status")
        status_resp.raise_for_status()
        version = status_resp.json().get("version")

        profiles_resp = await client.get("/qualityprofile")
        profiles_resp.raise_for_status()
        profiles = [{"id": p["id"], "name": p["name"]} for p in profiles_resp.json()]

        roots_resp = await client.get("/rootfolder")
        roots_resp.raise_for_status()
        roots = [{"id": r["id"], "path": r["path"]} for r in roots_resp.json()]

    return {"version": version, "quality_profiles": profiles, "root_folders": roots}


@dataclass
class SonarrSeries:
    id: int
    tvdb_id: int
    title: str
    year: int
    overview: str
    poster_url: Optional[str]
    network: str
    status: str
    original_language: str
    genres: list[str]
    monitored: bool
    raw: dict = field(default_factory=dict)

    @property
    def looks_like_anime(self) -> bool:
        """Sonarr/TVDB genre tagging for anime is inconsistent — this is only
        ever used to SORT candidates (anime-looking ones first for the
        /anime command) so the requester can visually confirm, never to
        silently decide anything on its own.
        """
        return "anime" in [g.lower() for g in self.genres] or self.original_language.lower() == "japanese"


@dataclass
class SonarrEpisode:
    id: int
    series_id: int
    episode_number: int
    season_number: int
    monitored: bool
    has_file: bool


def _to_series(raw: dict) -> SonarrSeries:
    return SonarrSeries(
        id=raw.get("id", 0),
        tvdb_id=raw.get("tvdbId", 0),
        title=raw.get("title", ""),
        year=raw.get("year", 0),
        overview=raw.get("overview", ""),
        poster_url=_poster_from_raw(raw),
        network=raw.get("network", "") or "",
        status=raw.get("status", "") or "",
        original_language=(raw.get("originalLanguage") or {}).get("name", ""),
        genres=raw.get("genres", []) or [],
        monitored=bool(raw.get("monitored")),
        raw=raw,
    )


@with_retry(label="Sonarr: lookup by term")
async def lookup_by_term(term: str) -> list[SonarrSeries]:
    """Sonarr's own search — TVDB-backed, no separate API key needed. Used
    for both the /tv and /anime slash commands' candidate lists.
    """
    async with _client() as client:
        resp = await client.get("/series/lookup", params={"term": term})
        resp.raise_for_status()
        data = resp.json()
    return [_to_series(s) for s in data]


@with_retry(label="Sonarr: find by tvdb id")
async def find_by_tvdb_id(tvdb_id: int) -> Optional[SonarrSeries]:
    async with _client() as client:
        resp = await client.get("/series", params={"tvdbId": tvdb_id})
        resp.raise_for_status()
        data = resp.json()
    return _to_series(data[0]) if data else None


@with_retry(label="Sonarr: get series")
async def get_series(series_id: int) -> SonarrSeries:
    async with _client() as client:
        resp = await client.get(f"/series/{series_id}")
        resp.raise_for_status()
        return _to_series(resp.json())


@with_retry(label="Sonarr: add series")
async def add_series(lookup_raw: dict, quality_profile_id: int, root_folder_path: str, series_type: str, search: bool) -> SonarrSeries:
    """series_type is "standard" for /tv or "anime" for /anime. search is
    deliberately False for anime — Sonarr must never blind-grab an anime
    release itself; Nyaarr's background scan takes over from here instead.
    """
    if settings.dry_run:
        await log(f"[DRY RUN] would add series '{lookup_raw.get('title')}' (tvdbId={lookup_raw.get('tvdbId')}) to Sonarr as {series_type}, search={search}")
        return _to_series({**lookup_raw, "id": -1})

    payload = dict(lookup_raw)
    payload["qualityProfileId"] = quality_profile_id
    payload["rootFolderPath"] = root_folder_path
    payload["seriesType"] = series_type
    payload["monitored"] = True
    payload["addOptions"] = {"searchForMissingEpisodes": search, "monitor": "all"}

    async with _client() as client:
        resp = await client.post("/series", json=payload)
        resp.raise_for_status()
        return _to_series(resp.json())


@with_retry(label="Sonarr: list episodes for series")
async def list_episodes(series_id: int) -> list[SonarrEpisode]:
    async with _client() as client:
        resp = await client.get("/episode", params={"seriesId": series_id})
        resp.raise_for_status()
        data = resp.json()
    return [
        SonarrEpisode(
            id=e["id"],
            series_id=e["seriesId"],
            episode_number=e.get("episodeNumber", 0),
            season_number=e.get("seasonNumber", 0),
            monitored=e.get("monitored", False),
            has_file=e.get("hasFile", False),
        )
        for e in data
    ]


@with_retry(label="Sonarr: get queue")
async def get_queue() -> list[dict[str, Any]]:
    async with _client() as client:
        resp = await client.get("/queue", params={"pageSize": 200, "includeEpisode": True})
        resp.raise_for_status()
        return resp.json().get("records", [])


async def queue_records_for_series(series_id: int) -> list[dict[str, Any]]:
    records = await get_queue()
    return [r for r in records if r.get("seriesId") == series_id]


@with_retry(label="Sonarr: remove queue item")
async def remove_queue_item(queue_id: int, remove_from_client: bool = True, blocklist: bool = False) -> None:
    if settings.dry_run:
        await log(f"[DRY RUN] would remove Sonarr queue item {queue_id}")
        return
    async with _client() as client:
        resp = await client.delete(
            f"/queue/{queue_id}",
            params={"removeFromClient": str(remove_from_client).lower(), "blocklist": str(blocklist).lower()},
        )
        resp.raise_for_status()
