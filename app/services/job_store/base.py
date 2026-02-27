from abc import ABC, abstractmethod
from typing import Optional

from app.models.jobs import JobRecord


class JobStore(ABC):
    @abstractmethod
    def create(self, job_id: str, job: JobRecord) -> None:
        raise NotImplementedError

    @abstractmethod
    def get(self, job_id: str) -> Optional[JobRecord]:
        raise NotImplementedError

    @abstractmethod
    def update(self, job_id: str, **updates: object) -> None:
        raise NotImplementedError
