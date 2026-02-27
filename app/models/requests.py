from typing import Optional

from pydantic import BaseModel


class DownloadRequest(BaseModel):
    platform: str
    url: str
    limit: Optional[int] = None
    location: Optional[str] = "downloads"
    folder_name: Optional[str] = None


class ListingRequest(BaseModel):
    platform: str
    url: str
    limit: Optional[int] = None


class DirectDownloadRequest(BaseModel):
    url: str
