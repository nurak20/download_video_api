from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Optional, List
import os
import re
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

import yt_dlp
from fastapi import BackgroundTasks

from app.core.config import COOKIE_FILE, COOKIE_REQUIRED_PLATFORMS, DEFAULT_DOWNLOAD_DIR, ZIP_CHUNK_SIZE
from app.core.errors import normalize_error_message
from app.models.jobs import JobRecord
from app.models.requests import DownloadRequest, DirectDownloadRequest, ListingRequest
from app.services.instagram.insta_service import InstagramService
from app.utils.files import derive_folder_name, file_iterator
from app.utils.time import now_ts


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


class InMemoryJobStore(JobStore):
    def __init__(self) -> None:
        self._jobs: Dict[str, JobRecord] = {}

    def create(self, job_id: str, job: JobRecord) -> None:
        self._jobs[job_id] = job

    def get(self, job_id: str) -> Optional[JobRecord]:
        return self._jobs.get(job_id)

    def update(self, job_id: str, **updates: object) -> None:
        if job_id in self._jobs:
            self._jobs[job_id].update(updates)


class DownloadService(ABC):
    @abstractmethod
    def queue_download(self, data: DownloadRequest, background_tasks: BackgroundTasks) -> dict:
        raise NotImplementedError

    @abstractmethod
    def get_job(self, job_id: str) -> Optional[JobRecord]:
        raise NotImplementedError


class YtDlpDownloadService(DownloadService):
    def __init__(self, job_store: JobStore) -> None:
        self._job_store = job_store
        self._instagram_service = InstagramService()

    def _update_job(self, job_id: str, **updates: object) -> None:
        self._job_store.update(job_id, **updates)

    @staticmethod
    def _is_instagram_profile_url(url: str) -> bool:
        return (
            re.match(r"^https?://(?:www\.)?instagram\.com/[^/?#]+/?(?:[?#].*)?$", url)
            is not None
        )

    def _resolve_instagram_profile_urls(
        self,
        data: DownloadRequest,
    ) -> tuple[Optional[list[str]], Optional[str]]:
        if data.platform.lower() != "instagram":
            return None, None
        if not self._is_instagram_profile_url(data.url):
            return None, None

        listing_request = ListingRequest(platform="instagram", url=data.url, limit=data.limit)
        items, error = self._instagram_service.list_instagram_media(listing_request)
        if error:
            return None, error
        if not items:
            return [], "Unable to resolve Instagram media items."

        video_items = items.get("video", [])
        urls: list[str] = []
        for item in video_items:
            if not isinstance(item, dict):
                continue
            candidate = item.get("video_url") or item.get("source_url")
            if isinstance(candidate, str) and candidate.strip():
                urls.append(candidate.strip())

        if not urls:
            return [], "No videos found on the Instagram profile."
        return urls, None

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

    def _download_job(
        self,
        job_id: str,
        data: DownloadRequest,
        user_folder: str,
        ffmpeg_available: bool,
        resolved_urls: Optional[list[str]] = None,
    ) -> None:
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
                targets = resolved_urls if resolved_urls else [data.url]
                ydl.download(targets)
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
        resolved_urls, resolve_error = self._resolve_instagram_profile_urls(data)
        ydl_opts_for_estimate = self._build_ydl_opts(
            data,
            user_folder,
            lambda _: None,
            ffmpeg_available,
            for_estimate=True,
        )
        if resolve_error:
            resolved_urls = None
        if resolved_urls is not None:
            total_items = len(resolved_urls)
            precheck_error = None
        else:
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
        background_tasks.add_task(
            self._download_job,
            job_id,
            data,
            user_folder,
            ffmpeg_available,
            resolved_urls,
        )

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


class TikTokService:
    def download_video(self, data: DirectDownloadRequest, background_tasks: BackgroundTasks):
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
        return {
            "response": file_iterator(file_path, ZIP_CHUNK_SIZE),
            "media_type": media_type,
            "headers": headers,
        }

    def _build_listing_opts(self, data: ListingRequest) -> Dict[str, object]:
        ydl_opts: Dict[str, object] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "ignoreerrors": True,
        }

        if data.platform.lower() in COOKIE_REQUIRED_PLATFORMS and os.path.exists(COOKIE_FILE):
            ydl_opts["cookiefile"] = COOKIE_FILE

        if data.limit and data.limit > 0:
            ydl_opts["playlistend"] = data.limit

        return ydl_opts

    @staticmethod
    def _as_str(value: object) -> Optional[str]:
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return str(value)

    def _video_item_from_info(self, info: Dict[str, object]) -> Optional[Dict[str, Optional[str]]]:
        video_id = self._as_str(info.get("id") or info.get("video_id"))
        video_url = self._as_str(
            info.get("webpage_url") or info.get("url") or info.get("original_url")
        )
        caption = self._as_str(info.get("description") or info.get("title")) or ""
        upload_date = self._as_str(info.get("upload_date"))

        if not video_id and not video_url and not caption and not upload_date:
            return None

        return {
            "video_url": video_url,
            "video_id": video_id,
            "caption": caption,
            "upload_date": upload_date,
        }

    def list_videos(
        self, data: ListingRequest
    ) -> tuple[Optional[List[Dict[str, Optional[str]]]], Optional[str]]:
        ydl_opts = self._build_listing_opts(data)
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(data.url, download=False)
            if not isinstance(info, dict):
                return None, "Unable to extract info for the provided URL."

            entries = info.get("entries")
            if entries is None:
                item = self._video_item_from_info(info)
                return ([item] if item else []), None

            items: List[Dict[str, Optional[str]]] = []
            limit = data.limit if data.limit and data.limit > 0 else None
            for entry in entries:
                if not entry:
                    continue
                if not isinstance(entry, dict):
                    continue
                item = self._video_item_from_info(entry)
                if item:
                    items.append(item)
                    if limit and len(items) >= limit:
                        break

            return items, None
        except Exception as exc:
            return None, str(exc)
