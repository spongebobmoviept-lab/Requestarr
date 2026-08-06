import asyncio
import datetime
import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Optional

from .config import settings

JobKind = Literal["movie", "tv", "anime"]
JobStatus = Literal["searching", "downloading", "done", "failed"]


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _key(kind: JobKind, external_id: int) -> str:
    return f"{kind}:{external_id}"


@dataclass
class Job:
    kind: JobKind
    external_id: int
    title: str
    thread_id: int
    requester_id: int
    status: JobStatus = "searching"
    # Freeform, kind-specific poll-state (e.g. which queue ids have already
    # been announced) — kept generic here rather than proliferating
    # dataclass fields per kind, since movie/tv/anime each track slightly
    # different things.
    context: dict = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)


class JobStore:
    """One entry per active request being polled for status — same shape as
    Reclaimarr's jobs.py, minus anything related to touching/preserving
    files (this bot never does that, it only ever adds+searches).
    """

    MAX_HISTORY = 300

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self.active_jobs: dict[str, Job] = {}
        self.history: list[dict[str, Any]] = []

    async def load(self) -> None:
        if not os.path.exists(self._path):
            return
        with open(self._path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        self.active_jobs = {k: Job(**v) for k, v in raw.get("active_jobs", {}).items()}
        self.history = raw.get("history", [])

    async def _save(self) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        payload = {
            "active_jobs": {k: asdict(v) for k, v in self.active_jobs.items()},
            "history": self.history,
        }
        tmp_path = self._path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp_path, self._path)

    async def try_claim(self, kind: JobKind, external_id: int, **fields: Any) -> Optional[Job]:
        key = _key(kind, external_id)
        async with self._lock:
            if key in self.active_jobs:
                return None
            job = Job(kind=kind, external_id=external_id, **fields)
            self.active_jobs[key] = job
            await self._save()
            return job

    async def get(self, kind: JobKind, external_id: int) -> Optional[Job]:
        return self.active_jobs.get(_key(kind, external_id))

    async def update(self, kind: JobKind, external_id: int, **fields: Any) -> None:
        async with self._lock:
            key = _key(kind, external_id)
            job = self.active_jobs.get(key)
            if job is None:
                return
            for name, value in fields.items():
                setattr(job, name, value)
            job.updated_at = _now()
            await self._save()

    async def release(self, kind: JobKind, external_id: int) -> None:
        async with self._lock:
            self.active_jobs.pop(_key(kind, external_id), None)
            await self._save()

    async def record_history(self, kind: JobKind, external_id: int, title: str, outcome: str, detail: str) -> None:
        async with self._lock:
            self.history.insert(0, {"kind": kind, "external_id": external_id, "title": title, "outcome": outcome, "detail": detail, "at": _now()})
            self.history = self.history[: self.MAX_HISTORY]
            await self._save()


store = JobStore(settings.jobs_file)
