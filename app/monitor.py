"""Background polling loop — one tick per request, posting status updates
into that request's Discord thread. Same shape as Reclaimarr's
_monitor_download: poll, dedupe the queue defensively, notify on change,
give up after a bounded stall window instead of polling forever.
"""

import asyncio
import datetime
from typing import Optional

import discord

from . import job_store, radarr, sonarr
from .config import settings
from .logger import log

_client: Optional[discord.Client] = None


def set_client(client: discord.Client) -> None:
    global _client
    _client = client


async def _post(thread_id: int, content: str) -> None:
    if _client is None:
        return
    try:
        thread = _client.get_channel(thread_id) or await _client.fetch_channel(thread_id)
        await thread.send(content)
    except Exception as exc:  # noqa: BLE001 — a failed notification should never break polling
        await log(f"monitor: failed to post to thread {thread_id}: {exc}")


def _is_stalled(job: job_store.Job) -> bool:
    created = datetime.datetime.fromisoformat(job.created_at)
    age = datetime.datetime.now(datetime.timezone.utc) - created
    return age.total_seconds() >= settings.max_job_duration_hours * 3600


async def _dedupe_movie_queue(movie_id: int) -> list[dict]:
    """Same reasoning as Reclaimarr/Nyaarr — Radarr's own search running
    alongside this bot's triggered search is a real, confirmed way to end up
    with two simultaneous downloads for the same movie.
    """
    records = await radarr.queue_records_for_movie(movie_id)
    if len(records) <= 1:
        return records

    def sort_key(r: dict) -> tuple:
        return ("remux" in r.get("title", "").lower(), -(r.get("seeders") or 0))

    records.sort(key=sort_key)
    keep, *extras = records
    for extra in extras:
        await radarr.remove_queue_item(extra["id"])
    await log(f"monitor: movie {movie_id} had {len(records)} simultaneous downloads — kept '{keep.get('title')}', removed {len(extras)}")
    return [keep]


async def _dedupe_series_queue(series_id: int) -> list[dict]:
    records = await sonarr.queue_records_for_series(series_id)
    by_episode: dict[int, list[dict]] = {}
    for r in records:
        ep_id = (r.get("episode") or {}).get("id")
        if ep_id is not None:
            by_episode.setdefault(ep_id, []).append(r)

    kept: list[dict] = []
    for ep_id, recs in by_episode.items():
        if len(recs) <= 1:
            kept.append(recs[0])
            continue
        recs.sort(key=lambda r: ("remux" in r.get("title", "").lower(), -(r.get("seeders") or 0)))
        keep, *extras = recs
        for extra in extras:
            await sonarr.remove_queue_item(extra["id"])
        await log(f"monitor: series {series_id} episode {ep_id} had {len(recs)} simultaneous downloads — kept '{keep.get('title')}', removed {len(extras)}")
        kept.append(keep)
    return kept


async def _poll_movie(job: job_store.Job) -> None:
    movie = await radarr.get_movie(job.external_id)
    if movie.has_file:
        await _post(job.thread_id, f"🎬 **{movie.title}** is now available!")
        await job_store.store.record_history(job.kind, job.external_id, movie.title, "done", "Downloaded and available.")
        await job_store.store.release(job.kind, job.external_id)
        return

    if _is_stalled(job):
        await _post(
            job.thread_id,
            f"⚠️ Still couldn't find a release for **{movie.title}** after {settings.max_job_duration_hours:.0f}h — "
            f"giving up for now. It stays monitored in Radarr in case something shows up later.",
        )
        await job_store.store.record_history(job.kind, job.external_id, movie.title, "failed", "No release found within the stall window.")
        await job_store.store.release(job.kind, job.external_id)
        return

    records = await _dedupe_movie_queue(job.external_id)
    if records and job.status != "downloading":
        record = records[0]
        size_gb = round((record.get("size") or 0) / (1024**3), 1)
        await _post(job.thread_id, f"⬇️ Grabbed **{record.get('title')}** ({size_gb} GB) — downloading now.")
        await job_store.store.update(job.kind, job.external_id, status="downloading")


async def _poll_series(job: job_store.Job) -> None:
    episodes = await sonarr.list_episodes(job.external_id)
    monitored = [e for e in episodes if e.monitored]

    if monitored and all(e.has_file for e in monitored):
        await _post(job.thread_id, f"✅ **{job.title}** — all caught up, {len(monitored)} episode(s) available!")
        await job_store.store.record_history(job.kind, job.external_id, job.title, "done", f"{len(monitored)} episode(s) available.")
        await job_store.store.release(job.kind, job.external_id)
        return

    if _is_stalled(job):
        if job.kind == "anime":
            note = "It's likely sitting in Nyaarr's Needs Review queue — open the Nyaarr dashboard to pick a release, or turn on auto-grab there."
        else:
            note = "It stays monitored in Sonarr in case something shows up later."
        await _post(job.thread_id, f"⚠️ Still waiting on releases for **{job.title}** after {settings.max_job_duration_hours:.0f}h. {note}")
        await job_store.store.record_history(job.kind, job.external_id, job.title, "failed", "No/incomplete releases within the stall window.")
        await job_store.store.release(job.kind, job.external_id)
        return

    records = await _dedupe_series_queue(job.external_id)
    seen = set(job.context.get("seen_queue_ids", []))
    new_records = [r for r in records if r.get("id") not in seen]
    for r in new_records:
        ep = r.get("episode") or {}
        label = f"S{ep.get('seasonNumber', 0):02d}E{ep.get('episodeNumber', 0):02d}"
        await _post(job.thread_id, f"⬇️ Grabbed {label}: {r.get('title')}")
        seen.add(r.get("id"))
    if new_records:
        await job_store.store.update(job.kind, job.external_id, status="downloading", context={**job.context, "seen_queue_ids": list(seen)})


async def poll_loop() -> None:
    while True:
        await asyncio.sleep(settings.poll_interval_seconds)
        for key, job in list(job_store.store.active_jobs.items()):
            try:
                if job.kind == "movie":
                    await _poll_movie(job)
                else:
                    await _poll_series(job)
            except Exception as exc:  # noqa: BLE001
                await log(f"monitor: poll failed for {key}: {exc} — will retry next cycle")
