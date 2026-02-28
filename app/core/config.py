from pathlib import Path

COOKIE_REQUIRED_PLATFORMS = {"facebook", "instagram", "tiktok"}
ROOT_DIR = Path(__file__).resolve().parents[2]
COOKIE_FILE = str(ROOT_DIR / "cookies.txt")
DEFAULT_DOWNLOAD_DIR = "downloads"
ZIP_CHUNK_SIZE = 1024 * 1024
