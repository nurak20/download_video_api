import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from app.models.requests import DownloadRequest


def derive_folder_name(data: DownloadRequest) -> str:
    if data.folder_name and data.folder_name.strip():
        return data.folder_name.strip()

    parsed = urlparse(data.url)
    path = parsed.path or ""
    if data.platform.lower() == "tiktok":
        # TikTok profile URLs look like /@username
        if "/@" in path:
            candidate = path.split("/@", 1)[1].split("/", 1)[0]
            if candidate:
                return candidate
    # Fallback to last non-empty path segment
    segments = [seg for seg in path.split("/") if seg]
    if segments:
        return segments[-1]
    return "videos"


def create_zip_for_folder(folder: Path, job_id: str) -> Path:
    temp_file = tempfile.NamedTemporaryFile(
        mode="wb",
        suffix=f"-{job_id}.zip",
        prefix=f"{folder.name}-",
        delete=False,
    )
    temp_file.close()
    zip_path = Path(temp_file.name)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for file in folder.glob("*"):
            if file.is_file():
                zipf.write(file, file.name)

    return zip_path


def file_iterator(path: Path, chunk_size: int):
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            yield chunk
