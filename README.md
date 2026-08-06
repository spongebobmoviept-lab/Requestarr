<p align="center">
  <img src="docs/banner.svg" alt="Requestarr" width="700" />
</p>

<p align="center">
  <img alt="Docker" src="https://img.shields.io/badge/docker-required-2496ED?logo=docker&logoColor=white">
  <img alt="Python" src="https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white">
  <img alt="Discord" src="https://img.shields.io/badge/discord-bot-5865F2?logo=discord&logoColor=white">
  <img alt="Radarr" src="https://img.shields.io/badge/radarr-optional-ffc230?logo=radarr&logoColor=black">
  <img alt="Sonarr" src="https://img.shields.io/badge/sonarr-optional-35c5f4?logo=sonarr&logoColor=white">
  <img alt="status" src="https://img.shields.io/badge/status-active-brightgreen">
</p>

Requestarr is a self-hosted Discord bot for requesting movies, TV shows, and anime — three slash commands, no Plex account linking, no dashboard to check. It adds things directly to Sonarr/Radarr and posts a running status update in a thread as your request moves from "searching" to "available."

- `/movie title:<text>` → adds to Radarr, searches immediately.
- `/tv title:<text>` → adds to Sonarr as a normal series, searches immediately.
- `/anime title:<text>` → adds to Sonarr tagged as anime, but does **not** trigger Sonarr's own search — if you also run [Nyaarr](https://github.com/spongebobmoviept-lab/Nyaarr), its background scan picks up the newly-added series and finds the right sub/dub release on its own next cycle instead of Sonarr grabbing the first (possibly wrong) result blind.

Every command shows up to 5 candidates (poster, year, network/studio, status, language, and a clear "already in your library" flag) to pick from, then a full-detail confirm screen before anything is actually added — nothing gets added until you press Confirm.

## Quick start

**You'll need:** Docker + Docker Compose, and a Discord bot application (a few clicks, see below) — Sonarr and Radarr are both optional, connect whichever you actually use.

```bash
git clone https://github.com/spongebobmoviept-lab/Requestarr.git
cd Requestarr
docker-compose up -d
```

Open `http://<this-machine's-ip>:8787` and follow the setup wizard:

1. Create your login
2. Connect Discord (bot token + optional server ID for instant command sync)
3. Connect Radarr — tests the connection live and lets you pick a quality profile + root folder for `/movie`
4. Connect Sonarr — same, but picks profile/root pairs for **both** `/tv` and `/anime` independently
5. Dry run — on by default; leave it on to try the full picker/confirm/thread flow against real search data without anything actually being added, flip it off once you trust it

Only the admin login and a Discord bot token are required to finish setup. Radarr and Sonarr are each independently optional — skip either and that request type just won't have anywhere to add to until you connect it later from `/setup`.

### Getting a Discord bot token

You have to do this part yourself — there's no way to create a Discord application on your behalf.

1. [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**.
2. **Bot** tab → **Reset Token** → copy it.
3. **OAuth2 → URL Generator**: scopes `bot` + `applications.commands`; permissions `Send Messages`, `Create Public Threads`, `Send Messages in Threads`, `Embed Links`. Open the generated URL to invite it to your server.
4. Enable Developer Mode (User Settings → Advanced) to copy your server ID for instant command sync.

## Configuration reference

Everything below is set through the setup wizard or by revisiting `/setup` later.

| Setting | Default | What it does |
|---|---|---|
| Movie quality profile / root folder | — | Where `/movie` adds to Radarr |
| TV quality profile / root folder | — | Where `/tv` adds to Sonarr |
| Anime quality profile / root folder | — | Where `/anime` adds to Sonarr |
| Max candidates shown | 5 | How many search results the picker shows |
| Poll interval | 60 sec | How often the status monitor checks job progress |
| Max job duration | 6 hours | How long before giving up and posting "couldn't find anything" |
| Dry run | on | Logs what would be added instead of actually calling Sonarr/Radarr |

## FAQ

**What does dry run actually skip?**
Only the final "add to Sonarr/Radarr" call. The picker, confirm screen, and search results are all real — you're seeing exactly what would happen, just without it happening yet.

**Can I run this without Sonarr or Radarr?**
Yes, each is independent. Skip whichever you don't use in the wizard; that request type just won't be available until you connect it.

**Why doesn't `/anime` search immediately like `/tv` does?**
Because Sonarr searching blind for anime tends to grab the first available release regardless of sub/dub or fansub group quality. If you run Nyaarr, it handles that curation on its own schedule instead. If you don't run Nyaarr, you'll need to trigger a search for anime adds manually from Sonarr.

**Does this need a Plex account or link to specific users?**
No — anyone who can use slash commands in your server can request. There's no per-user Plex linking.

## Troubleshooting

- **The container won't start / crashes immediately.** Check `docker-compose logs -f requestarr` — a common first-run cause is `./data` being created as root before the container's non-root user can write to it: `mkdir -p data && sudo chown 1000:1000 data`.
- **The wizard's "Test & Continue" fails.** Double check the URL includes `http://` and the correct port, and that it's reachable *from inside the container* — `localhost` almost never works here, use the machine's real LAN IP.
- **Slash commands aren't showing up in Discord.** Without a server (guild) ID, commands sync globally, which can take up to an hour. Add the guild ID in the wizard's Discord step for instant sync.
- **Found a bug or want a feature?** Open an issue on this repo.

## Design principles

- One Python process, one container, no database, no build step.
- Nothing gets added without an explicit Confirm click — ever.
- Dry run exercises the entire real flow (search, picker, confirm) except the final write, so you can trust it before turning it off.
