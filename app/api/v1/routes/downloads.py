import os
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse

from app.core.config import ZIP_CHUNK_SIZE
from app.models.requests import DownloadRequest
from app.services.tiktok.tiktok_service import DownloadService, InMemoryJobStore, YtDlpDownloadService
from app.utils.files import create_zip_for_folder, file_iterator

router = APIRouter()

_job_store = InMemoryJobStore()
_download_service = YtDlpDownloadService(_job_store)


def get_download_service() -> DownloadService:
    return _download_service


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
