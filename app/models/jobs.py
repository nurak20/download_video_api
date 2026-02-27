from typing import Optional, TypedDict


class JobRecord(TypedDict, total=False):
    status: str
    platform: str
    url: str
    saved_to: str
    limit: Optional[int]
    total_items: Optional[int]
    downloaded_items: int
    current_title: Optional[str]
    current_progress: Optional[float]
    error: Optional[str]
    ffmpeg_available: bool
    created_at: float
    finished_at: Optional[float]
