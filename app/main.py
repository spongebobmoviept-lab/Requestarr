import asyncio
import hmac
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from . import auth_store, bot, connections_store, job_store, monitor, radarr, settings_store, sonarr
from .auth import check_credentials, require_login, security
from .config import settings
from .logger import log

_background_tasks: list[asyncio.Task] = []

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")

# Explicit no-cache on every static asset — this app changes during setup/
# tuning, and a browser silently serving a stale cached page is a much worse
# failure mode than the tiny cost of re-fetching these small files on every load.
_NO_CACHE_HEADERS = {"Cache-Control": "no-cache, no-store, must-revalidate"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    os.makedirs(settings.data_dir, exist_ok=True)
    connections_store.load_overrides()
    settings_store.load_overrides()
    await job_store.store.load()
    monitor.set_client(bot.client)

    if settings.dry_run:
        await log("requestarr: DRY RUN mode — add calls will be logged, not executed")
    await log("requestarr: starting")
    _background_tasks.append(asyncio.create_task(bot.start()))
    _background_tasks.append(asyncio.create_task(monitor.poll_loop()))

    yield
    for task in _background_tasks:
        task.cancel()


app = FastAPI(title="Requestarr", version="1.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


_optional_basic = HTTPBasic(auto_error=False)


def _authorize_active_downloads(request: Request, credentials: HTTPBasicCredentials | None) -> None:
    """See the ACTIVE_DOWNLOADS_* settings in config.py for the rules."""
    if settings.active_downloads_allow_unauthenticated:
        return
    supplied = request.headers.get("x-api-key", "")
    expected = settings.active_downloads_api_key
    if expected and supplied and hmac.compare_digest(supplied.encode(), expected.encode()):
        return
    if credentials is not None:
        check_credentials(request, credentials)  # raises 401/429 on failure
        return
    raise HTTPException(
        status_code=401,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Basic"},
    )


@app.get("/api/jobs/active-downloads")
async def active_downloads(request: Request, credentials: HTTPBasicCredentials | None = Depends(_optional_basic)) -> JSONResponse:
    """Read-only list of requests that are currently downloading, so an
    external helper (for example a download-queue prioritiser) can tell
    which Sonarr/Radarr downloads trace back to a real person's request.
    Deliberately minimal: kind, the Radarr movie id or Sonarr series id,
    and the title.
    No requester ids, Discord thread ids or anything else from the job.
    """
    _authorize_active_downloads(request, credentials)
    jobs = [
        {"kind": j.kind, "external_id": j.external_id, "title": j.title}
        for j in job_store.store.active_jobs.values()
        if j.status == "downloading"
    ]
    return JSONResponse({"active_downloads": jobs}, headers={"Cache-Control": "no-store"})


@app.get("/favicon.svg")
async def favicon() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "favicon.svg"), media_type="image/svg+xml")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> Response:
    # Checked before auth on purpose: a fresh, unconfigured instance has no
    # working login yet, so gating "/" behind require_login here would just
    # show the browser's native Basic Auth popup with no credentials that
    # could possibly work — a dead end for a first-time visitor. Route them
    # to the wizard instead, which is what actually creates that login.
    if not auth_store.is_setup_complete():
        return RedirectResponse(url="/setup")
    credentials = await security(request)
    check_credentials(request, credentials)
    return FileResponse(os.path.join(STATIC_DIR, "setup.html"), headers=_NO_CACHE_HEADERS)


@app.get("/setup", response_class=HTMLResponse)
async def setup_page() -> FileResponse:
    # Same page serves both first-run setup AND later editing — once setup is
    # complete, the page's own JS asks for login and switches into an
    # unlocked "edit any step" mode instead of the sequential first-run flow.
    return FileResponse(os.path.join(STATIC_DIR, "setup.html"), headers=_NO_CACHE_HEADERS)


@app.get("/api/setup/status")
async def api_setup_status() -> JSONResponse:
    """Public on purpose — this is the very first call the page makes,
    before any login exists, to decide whether to show the wizard at all.
    """
    return JSONResponse(
        {
            "setup_complete": auth_store.is_setup_complete(),
            "admin_configured": auth_store.is_admin_configured(),
            "discord_configured": bool(settings.discord_bot_token),
        }
    )


@app.post("/api/setup/admin")
async def api_setup_admin(body: dict) -> JSONResponse:
    """Creates the one and only admin login. Deliberately not behind
    require_login — there's no login yet to require. Guarded instead by
    "only works once": the moment an admin exists, this refuses, so nobody
    else on the LAN can hijack an instance after the fact.
    """
    if auth_store.is_admin_configured():
        raise HTTPException(status_code=403, detail="An admin login already exists for this instance")
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    if not username or len(password) < 8:
        raise HTTPException(status_code=400, detail="Username is required and password must be at least 8 characters")
    auth_store.set_admin(username, password)
    await log(f"setup: admin login created for '{username}'")
    return JSONResponse({"ok": True})


@app.post("/api/setup/test-discord")
async def api_setup_test_discord(body: dict, _: str = Depends(require_login)) -> JSONResponse:
    token = body.get("token", "")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://discord.com/api/v10/users/@me",
                headers={"Authorization": f"Bot {token}"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Couldn't connect: {exc}")
    return JSONResponse({"username": f"{data.get('username', 'unknown')}"})


@app.post("/api/setup/test-radarr")
async def api_setup_test_radarr(body: dict, _: str = Depends(require_login)) -> JSONResponse:
    try:
        result = await radarr.test_connection(body.get("url", ""), body.get("api_key", ""))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Couldn't connect: {exc}")
    return JSONResponse(result)


@app.post("/api/setup/test-sonarr")
async def api_setup_test_sonarr(body: dict, _: str = Depends(require_login)) -> JSONResponse:
    try:
        result = await sonarr.test_connection(body.get("url", ""), body.get("api_key", ""))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Couldn't connect: {exc}")
    return JSONResponse(result)


@app.get("/api/connections")
async def api_get_connections(_: str = Depends(require_login)) -> JSONResponse:
    return JSONResponse(connections_store.current_display())


@app.post("/api/connections")
async def api_save_connections(update: dict, _: str = Depends(require_login)) -> JSONResponse:
    unknown = [k for k in update if k not in connections_store.ALL_KEYS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown connection field(s): {unknown}")
    result = connections_store.save_overrides(update)
    await log("connections: settings updated by user")
    return JSONResponse(result)


@app.get("/api/settings")
async def api_get_settings(_: str = Depends(require_login)) -> JSONResponse:
    return JSONResponse(settings_store.current_editable())


@app.post("/api/settings")
async def api_update_settings(update: dict, _: str = Depends(require_login)) -> JSONResponse:
    unknown = [k for k in update if k not in settings_store.EDITABLE_KEYS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown/non-editable setting(s): {unknown}")
    return JSONResponse(settings_store.save_overrides(update))
