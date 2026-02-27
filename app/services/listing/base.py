from abc import ABC, abstractmethod
from typing import Dict, Optional, List

from app.models.requests import ListingRequest


class ListingService(ABC):
    @abstractmethod
    def list_videos(self, data: ListingRequest) -> tuple[Optional[List[Dict[str, Optional[str]]]], Optional[str]]:
        raise NotImplementedError

    @abstractmethod
    def list_instagram_media(
        self,
        data: ListingRequest,
    ) -> tuple[Optional[Dict[str, List[Dict[str, Optional[str]]]]], Optional[str]]:
        raise NotImplementedError
