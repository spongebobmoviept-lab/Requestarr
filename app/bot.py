"""Discord-side of the app: three slash commands, each running the same
two-step "narrow it down, then confirm" flow described in the plan. No
account linking, no web UI — this is the whole interface.
"""

from typing import Optional

import discord
from discord import app_commands

from . import job_store, matcher, radarr, sonarr
from .config import settings
from .logger import log

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


def _profile_and_root(kind: str) -> tuple[int, str]:
    if kind == "movie":
        return settings.movie_quality_profile_id, settings.movie_root_folder
    if kind == "anime":
        return settings.anime_quality_profile_id, settings.anime_root_folder
    return settings.tv_quality_profile_id, settings.tv_root_folder


REQUESTS_CHANNEL_NAME = "requests"


def _target_channel(fallback: discord.abc.Messageable, guild: Optional[discord.Guild]) -> discord.abc.Messageable:
    """Requests post in #requests regardless of where the command was typed
    — falls back to wherever the command was actually run if that channel
    doesn't exist yet (e.g. before Servarr's /setup-server has been applied).
    """
    if guild is None:
        return fallback
    channel = discord.utils.get(guild.text_channels, name=REQUESTS_CHANNEL_NAME)
    return channel or fallback


class ConfirmView(discord.ui.View):
    """Step 2 — the single, full-detail screen. Nothing is added to
    Sonarr/Radarr until Confirm is pressed; if the pick is already in the
    library, there's no Confirm button at all, only Close.
    """

    def __init__(self, kind: str, candidate: matcher.Candidate, already_status: Optional[str], requester_id: int, private: bool = False):
        super().__init__(timeout=180)
        self.kind = kind
        self.candidate = candidate
        self.already_status = already_status
        self.requester_id = requester_id
        self.private = private

        if not already_status:
            confirm = discord.ui.Button(label="Confirm", emoji="✅", style=discord.ButtonStyle.success)
            confirm.callback = self._confirm
            self.add_item(confirm)
        close = discord.ui.Button(label="Cancel" if not already_status else "Close", emoji="❌" if not already_status else None, style=discord.ButtonStyle.secondary)
        close.callback = self._cancel
        self.add_item(close)

    async def _owner_only(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("This isn't your request.", ephemeral=True)
            return False
        return True

    async def _cancel(self, interaction: discord.Interaction) -> None:
        if not await self._owner_only(interaction):
            return
        self.stop()
        await interaction.response.edit_message(content="Cancelled.", embed=None, view=None)

    async def _confirm(self, interaction: discord.Interaction) -> None:
        if not await self._owner_only(interaction):
            return
        self.stop()
        await interaction.response.edit_message(content=f"⏳ Adding **{self.candidate.title}**…", embed=None, view=None)

        profile_id, root_folder = _profile_and_root(self.kind)
        try:
            if self.kind == "movie":
                movie = await radarr.add_movie(self.candidate.raw, profile_id, root_folder, search=True)
                external_id, title = movie.id, movie.title
            else:
                series_type = "anime" if self.kind == "anime" else "standard"
                search = self.kind != "anime"  # never let Sonarr blind-search an anime release
                series = await sonarr.add_series(self.candidate.raw, profile_id, root_folder, series_type, search=search)
                external_id, title = series.id, series.title
        except Exception as exc:  # noqa: BLE001
            await log(f"bot: add failed for '{self.candidate.title}' ({self.kind}): {exc}")
            await interaction.followup.send(f"⚠️ Couldn't add **{self.candidate.title}**: {exc}", ephemeral=True)
            return

        note = "Nyaarr will pick the right sub/dub release automatically on its next scan cycle." if self.kind == "anime" else "Searching now."

        if self.private:
            dm = await interaction.user.create_dm()
            await dm.send(f"🎬 **{title}** — added. {note} I'll post updates right here as it progresses.")
            await job_store.store.try_claim(self.kind, external_id, title=title, thread_id=dm.id, requester_id=interaction.user.id)
            await interaction.followup.send(f"✅ Added **{title}** — check your DMs for updates.", ephemeral=True)
            return

        # The add already succeeded above — from here on, nothing may be
        # allowed to leave this job permanently untracked. Confirmed live:
        # a permission error on the #requests post used to blow up
        # unhandled here, leaving a real Sonarr/Radarr add with no thread
        # and no job_store entry at all — added, but silently orphaned.
        target = _target_channel(interaction.channel, interaction.guild)
        try:
            public_msg = await target.send(f"🎬 **{title}** requested by {interaction.user.mention} — added. {note}")
            thread = await public_msg.create_thread(name=title[:100])
            await job_store.store.try_claim(self.kind, external_id, title=title, thread_id=thread.id, requester_id=interaction.user.id)
            await interaction.followup.send(f"✅ Added **{title}** — updates will show up in {thread.mention}.", ephemeral=True)
        except Exception as exc:  # noqa: BLE001
            await log(f"bot: couldn't post/thread request for '{title}' in #{REQUESTS_CHANNEL_NAME}: {exc} — falling back to DM")
            dm = await interaction.user.create_dm()
            await dm.send(f"🎬 **{title}** — added. {note} (Couldn't post in #{REQUESTS_CHANNEL_NAME} — sending updates here instead.)")
            await job_store.store.try_claim(self.kind, external_id, title=title, thread_id=dm.id, requester_id=interaction.user.id)
            await interaction.followup.send(f"✅ Added **{title}** — couldn't post in #{REQUESTS_CHANNEL_NAME} (permissions?), so updates will DM you instead.", ephemeral=True)


class PickerView(discord.ui.View):
    """Step 1 — up to 5 numbered candidates, each its own embed with its
    own poster, plus a Cancel button.
    """

    def __init__(self, kind: str, candidates: list[matcher.Candidate], already: list[Optional[str]], requester_id: int, private: bool = False):
        super().__init__(timeout=180)
        self.kind = kind
        self.candidates = candidates
        self.already = already
        self.requester_id = requester_id
        self.private = private

        for i in range(len(candidates)):
            btn = discord.ui.Button(label=str(i + 1), style=discord.ButtonStyle.primary)
            btn.callback = self._make_pick(i)
            self.add_item(btn)
        cancel = discord.ui.Button(label="Cancel", emoji="❌", style=discord.ButtonStyle.secondary)
        cancel.callback = self._cancel
        self.add_item(cancel)

    def _make_pick(self, index: int):
        async def _pick(interaction: discord.Interaction) -> None:
            if interaction.user.id != self.requester_id:
                await interaction.response.send_message("This isn't your request.", ephemeral=True)
                return
            self.stop()
            candidate = self.candidates[index]
            already_status = self.already[index]
            embed = matcher.confirm_embed(candidate, already_status)
            view = ConfirmView(self.kind, candidate, already_status, self.requester_id, private=self.private)
            await interaction.response.edit_message(content=None, embeds=[embed], view=view)

        return _pick

    async def _cancel(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("This isn't your request.", ephemeral=True)
            return
        self.stop()
        await interaction.response.edit_message(content="Cancelled.", embeds=[], view=None)


async def _search_movie(interaction: discord.Interaction, title: str) -> None:
    await interaction.response.defer(ephemeral=True)
    results = (await radarr.lookup_by_term(title))[: settings.max_candidates]
    if not results:
        await interaction.followup.send(f"No movie matches found for '{title}'.", ephemeral=True)
        return

    candidates = [matcher.from_movie(m) for m in results]
    already: list[Optional[str]] = []
    for m in results:
        existing = await radarr.find_by_tmdb_id(m.tmdb_id)
        already.append(("Downloaded" if existing.has_file else "Monitored — searching") if existing else None)

    embeds = [matcher.candidate_list_embed(c, i + 1, already[i]) for i, c in enumerate(candidates)]
    view = PickerView("movie", candidates, already, interaction.user.id)
    await interaction.followup.send(content=f"Found {len(candidates)} match(es) for **{title}** — pick one:", embeds=embeds, view=view, ephemeral=True)


async def _search_series(interaction: discord.Interaction, title: str, kind: str) -> None:
    await interaction.response.defer(ephemeral=True)
    results = await sonarr.lookup_by_term(title)
    if kind == "anime":
        results.sort(key=lambda s: not s.looks_like_anime)  # anime-looking results first, never decided silently
    results = results[: settings.max_candidates]
    if not results:
        await interaction.followup.send(f"No show matches found for '{title}'.", ephemeral=True)
        return

    candidates = [matcher.from_series(s) for s in results]
    already: list[Optional[str]] = []
    for s in results:
        existing = await sonarr.find_by_tvdb_id(s.tvdb_id)
        already.append("Already added" if existing else None)

    embeds = [matcher.candidate_list_embed(c, i + 1, already[i]) for i, c in enumerate(candidates)]
    view = PickerView(kind, candidates, already, interaction.user.id)
    await interaction.followup.send(content=f"Found {len(candidates)} match(es) for **{title}** — pick one:", embeds=embeds, view=view, ephemeral=True)


@tree.command(name="movie", description="Request a movie")
@app_commands.describe(title="Movie title")
async def movie_command(interaction: discord.Interaction, title: str) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("Use this in a server channel.", ephemeral=True)
        return
    await _search_movie(interaction, title)


@tree.command(name="tv", description="Request a TV show")
@app_commands.describe(title="TV show title")
async def tv_command(interaction: discord.Interaction, title: str) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("Use this in a server channel.", ephemeral=True)
        return
    await _search_series(interaction, title, "tv")


@tree.command(name="anime", description="Request an anime series")
@app_commands.describe(title="Anime title")
async def anime_command(interaction: discord.Interaction, title: str) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("Use this in a server channel.", ephemeral=True)
        return
    await _search_series(interaction, title, "anime")


@client.event
async def on_ready() -> None:
    await log(f"requestarr: logged in as {client.user}")
    if settings.discord_guild_id:
        guild = discord.Object(id=int(settings.discord_guild_id))
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
        # Wipe any stale global registration from before a guild id was set —
        # otherwise Discord shows both the global and guild-specific copies
        # of every command stacked together in the picker.
        tree.clear_commands(guild=None)
        await tree.sync()
        await log(f"requestarr: synced commands to guild {settings.discord_guild_id} (instant); cleared global commands")
    else:
        await tree.sync()
        await log("requestarr: synced commands globally (can take up to an hour to propagate on first use)")


async def start() -> None:
    if not settings.discord_bot_token:
        await log("requestarr: DISCORD_BOT_TOKEN not set — bot will not connect. Set it in .env and restart once you've created a bot application (see README).")
        return
    await client.start(settings.discord_bot_token)
