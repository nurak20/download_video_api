import shutil

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse

from app.core.errors import normalize_error_message
from app.models.requests import DirectDownloadRequest, ListingRequest
from app.services.tiktok.tiktok_service import TikTokService
from app.utils.time import now_ts

router = APIRouter()
_tiktok_service = TikTokService()


def get_tiktok_service() -> TikTokService:
    return _tiktok_service


@router.post("/download-tiktok")
def download_tiktok_video(
    data: DirectDownloadRequest,
    background_tasks: BackgroundTasks,
    service: TikTokService = Depends(get_tiktok_service),
):
    result = service.download_video(data, background_tasks)
    if isinstance(result, dict) and result.get("status") == "error":
        return result
    return StreamingResponse(
        result["response"],
        media_type=result["media_type"],
        headers=result["headers"],
    )


@router.post("/list-videos")
def list_videos_with_caption(
    data: ListingRequest,
    service: TikTokService = Depends(get_tiktok_service),
):
    items, error = service.list_videos(data)
    if error:
        message = normalize_error_message(
            data.platform,
            error,
            ffmpeg_available=shutil.which("ffmpeg") is not None,
        )
        return {"status": "error", "message": message}
    return {
        "data": {
            "total_item": len(items),
            "query_date": now_ts(),
            "list": items,
        }
    }
