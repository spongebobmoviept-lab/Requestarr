from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from .config import settings
from .logger import log, with_retry


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=f"{settings.radarr_url}/api/v3",
        timeout=30,
        headers={"X-Api-Key": settings.radarr_api_key},
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
    populated from the same round-trip.
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
class RadarrMovie:
    id: int
    tmdb_id: int
    title: str
    year: int
    overview: str
    poster_url: Optional[str]
    studio: str
    status: str
    original_language: str
    has_file: bool
    monitored: bool
    raw: dict = field(default_factory=dict)


def _to_movie(raw: dict) -> RadarrMovie:
    return RadarrMovie(
        id=raw.get("id", 0),
        tmdb_id=raw.get("tmdbId", 0),
        title=raw.get("title", ""),
        year=raw.get("year", 0),
        overview=raw.get("overview", ""),
        poster_url=_poster_from_raw(raw),
        studio=raw.get("studio", "") or "",
        status=raw.get("status", "") or "",
        original_language=(raw.get("originalLanguage") or {}).get("name", ""),
        has_file=bool(raw.get("hasFile")),
        monitored=bool(raw.get("monitored")),
        raw=raw,
    )


@with_retry(label="Radarr: lookup by term")
async def lookup_by_term(term: str) -> list[RadarrMovie]:
    """Radarr's own search — TMDB-backed, no separate TMDB key needed.
    Used for the /movie slash command's candidate list.
    """
    async with _client() as client:
        resp = await client.get("/movie/lookup", params={"term": term})
        resp.raise_for_status()
        data = resp.json()
    return [_to_movie(m) for m in data]


@with_retry(label="Radarr: find by tmdb id")
async def find_by_tmdb_id(tmdb_id: int) -> Optional[RadarrMovie]:
    """Duplicate guard — checked before ever adding, so a request for
    something already in the library reports its status instead of adding
    a second copy.
    """
    async with _client() as client:
        resp = await client.get("/movie", params={"tmdbId": tmdb_id})
        resp.raise_for_status()
        data = resp.json()
    return _to_movie(data[0]) if data else None


@with_retry(label="Radarr: get movie")
async def get_movie(movie_id: int) -> RadarrMovie:
    async with _client() as client:
        resp = await client.get(f"/movie/{movie_id}")
        resp.raise_for_status()
        return _to_movie(resp.json())


@with_retry(label="Radarr: add movie")
async def add_movie(lookup_raw: dict, quality_profile_id: int, root_folder_path: str, search: bool = True) -> RadarrMovie:
    if settings.dry_run:
        await log(f"[DRY RUN] would add movie '{lookup_raw.get('title')}' (tmdbId={lookup_raw.get('tmdbId')}) to Radarr, search={search}")
        return _to_movie({**lookup_raw, "id": -1})

    payload = dict(lookup_raw)
    payload["qualityProfileId"] = quality_profile_id
    payload["rootFolderPath"] = root_folder_path
    payload["monitored"] = True
    payload["addOptions"] = {"searchForMovie": search}

    async with _client() as client:
        resp = await client.post("/movie", json=payload)
        resp.raise_for_status()
        return _to_movie(resp.json())


@with_retry(label="Radarr: get queue")
async def get_queue() -> list[dict[str, Any]]:
    async with _client() as client:
        resp = await client.get("/queue", params={"pageSize": 200})
        resp.raise_for_status()
        return resp.json().get("records", [])


async def queue_records_for_movie(movie_id: int) -> list[dict[str, Any]]:
    records = await get_queue()
    return [r for r in records if r.get("movieId") == movie_id]


@with_retry(label="Radarr: remove queue item")
async def remove_queue_item(queue_id: int, remove_from_client: bool = True, blocklist: bool = False) -> None:
    if settings.dry_run:
        await log(f"[DRY RUN] would remove Radarr queue item {queue_id}")
        return
    async with _client() as client:
        resp = await client.delete(
            f"/queue/{queue_id}",
            params={"removeFromClient": str(remove_from_client).lower(), "blocklist": str(blocklist).lower()},
        )
        resp.raise_for_status()
