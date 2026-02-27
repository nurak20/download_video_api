from threading import Lock
from typing import Dict, Optional

from app.models.jobs import JobRecord
from app.services.job_store.base import JobStore


class InMemoryJobStore(JobStore):
    def __init__(self) -> None:
        self._jobs: Dict[str, JobRecord] = {}
        self._lock = Lock()

    def create(self, job_id: str, job: JobRecord) -> None:
        with self._lock:
            self._jobs[job_id] = job

    def get(self, job_id: str) -> Optional[JobRecord]:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def update(self, job_id: str, **updates: object) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.update(updates)
