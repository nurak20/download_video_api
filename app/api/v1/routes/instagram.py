import shutil

from fastapi import APIRouter, Depends

from app.core.errors import normalize_error_message
from app.models.requests import ListingRequest
from app.services.instagram.insta_service import InstagramService
from app.utils.time import now_ts

router = APIRouter()
_instagram_service = InstagramService()


def get_instagram_service() -> InstagramService:
    return _instagram_service


@router.post("/list-instagram-media")
def list_instagram_media(
    data: ListingRequest,
    service: InstagramService = Depends(get_instagram_service),
):
    items, error = service.list_instagram_media(data)
    if error:
        message = normalize_error_message(
            data.platform,
            error,
            ffmpeg_available=shutil.which("ffmpeg") is not None,
        )
        return {"status": "error", "message": message}
    return {
        "data": {
            "total_video": len(items.get("video", [])),
            "total_image": len(items.get("image", [])),
            "total_highlight": len(items.get("highlight", [])),
            "query_date": now_ts(),
            "video": items.get("video", []),
            "image": items.get("image", []),
            "highlight": items.get("highlight", []),
        }
    }
