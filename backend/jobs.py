"""In-memory job store with background processing.

Good enough for a self-hosted, single-user tool: jobs run on daemon threads
and progress is polled over HTTP. For multi-user/production use you'd swap this
for a real queue (e.g. Redis + RQ/Celery) and persistent storage.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional


@dataclass
class Job:
    id: str
    url: str
    mode: str                    # "auto" | "manual"
    reframe: str
    captions: bool
    highlight: bool
    language: Optional[str]
    # Manual mode:
    start: Optional[float] = None
    end: Optional[float] = None
    # Auto mode:
    num_clips: int = 3
    min_len: float = 15.0
    max_len: float = 60.0
    # Progress / results:
    status: str = "queued"       # queued | running | done | error
    stage: str = "queued"
    progress: int = 0
    message: str = "Queued"
    title: Optional[str] = None
    clips: List[dict] = field(default_factory=list)
    error: Optional[str] = None


class JobStore:
    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, **kwargs) -> Job:
        job_id = uuid.uuid4().hex[:12]
        job = Job(id=job_id, **kwargs)
        with self._lock:
            self._jobs[job_id] = job
        return job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    @staticmethod
    def to_dict(job: Job) -> dict:
        return asdict(job)


store = JobStore()


def _run_job(job: Job) -> None:
    from .pipeline.process import process_job

    def update(**fields) -> None:
        for key, value in fields.items():
            setattr(job, key, value)

    job.status = "running"
    try:
        process_job(job, update)
        job.status = "done"
    except Exception as exc:  # surface the failure to the UI rather than crash
        job.status = "error"
        job.error = str(exc)
        job.message = "Failed"


def start_job(job: Job) -> None:
    threading.Thread(target=_run_job, args=(job,), daemon=True).start()
