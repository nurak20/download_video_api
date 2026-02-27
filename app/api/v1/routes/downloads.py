import os
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse
import yt_dlp

from app.core.config import COOKIE_FILE, COOKIE_REQUIRED_PLATFORMS, ZIP_CHUNK_SIZE
from app.core.errors import normalize_error_message
from app.models.requests import DirectDownloadRequest, DownloadRequest, ListingRequest
from app.services.downloader.base import DownloadService
from app.services.downloader.ytdlp import YtDlpDownloadService
from app.services.job_store.memory import InMemoryJobStore
from app.services.listing.base import ListingService
from app.services.listing.ytdlp import YtDlpListingService
from app.utils.files import create_zip_for_folder, file_iterator
from app.utils.time import now_ts

router = APIRouter()

_job_store = InMemoryJobStore()
_download_service = YtDlpDownloadService(_job_store)
_listing_service = YtDlpListingService()


def get_download_service() -> DownloadService:
    return _download_service


def get_listing_service() -> ListingService:
    return _listing_service


@router.post("/download")
def download_videos(
    data: DownloadRequest,
    background_tasks: BackgroundTasks,
    service: DownloadService = Depends(get_download_service),
):
    return service.queue_download(data, background_tasks)


@router.get("/download/{job_id}")
def get_download_status(
    job_id: str,
    service: DownloadService = Depends(get_download_service),
):
    job = service.get_job(job_id)
    if not job:
        return {"status": "error", "message": "job not found"}
    return job


@router.get("/download-zip/{job_id}")
def download_zip(
    job_id: str,
    background_tasks: BackgroundTasks,
    service: DownloadService = Depends(get_download_service),
):
    job = service.get_job(job_id)
    if not job:
        return {"status": "error", "message": "job not found"}

    if job.get("status") != "finished":
        return {"status": "error", "message": "job not finished yet"}

    folder = Path(job["saved_to"])

    if not folder.exists():
        return {"status": "error", "message": "download folder not found"}

    zip_path = create_zip_for_folder(folder, job_id)

    background_tasks.add_task(os.remove, zip_path)

    headers = {"Content-Disposition": f'attachment; filename="{folder.name}.zip"'}
    return StreamingResponse(
        file_iterator(zip_path, ZIP_CHUNK_SIZE),
        media_type="application/zip",
        headers=headers,
    )


@router.post("/list-videos")
def list_videos_with_caption(
    data: ListingRequest,
    service: ListingService = Depends(get_listing_service),
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


@router.post("/list-instagram-media")
def list_instagram_media(
    data: ListingRequest,
    service: ListingService = Depends(get_listing_service),
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


@router.post("/download-tiktok")
def download_tiktok_video(
    data: DirectDownloadRequest,
    background_tasks: BackgroundTasks,
):
    ffmpeg_available = shutil.which("ffmpeg") is not None
    temp_dir = Path(tempfile.mkdtemp(prefix="tiktok-"))

    ydl_opts: dict[str, object] = {
        "outtmpl": os.path.join(temp_dir.as_posix(), "%(title).80s.%(ext)s"),
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        if ffmpeg_available
        else "best[ext=mp4]",
        "quiet": True,
        "restrictfilenames": True,
        "ignoreerrors": "only_download",
        "no_warnings": True,
        "noplaylist": True,
    }

    if ffmpeg_available:
        ydl_opts["merge_output_format"] = "mp4"
        ydl_opts["postprocessors"] = [
            {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"},
        ]

    if "tiktok" in COOKIE_REQUIRED_PLATFORMS and os.path.exists(COOKIE_FILE):
        ydl_opts["cookiefile"] = COOKIE_FILE

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(data.url, download=True)
    except Exception as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        message = normalize_error_message("tiktok", str(exc), ffmpeg_available)
        return {"status": "error", "message": message}

    file_path: Path | None = None
    if isinstance(info, dict):
        for key in ("_filename", "filepath"):
            candidate = info.get(key)
            if isinstance(candidate, str) and candidate and os.path.exists(candidate):
                file_path = Path(candidate)
                break
        if file_path is None:
            requested = info.get("requested_downloads")
            if isinstance(requested, list):
                for item in requested:
                    if not isinstance(item, dict):
                        continue
                    candidate = item.get("filepath")
                    if isinstance(candidate, str) and candidate and os.path.exists(candidate):
                        file_path = Path(candidate)
                        break

    if file_path is None:
        candidates = [
            path
            for path in temp_dir.iterdir()
            if path.is_file()
            and not path.name.endswith(".part")
            and path.suffix.lower() not in {".txt", ".ytdl"}
        ]
        if candidates:
            file_path = max(candidates, key=lambda path: path.stat().st_size)

    if file_path is None or not file_path.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)
        return {"status": "error", "message": "Downloaded file not found."}

    background_tasks.add_task(shutil.rmtree, temp_dir, ignore_errors=True)

    suffix = file_path.suffix.lower()
    media_type = {
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
        ".mov": "video/quicktime",
    }.get(suffix, "application/octet-stream")

    headers = {"Content-Disposition": f'attachment; filename="{file_path.name}"'}
    return StreamingResponse(
        file_iterator(file_path, ZIP_CHUNK_SIZE),
        media_type=media_type,
        headers=headers,
    )
