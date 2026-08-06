"""Turns a Radarr/Sonarr lookup result into the embeds shown at each step of
the picker. Kept deliberately dumb/uniform — bot.py owns all the Discord
interaction plumbing, this module just knows how to render one candidate.
"""

from dataclasses import dataclass
from typing import Optional

import discord

from .radarr import RadarrMovie
from .sonarr import SonarrSeries

GOLD = 0xD4AF37


@dataclass
class Candidate:
    title: str
    year: int
    external_id: int  # tmdbId for movies, tvdbId for series
    overview: str
    poster_url: Optional[str]
    meta_line: str
    raw: dict


def from_movie(m: RadarrMovie) -> Candidate:
    meta = " • ".join(p for p in [m.studio, m.status.title(), m.original_language] if p)
    return Candidate(title=m.title, year=m.year, external_id=m.tmdb_id, overview=m.overview, poster_url=m.poster_url, meta_line=meta, raw=m.raw)


def from_series(s: SonarrSeries) -> Candidate:
    meta = " • ".join(p for p in [s.network, s.status.title(), s.original_language] if p)
    return Candidate(title=s.title, year=s.year, external_id=s.tvdb_id, overview=s.overview, poster_url=s.poster_url, meta_line=meta, raw=s.raw)


def _truncate(text: str, limit: int) -> str:
    text = text or "No overview available."
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def candidate_list_embed(candidate: Candidate, index: int, already_status: Optional[str]) -> discord.Embed:
    """Step 1 — one of up to 5 embeds shown together, each numbered so the
    button row below can reference them unambiguously.
    """
    title = f"{index}. {candidate.title} ({candidate.year})" if candidate.year else f"{index}. {candidate.title}"
    embed = discord.Embed(title=title, description=_truncate(candidate.overview, 200), color=GOLD)
    if candidate.meta_line:
        embed.add_field(name="Details", value=candidate.meta_line, inline=False)
    if already_status:
        embed.add_field(name=" ", value=f"✅ **Already in your library** — {already_status}", inline=False)
    if candidate.poster_url:
        embed.set_image(url=candidate.poster_url)
    return embed


def confirm_embed(candidate: Candidate, already_status: Optional[str]) -> discord.Embed:
    """Step 2 — the single, full-detail "are you sure this is the one"
    screen. Nothing gets added to Sonarr/Radarr until this is confirmed.
    """
    title = f"{candidate.title} ({candidate.year})" if candidate.year else candidate.title
    embed = discord.Embed(title=title, description=_truncate(candidate.overview, 500), color=GOLD)
    if candidate.meta_line:
        embed.add_field(name="Details", value=candidate.meta_line, inline=False)
    if candidate.poster_url:
        embed.set_image(url=candidate.poster_url)
    if already_status:
        embed.add_field(name="Status", value=f"✅ Already in your library — {already_status}", inline=False)
    else:
        embed.add_field(name="Status", value="Not in your library yet.", inline=False)
    return embed
