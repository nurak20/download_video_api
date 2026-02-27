def normalize_error_message(platform: str, message: str, ffmpeg_available: bool) -> str:
    if platform.lower() == "tiktok":
        if "private or has embedding disabled" in message or "Unable to extract secondary user ID" in message:
            return (
                "TikTok profile is private or embedding is disabled. "
                "Use a logged-in cookies.txt or try a tiktokuser:channel_id URL."
            )
    if not ffmpeg_available and (
        "ffmpeg not found" in message
        or "Requested format is not available" in message
        or "requested format is not available" in message
    ):
        return (
            "ffmpeg not found. Install ffmpeg to enable MP4 conversion/merging. "
            "Without ffmpeg, only videos with a direct MP4 file can be downloaded."
        )
    return message
