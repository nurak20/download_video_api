import os
import shutil
from typing import Dict, Optional
from uuid import uuid4

import yt_dlp
from fastapi import BackgroundTasks

from app.core.config import COOKIE_FILE, COOKIE_REQUIRED_PLATFORMS, DEFAULT_DOWNLOAD_DIR
from app.core.errors import normalize_error_message
from app.models.jobs import JobRecord
from app.models.requests import DownloadRequest
from app.services.downloader.base import DownloadService
from app.services.job_store.base import JobStore
from app.utils.files import derive_folder_name
from app.utils.time import now_ts


class YtDlpDownloadService(DownloadService):
    def __init__(self, job_store: JobStore) -> None:
        self._job_store = job_store

    def _update_job(self, job_id: str, **updates: object) -> None:
        self._job_store.update(job_id, **updates)

    def _build_ydl_opts(
        self,
        data: DownloadRequest,
        user_folder: str,
        progress_hook,
        ffmpeg_available: bool,
        for_estimate: bool = False,
    ) -> Dict[str, object]:
        ydl_opts: Dict[str, object] = {
            "outtmpl": os.path.join(user_folder, "%(title).80s.%(ext)s"),
            # Prefer MP4. If ffmpeg is available, we can merge/convert to MP4 as needed.
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
            if ffmpeg_available
            else "best[ext=mp4]",
            "quiet": True,
            "restrictfilenames": True,
            "ignoreerrors": "only_download",
            "no_warnings": True,
            "progress_hooks": [progress_hook],
        }
        if not for_estimate:
            ydl_opts["download_archive"] = os.path.join(user_folder, "downloaded.txt")
        if ffmpeg_available:
            ydl_opts["merge_output_format"] = "mp4"
            ydl_opts["postprocessors"] = [
                {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"},
            ]

        if data.platform.lower() in COOKIE_REQUIRED_PLATFORMS and os.path.exists(COOKIE_FILE):
            ydl_opts["cookiefile"] = COOKIE_FILE

        if data.limit and data.limit > 0:
            ydl_opts["playlistend"] = data.limit

        return ydl_opts

    def _estimate_total_items(
        self,
        data: DownloadRequest,
        ydl_opts: Dict[str, object],
    ) -> tuple[Optional[int], Optional[str]]:
        if data.limit and data.limit > 0:
            return data.limit, None
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(data.url, download=False)
            if not isinstance(info, dict):
                return None, "Unable to extract info for the provided URL."
            entries = info.get("entries")
            if entries is None:
                return 1, None
            if isinstance(entries, list):
                return len([entry for entry in entries if entry]), None
            return sum(1 for entry in entries if entry), None
        except Exception as exc:
            return None, str(exc)

    def _download_job(self, job_id: str, data: DownloadRequest, user_folder: str, ffmpeg_available: bool) -> None:
        def progress_hook(d: Dict[str, object]) -> None:
            status = d.get("status")
            if status == "downloading":
                downloaded = d.get("downloaded_bytes") or 0
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                current_progress = (downloaded / total) * 100 if total else None
                info = d.get("info_dict") or {}
                self._update_job(
                    job_id,
                    status="downloading",
                    current_title=info.get("title"),
                    current_progress=current_progress,
                )
            elif status == "finished":
                job = self._job_store.get(job_id)
                if job:
                    downloaded_items = int(job.get("downloaded_items") or 0) + 1
                    self._update_job(
                        job_id,
                        downloaded_items=downloaded_items,
                        current_progress=100.0,
                    )

        ydl_opts = self._build_ydl_opts(data, user_folder, progress_hook, ffmpeg_available)

        self._update_job(job_id, status="downloading")
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([data.url])
            self._update_job(job_id, status="finished", finished_at=now_ts())
        except Exception as exc:
            message = normalize_error_message(data.platform, str(exc), ffmpeg_available)
            self._update_job(job_id, status="error", error=message, finished_at=now_ts())

    def queue_download(self, data: DownloadRequest, background_tasks: BackgroundTasks) -> dict:
        base_folder = data.location or DEFAULT_DOWNLOAD_DIR
        folder_name = derive_folder_name(data)
        user_folder = os.path.join(base_folder, folder_name)

        job_id = str(uuid4())
        ffmpeg_available = shutil.which("ffmpeg") is not None
        ydl_opts_for_estimate = self._build_ydl_opts(
            data,
            user_folder,
            lambda _: None,
            ffmpeg_available,
            for_estimate=True,
        )
        total_items, precheck_error = self._estimate_total_items(data, ydl_opts_for_estimate)
        effective_limit = data.limit if data.limit and data.limit > 0 else None

        job: JobRecord = {
            "status": "queued",
            "platform": data.platform,
            "url": data.url,
            "saved_to": user_folder,
            "limit": effective_limit,
            "total_items": total_items,
            "downloaded_items": 0,
            "current_title": None,
            "current_progress": None,
            "error": None,
            "ffmpeg_available": ffmpeg_available,
            "created_at": now_ts(),
            "finished_at": None,
        }
        self._job_store.create(job_id, job)

        if precheck_error:
            message = normalize_error_message(data.platform, precheck_error, ffmpeg_available)
            self._update_job(job_id, status="error", error=message, finished_at=now_ts())
            return {
                "job_id": job_id,
                "status": "error",
                "message": message,
                "platform": data.platform,
                "saved_to": user_folder,
                "total_items": total_items,
                "downloaded_items": 0,
            }

        os.makedirs(user_folder, exist_ok=True)
        background_tasks.add_task(self._download_job, job_id, data, user_folder, ffmpeg_available)

        return {
            "job_id": job_id,
            "status": "queued",
            "platform": data.platform,
            "saved_to": user_folder,
            "total_items": total_items,
            "downloaded_items": 0,
        }

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        return self._job_store.get(job_id)
