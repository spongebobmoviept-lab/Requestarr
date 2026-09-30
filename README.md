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

Every command shows up to 5 candidates (poster, year, network/studio, status, language, and a clear "already in your library" flag) to pick from, then a full-detail confirm screen before anything is actually added — nothing gets added until you press Confirm. Results are re-ranked by how closely they match what you typed, so a small typo or a missing "The" still puts the right title first; if nothing matches, the bot says so and suggests checking the spelling.

## Quick start

**You'll need:** Docker + Docker Compose, and a Discord bot application (a few clicks, see below) — Sonarr and Radarr are both optional, connect whichever you actually use.

No need to clone or build anything: a ready-made image is published for amd64 and arm64 (Raspberry Pi 4/5 on a 64-bit OS).

```bash
mkdir requestarr && cd requestarr
curl -fsSLO https://raw.githubusercontent.com/spongebobmoviept-lab/Requestarr/main/docker-compose.yml
curl -fsSL -o .env.example https://raw.githubusercontent.com/spongebobmoviept-lab/Requestarr/main/.env.example
cp .env.example .env                        # optional overrides; the defaults work as-is
mkdir -p data && sudo chown 1000:1000 data  # container runs as a non-root user
docker compose up -d                        # pulls ghcr.io/spongebobmoviept-lab/requestarr
```

To build from source instead, clone the repo, uncomment `build:` in `docker-compose.yml`, and run `docker compose up -d --build`.

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

Two optional settings are set in `.env` only (see `.env.example`). They control the read-only `GET /api/jobs/active-downloads` endpoint, which lists requests that are currently downloading (`kind`, Radarr movie id or Sonarr series id, `title`; nothing about who asked). It's meant for an external helper, such as a script that keeps human requests at the front of your download client's queue.

| Env variable | Default | What it does |
|---|---|---|
| `ACTIVE_DOWNLOADS_API_KEY` | empty | When set, a caller can read the endpoint by sending this value in an `X-Api-Key` header. Use a long random string. |
| `ACTIVE_DOWNLOADS_ALLOW_UNAUTHENTICATED` | `false` | When `true`, anyone who can reach the port can read the endpoint with no auth (the pre-1.1 behaviour). Only for a trusted LAN. |

With neither set, the endpoint needs the admin login (HTTP Basic), like the rest of the API.

```bash
curl -H "X-Api-Key: $ACTIVE_DOWNLOADS_API_KEY" http://<host>:8787/api/jobs/active-downloads
# {"active_downloads": [{"kind": "movie", "external_id": 42, "title": "Some Movie"}]}
```

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

- **The container won't start / crashes immediately.** Check `docker compose logs -f requestarr` — a common first-run cause is `./data` being created as root before the container's non-root user can write to it: `mkdir -p data && sudo chown 1000:1000 data`.
- **The wizard's "Test & Continue" fails.** Double check the URL includes `http://` and the correct port, and that it's reachable *from inside the container* — `localhost` almost never works here, use the machine's real LAN IP.
- **Slash commands aren't showing up in Discord.** Without a server (guild) ID, commands sync globally, which can take up to an hour. Add the guild ID in the wizard's Discord step for instant sync.
- **Found a bug or want a feature?** Open an issue on this repo.

## Design principles

- One Python process, one container, no database, no build step.
- Nothing gets added without an explicit Confirm click — ever.
- Dry run exercises the entire real flow (search, picker, confirm) except the final write, so you can trust it before turning it off.

## Security notes

- **Keep the web UI on your LAN, not the open internet.** It uses HTTP Basic auth with a per-IP lockout after 10 failed attempts. Basic auth over plain HTTP sends the password in the clear, so for remote access put Requestarr behind a reverse proxy with HTTPS, or a VPN. The Discord bot itself needs no inbound port.
- **First-run setup is open until you finish it.** Until an admin login exists, anyone who can reach port 8787 can create it. Complete the wizard right after first start.
- **Unauthenticated endpoints:** only `/health` (returns `{"status": "ok"}`) and the setup pages. `/api/jobs/active-downloads` is authenticated by default; see the table above before opening it up.
- **Secrets stay in `data/`.** The Discord bot token, API keys and your hashed admin login live in the `data/` volume, never in the image. `.env` is git-ignored; only `.env.example` is tracked. API keys and Discord webhook tokens are redacted from log lines.
- **Anyone in your Discord server who can use slash commands can request.** Restrict the commands per role or channel in Discord's Server Settings → Integrations if you need to.
- The container runs as a non-root user (uid 1000).

## Changelog

**1.1.0**
- Search results are re-ranked by similarity to what was typed (typos, a missing "The", a different subtitle), and "no match" replies suggest checking the spelling.
- New read-only `GET /api/jobs/active-downloads` endpoint for external helpers, authenticated by default, with optional `ACTIVE_DOWNLOADS_API_KEY` / `ACTIVE_DOWNLOADS_ALLOW_UNAUTHENTICATED` settings.
- Security: Discord webhook tokens are redacted from logs; setup-wizard dropdowns treat Sonarr/Radarr names as text.
- `.env` is now `.env.example` (copy it to `.env`); `.env` is git-ignored. Docker base image pinned to a specific Python patch release.

**1.0.0**: first public release.

## License

MIT, see [LICENSE](LICENSE).
