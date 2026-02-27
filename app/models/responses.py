from typing import List, Optional

from pydantic import BaseModel


class VideoItem(BaseModel):
    video_url: Optional[str] = None
    caption: str = ""
    upload_date: Optional[str] = None
    video_id: Optional[str] = None


class VideoListData(BaseModel):
    total_item: int
    query_date: float
    list: List[VideoItem]


class VideoListResponse(BaseModel):
    data: VideoListData
