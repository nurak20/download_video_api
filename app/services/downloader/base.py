from abc import ABC, abstractmethod
from typing import Optional

from fastapi import BackgroundTasks

from app.models.jobs import JobRecord
from app.models.requests import DownloadRequest


class DownloadService(ABC):
    @abstractmethod
    def queue_download(self, data: DownloadRequest, background_tasks: BackgroundTasks) -> dict:
        raise NotImplementedError

    @abstractmethod
    def get_job(self, job_id: str) -> Optional[JobRecord]:
        raise NotImplementedError
