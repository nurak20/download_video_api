from typing import Dict, Optional, List, Any
import os
import json
import re
import html as html_lib
import urllib.request
import http.cookiejar
from datetime import datetime

import yt_dlp

from app.core.config import COOKIE_FILE, COOKIE_REQUIRED_PLATFORMS
from app.models.requests import ListingRequest
from app.services.listing.base import ListingService


class YtDlpListingService(ListingService):
    _INSTAGRAM_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Referer": "https://www.instagram.com/",
        "Origin": "https://www.instagram.com",
        "X-IG-App-ID": "936619743392459",
        "X-ASBD-ID": "198387",
        "X-IG-WWW-Claim": "0",
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

    @staticmethod
    def _format_upload_date(timestamp: Optional[int]) -> Optional[str]:
        if not timestamp:
            return None
        try:
            return datetime.utcfromtimestamp(int(timestamp)).strftime("%Y%m%d")
        except (ValueError, TypeError, OSError):
            return None

    @staticmethod
    def _apply_limit(items: List[Dict[str, Optional[str]]], limit: Optional[int]) -> List[Dict[str, Optional[str]]]:
        if limit and limit > 0:
            return items[:limit]
        return items

    @staticmethod
    def _pick_best_candidate(candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not candidates:
            return None

        def score(item: Dict[str, Any]) -> int:
            width = item.get("width") or 0
            height = item.get("height") or 0
            return int(width) * int(height)

        return max(candidates, key=score)

    def _build_instagram_opener(self) -> urllib.request.OpenerDirector:
        if os.path.exists(COOKIE_FILE):
            jar = http.cookiejar.MozillaCookieJar(COOKIE_FILE)
            try:
                jar.load(ignore_discard=True, ignore_expires=True)
            except Exception:
                jar = None
            if jar is not None:
                return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        return urllib.request.build_opener()

    def _download_instagram_text(self, url: str, headers: Optional[Dict[str, str]] = None) -> str:
        opener = self._build_instagram_opener()
        request_headers = dict(self._INSTAGRAM_HEADERS)
        if headers:
            request_headers.update(headers)
        req = urllib.request.Request(url, headers=request_headers)
        with opener.open(req, timeout=20) as resp:
            return resp.read().decode("utf-8", "ignore")

    def _download_instagram_json(self, url: str, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        text = self._download_instagram_text(url, headers=headers)
        payload = self._try_parse_json(text)
        if payload is None:
            raise ValueError(
                "Instagram returned a non-JSON response (login may be required or rate limit reached)."
            )
        return payload

    @staticmethod
    def _try_parse_json(text: str) -> Optional[Any]:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _extract_balanced_json(text: str, start_index: int) -> Optional[str]:
        if start_index < 0 or start_index >= len(text):
            return None
        while start_index < len(text) and text[start_index] not in "{[":
            start_index += 1
        if start_index >= len(text):
            return None

        stack: List[str] = []
        in_string = False
        escape = False
        for idx in range(start_index, len(text)):
            ch = text[idx]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
                continue
            if ch in "{[":
                stack.append(ch)
                continue
            if ch in "}]":
                if not stack:
                    return None
                stack.pop()
                if not stack:
                    return text[start_index : idx + 1]
        return None

    def _extract_json_from_html(self, html: str) -> Optional[Dict[str, Any]]:
        next_match = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
        if next_match:
            try:
                return json.loads(html_lib.unescape(next_match.group(1)))
            except json.JSONDecodeError:
                pass

        patterns = [
            r"window\._sharedData\s*=\s*",
            r"window\.__additionalDataLoaded\s*\(\s*[^,]+,",
        ]
        for pattern in patterns:
            match = re.search(pattern, html)
            if not match:
                continue
            json_blob = self._extract_balanced_json(html, match.end())
            if not json_blob:
                continue
            try:
                return json.loads(json_blob)
            except json.JSONDecodeError:
                continue
        return None

    def _extract_caption_from_graphql(self, media: Dict[str, Any]) -> str:
        edges = media.get("edge_media_to_caption", {}).get("edges")
        if isinstance(edges, list) and edges:
            node = edges[0].get("node") if isinstance(edges[0], dict) else None
            caption = node.get("text") if isinstance(node, dict) else None
            if caption:
                return self._as_str(caption) or ""
        return self._as_str(media.get("caption")) or ""

    def _build_video_item(
        self,
        video_url: Optional[str],
        caption: str,
        video_id: Optional[str],
        thumbnail_url: Optional[str],
        timestamp: Optional[int],
        width: Optional[int],
        height: Optional[int],
        source_url: Optional[str],
        duration: Optional[object] = None,
    ) -> Dict[str, Optional[str]]:
        return {
            "video_url": video_url,
            "video_id": video_id,
            "caption": caption,
            "thumbnail_url": thumbnail_url,
            "timestamp": self._as_str(timestamp),
            "upload_date": self._format_upload_date(timestamp),
            "width": self._as_str(width),
            "height": self._as_str(height),
            "duration": self._as_str(duration),
            "source_url": source_url,
        }

    def _build_image_item(
        self,
        image_url: Optional[str],
        caption: str,
        image_id: Optional[str],
        thumbnail_url: Optional[str],
        timestamp: Optional[int],
        width: Optional[int],
        height: Optional[int],
        source_url: Optional[str],
    ) -> Dict[str, Optional[str]]:
        return {
            "image_url": image_url,
            "image_id": image_id,
            "caption": caption,
            "thumbnail_url": thumbnail_url,
            "timestamp": self._as_str(timestamp),
            "upload_date": self._format_upload_date(timestamp),
            "width": self._as_str(width),
            "height": self._as_str(height),
            "source_url": source_url,
        }

    def _build_highlight_item(
        self,
        highlight_url: Optional[str],
        caption: str,
        highlight_id: Optional[str],
        media_type: str,
        thumbnail_url: Optional[str],
        timestamp: Optional[int],
        width: Optional[int],
        height: Optional[int],
        source_url: Optional[str],
    ) -> Dict[str, Optional[str]]:
        return {
            "highlight_url": highlight_url,
            "highlight_id": highlight_id,
            "caption": caption,
            "media_type": media_type,
            "thumbnail_url": thumbnail_url,
            "timestamp": self._as_str(timestamp),
            "upload_date": self._format_upload_date(timestamp),
            "width": self._as_str(width),
            "height": self._as_str(height),
            "source_url": source_url,
        }

    def _extract_from_shortcode_media(
        self,
        media: Dict[str, Any],
        source_url: str,
    ) -> tuple[List[Dict[str, Optional[str]]], List[Dict[str, Optional[str]]]]:
        caption = self._extract_caption_from_graphql(media)
        timestamp = media.get("taken_at_timestamp") or media.get("taken_at")
        nodes = []
        sidecar = media.get("edge_sidecar_to_children", {}).get("edges")
        if isinstance(sidecar, list) and sidecar:
            for edge in sidecar:
                if isinstance(edge, dict) and isinstance(edge.get("node"), dict):
                    nodes.append(edge["node"])
        else:
            nodes = [media]

        videos: List[Dict[str, Optional[str]]] = []
        images: List[Dict[str, Optional[str]]] = []
        for node in nodes:
            is_video = bool(node.get("is_video")) or node.get("__typename") == "GraphVideo"
            node_caption = caption
            node_timestamp = node.get("taken_at_timestamp") or timestamp
            node_id = self._as_str(node.get("id") or node.get("shortcode"))
            width = node.get("dimensions", {}).get("width")
            height = node.get("dimensions", {}).get("height")
            thumbnail = self._as_str(node.get("thumbnail_src") or node.get("display_url"))
            if is_video:
                video_url = self._as_str(node.get("video_url"))
                if not video_url and node.get("shortcode"):
                    video_url = f"https://www.instagram.com/p/{node.get('shortcode')}/"
                videos.append(
                    self._build_video_item(
                        video_url=video_url,
                        caption=node_caption,
                        video_id=node_id,
                        thumbnail_url=thumbnail,
                        timestamp=node_timestamp,
                        width=width,
                        height=height,
                        source_url=source_url,
                    )
                )
            else:
                image_url = self._as_str(node.get("display_url") or node.get("thumbnail_src"))
                images.append(
                    self._build_image_item(
                        image_url=image_url,
                        caption=node_caption,
                        image_id=node_id,
                        thumbnail_url=image_url or thumbnail,
                        timestamp=node_timestamp,
                        width=width,
                        height=height,
                        source_url=source_url,
                    )
                )

        return videos, images

    def _extract_from_product_item(
        self,
        media: Dict[str, Any],
        source_url: str,
    ) -> tuple[List[Dict[str, Optional[str]]], List[Dict[str, Optional[str]]]]:
        caption = ""
        caption_obj = media.get("caption")
        if isinstance(caption_obj, dict):
            caption = self._as_str(caption_obj.get("text")) or ""
        else:
            caption = self._as_str(caption_obj) or ""

        timestamp = media.get("taken_at") or media.get("taken_at_timestamp")
        media_items = media.get("carousel_media") if isinstance(media.get("carousel_media"), list) else [media]

        videos: List[Dict[str, Optional[str]]] = []
        images: List[Dict[str, Optional[str]]] = []
        for item in media_items:
            if not isinstance(item, dict):
                continue
            item_id = self._as_str(item.get("id") or item.get("pk") or item.get("code"))
            item_timestamp = item.get("taken_at") or timestamp
            video_versions = item.get("video_versions") or []
            if isinstance(video_versions, list) and video_versions:
                best_video = self._pick_best_candidate(video_versions)
                video_url = self._as_str(best_video.get("url") if best_video else None)
                if not video_url:
                    video_url = source_url
                width = best_video.get("width") if isinstance(best_video, dict) else None
                height = best_video.get("height") if isinstance(best_video, dict) else None
                image_candidates = item.get("image_versions2", {}).get("candidates")
                best_image = self._pick_best_candidate(image_candidates or [])
                thumbnail = self._as_str(best_image.get("url") if best_image else None)
                videos.append(
                    self._build_video_item(
                        video_url=video_url,
                        caption=caption,
                        video_id=item_id,
                        thumbnail_url=thumbnail,
                        timestamp=item_timestamp,
                        width=width,
                        height=height,
                        source_url=source_url,
                    )
                )
                continue

            image_candidates = item.get("image_versions2", {}).get("candidates")
            best_image = self._pick_best_candidate(image_candidates or [])
            image_url = self._as_str(best_image.get("url") if best_image else None)
            width = best_image.get("width") if isinstance(best_image, dict) else None
            height = best_image.get("height") if isinstance(best_image, dict) else None
            if image_url:
                images.append(
                    self._build_image_item(
                        image_url=image_url,
                        caption=caption,
                        image_id=item_id,
                        thumbnail_url=image_url,
                        timestamp=item_timestamp,
                        width=width,
                        height=height,
                        source_url=source_url,
                    )
                )

        return videos, images

    def _extract_instagram_post_media(
        self, url: str
    ) -> tuple[List[Dict[str, Optional[str]]], List[Dict[str, Optional[str]]], Optional[str]]:
        try:
            html = self._download_instagram_text(url)
        except Exception as exc:
            return [], [], str(exc)

        data = self._extract_json_from_html(html)
        if not data:
            embed_url = url.rstrip("/") + "/embed/"
            if embed_url != url:
                try:
                    embed_html = self._download_instagram_text(embed_url)
                except Exception as exc:
                    return [], [], str(exc)
                data = self._extract_json_from_html(embed_html)
        if not data:
            return [], [], "Unable to parse Instagram data from the page."

        media = None
        entry_data = data.get("entry_data")
        if isinstance(entry_data, dict):
            post_pages = entry_data.get("PostPage")
            if isinstance(post_pages, list) and post_pages:
                post_page = post_pages[0]
                if isinstance(post_page, dict):
                    graphql = post_page.get("graphql")
                    if isinstance(graphql, dict):
                        media = graphql.get("shortcode_media")
                    if media is None:
                        maybe_media = post_page.get("media")
                        if isinstance(maybe_media, dict):
                            media = maybe_media

        if media is None:
            if isinstance(data.get("items"), list) and data["items"]:
                media = data["items"][0]
            elif isinstance(data.get("graphql"), dict):
                media = data["graphql"].get("shortcode_media") or data.get("shortcode_media")

        if not isinstance(media, dict):
            return [], [], "Unable to locate media payload in Instagram response."

        if any(key in media for key in ("carousel_media", "image_versions2", "video_versions")):
            videos, images = self._extract_from_product_item(media, url)
        else:
            videos, images = self._extract_from_shortcode_media(media, url)

        return videos, images, None

    def _video_items_from_ytdlp_info(self, info: Dict[str, Any]) -> List[Dict[str, Optional[str]]]:
        entries = info.get("entries") if isinstance(info.get("entries"), list) else [info]
        items: List[Dict[str, Optional[str]]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue

            formats = entry.get("formats")
            video_url = None
            width = None
            height = None
            if isinstance(formats, list) and formats:
                best_format = self._pick_best_candidate(formats)
                if isinstance(best_format, dict):
                    video_url = self._as_str(best_format.get("url"))
                    width = best_format.get("width")
                    height = best_format.get("height")

            if not video_url:
                video_url = self._as_str(
                    entry.get("url") or entry.get("webpage_url") or entry.get("original_url")
                )

            caption = self._as_str(entry.get("description") or entry.get("title")) or ""
            video_id = self._as_str(entry.get("id") or entry.get("video_id"))
            thumbnail = self._as_str(entry.get("thumbnail"))
            timestamp = entry.get("timestamp")
            duration = entry.get("duration")
            source_url = self._as_str(entry.get("webpage_url") or entry.get("original_url"))

            if not video_url and not video_id:
                continue

            items.append(
                self._build_video_item(
                    video_url=video_url,
                    caption=caption,
                    video_id=video_id,
                    thumbnail_url=thumbnail,
                    timestamp=timestamp if isinstance(timestamp, int) else None,
                    width=width,
                    height=height,
                    source_url=source_url,
                    duration=duration,
                )
            )

        return items

    def _list_videos_from_ytdlp(self, data: ListingRequest) -> tuple[List[Dict[str, Optional[str]]], Optional[str]]:
        ydl_opts = self._build_listing_opts(data)
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(data.url, download=False)
            if not isinstance(info, dict):
                return [], "Unable to extract info for the provided URL."
            return self._video_items_from_ytdlp_info(info), None
        except Exception as exc:
            return [], str(exc)

    def _extract_story_user_info(self, html: str) -> Optional[Dict[str, Any]]:
        match = re.search(r'"user":', html)
        if not match:
            return None
        json_blob = self._extract_balanced_json(html, match.end())
        if not json_blob:
            return None
        try:
            return json.loads(json_blob)
        except json.JSONDecodeError:
            return None

    def _list_instagram_story_items_by_reel_id(
        self, reel_id: str, source_url: str
    ) -> tuple[List[Dict[str, Optional[str]]], Optional[str]]:
        api_url = f"https://i.instagram.com/api/v1/feed/reels_media/?reel_ids={reel_id}"
        try:
            payload = self._download_instagram_json(api_url)
        except Exception as exc:
            return [], str(exc)

        reels = payload.get("reels", {}) if isinstance(payload, dict) else {}
        reel = reels.get(reel_id, {}) if isinstance(reels, dict) else {}
        if not reel and isinstance(reels, dict) and reels:
            reel = next(iter(reels.values()))
        items = reel.get("items") if isinstance(reel, dict) else None
        if not isinstance(items, list):
            return [], "Unable to locate story items."

        highlight_items: List[Dict[str, Optional[str]]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = self._as_str(item.get("id") or item.get("pk"))
            timestamp = item.get("taken_at") or item.get("taken_at_timestamp")
            caption_obj = item.get("caption")
            if isinstance(caption_obj, dict):
                caption = self._as_str(caption_obj.get("text")) or ""
            else:
                caption = self._as_str(caption_obj) or ""

            video_versions = item.get("video_versions") or []
            if isinstance(video_versions, list) and video_versions:
                best_video = self._pick_best_candidate(video_versions)
                video_url = self._as_str(best_video.get("url") if isinstance(best_video, dict) else None)
                width = best_video.get("width") if isinstance(best_video, dict) else None
                height = best_video.get("height") if isinstance(best_video, dict) else None
                image_candidates = item.get("image_versions2", {}).get("candidates")
                best_image = self._pick_best_candidate(image_candidates or [])
                thumbnail = self._as_str(best_image.get("url") if isinstance(best_image, dict) else None)
                if not video_url:
                    video_url = source_url
                highlight_items.append(
                    self._build_highlight_item(
                        highlight_url=video_url,
                        caption=caption,
                        highlight_id=item_id,
                        media_type="video",
                        thumbnail_url=thumbnail,
                        timestamp=timestamp if isinstance(timestamp, int) else None,
                        width=width,
                        height=height,
                        source_url=source_url,
                    )
                )
                continue

            image_candidates = item.get("image_versions2", {}).get("candidates")
            best_image = self._pick_best_candidate(image_candidates or [])
            image_url = self._as_str(best_image.get("url") if isinstance(best_image, dict) else None)
            width = best_image.get("width") if isinstance(best_image, dict) else None
            height = best_image.get("height") if isinstance(best_image, dict) else None
            if image_url:
                highlight_items.append(
                    self._build_highlight_item(
                        highlight_url=image_url,
                        caption=caption,
                        highlight_id=item_id,
                        media_type="image",
                        thumbnail_url=image_url,
                        timestamp=timestamp if isinstance(timestamp, int) else None,
                        width=width,
                        height=height,
                        source_url=source_url,
                    )
                )

        return highlight_items, None

    def _list_instagram_story_items(
        self, url: str
    ) -> tuple[List[Dict[str, Optional[str]]], Optional[str]]:
        highlight_match = re.search(r"/stories/highlights/(\d+)", url)
        if highlight_match:
            highlight_id = highlight_match.group(1)
            return self._list_instagram_story_items_by_reel_id(f"highlight:{highlight_id}", url)

        user_match = re.search(r"/stories/([^/?#]+)", url)
        if not user_match:
            return [], "Invalid Instagram stories URL."
        try:
            html = self._download_instagram_text(url)
        except Exception as exc:
            return [], str(exc)

        user_info = self._extract_story_user_info(html)
        user_id = None
        if isinstance(user_info, dict):
            user_id = user_info.get("pk") or user_info.get("id")

        if not user_id:
            return [], "Unable to extract story user info."

        return self._list_instagram_story_items_by_reel_id(str(user_id), url)

    @staticmethod
    def _is_instagram_profile_url(url: str) -> bool:
        return (
            re.match(r"^https?://(?:www\.)?instagram\.com/[^/?#]+/?(?:[?#].*)?$", url)
            is not None
        )

    def _extract_profile_shortcodes_from_html(self, html: str) -> tuple[List[str], Optional[str]]:
        data = self._extract_json_from_html(html)
        if not data:
            return [], "Unable to parse Instagram profile data."

        shortcodes = self._collect_shortcodes_from_data(data)
        if shortcodes:
            return shortcodes, None

        user = None
        entry_data = data.get("entry_data")
        if isinstance(entry_data, dict):
            profile_pages = entry_data.get("ProfilePage")
            if isinstance(profile_pages, list) and profile_pages:
                profile_page = profile_pages[0]
                if isinstance(profile_page, dict):
                    graphql = profile_page.get("graphql")
                    if isinstance(graphql, dict):
                        user = graphql.get("user")

        if user is None and isinstance(data.get("graphql"), dict):
            user = data["graphql"].get("user")

        if not isinstance(user, dict):
            return [], "Unable to locate Instagram profile payload."

        timeline = user.get("edge_owner_to_timeline_media")
        edges = timeline.get("edges") if isinstance(timeline, dict) else None
        if not isinstance(edges, list):
            return [], "Unable to locate Instagram profile posts."

        shortcodes = self._collect_shortcodes_from_data(edges)
        if not shortcodes:
            return [], "No posts found on the profile."

        return shortcodes, None

    def _collect_shortcodes_from_data(self, data: Any) -> List[str]:
        found: List[str] = []

        def walk(obj: Any) -> None:
            nonlocal found
            if found:
                return
            if isinstance(obj, dict):
                edges = obj.get("edges")
                if isinstance(edges, list):
                    shortcodes: List[str] = []
                    for edge in edges:
                        if not isinstance(edge, dict):
                            continue
                        node = edge.get("node")
                        if not isinstance(node, dict):
                            continue
                        shortcode = node.get("shortcode")
                        if isinstance(shortcode, str) and shortcode:
                            shortcodes.append(shortcode)
                    if shortcodes:
                        found = shortcodes
                        return
                for value in obj.values():
                    walk(value)
                    if found:
                        return
            elif isinstance(obj, list):
                for value in obj:
                    walk(value)
                    if found:
                        return

        walk(data)
        return found

    def _list_instagram_profile_media(
        self, url: str, limit: Optional[int]
    ) -> tuple[List[Dict[str, Optional[str]]], List[Dict[str, Optional[str]]], Optional[str]]:
        try:
            html = self._download_instagram_text(url)
        except Exception as exc:
            return [], [], str(exc)

        shortcodes, error = self._extract_profile_shortcodes_from_html(html)
        if error:
            json_error = None
            json_shortcodes: List[str] = []
            for suffix in ("?__a=1&__d=dis", "?__a=1"):
                try:
                    text = self._download_instagram_text(
                        url.rstrip("/") + "/" + suffix,
                        headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
                    )
                except Exception as exc:
                    json_error = str(exc)
                    continue

                payload = self._try_parse_json(text)
                if payload is None:
                    payload = self._extract_json_from_html(text)
                if payload is None:
                    json_error = (
                        "Instagram returned a non-JSON response (login may be required or rate limit reached)."
                    )
                    continue
                json_shortcodes = self._collect_shortcodes_from_data(payload)
                if json_shortcodes:
                    break
            if json_shortcodes:
                shortcodes = json_shortcodes
            else:
                return [], [], json_error or error

        if limit and limit > 0:
            shortcodes = shortcodes[:limit]

        videos: List[Dict[str, Optional[str]]] = []
        images: List[Dict[str, Optional[str]]] = []
        seen_video: set[str] = set()
        seen_image: set[str] = set()
        last_error: Optional[str] = None

        for shortcode in shortcodes:
            post_url = f"https://www.instagram.com/p/{shortcode}/"
            post_videos, post_images, post_error = self._extract_instagram_post_media(post_url)
            if post_error and not (post_videos or post_images):
                last_error = post_error
                continue

            for item in post_videos:
                key = (item.get("video_id") or item.get("video_url") or "").strip()
                if key and key in seen_video:
                    continue
                if key:
                    seen_video.add(key)
                videos.append(item)

            for item in post_images:
                key = (item.get("image_id") or item.get("image_url") or "").strip()
                if key and key in seen_image:
                    continue
                if key:
                    seen_image.add(key)
                images.append(item)

        if not videos and not images and last_error:
            return [], [], last_error

        return videos, images, None

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

    def list_instagram_media(
        self, data: ListingRequest
    ) -> tuple[Optional[Dict[str, List[Dict[str, Optional[str]]]]], Optional[str]]:
        if data.platform.lower() != "instagram":
            return None, "Platform must be instagram for this endpoint."

        if "/stories/" in data.url:
            highlight_items, error = self._list_instagram_story_items(data.url)
            if error:
                return None, error
            highlight_items = self._apply_limit(highlight_items, data.limit)
            return {"video": [], "image": [], "highlight": highlight_items}, None

        if self._is_instagram_profile_url(data.url):
            videos, images, error = self._list_instagram_profile_media(data.url, data.limit)
            if error and not (videos or images):
                return None, error
            return {"video": videos, "image": images, "highlight": []}, None

        videos, images, error = self._extract_instagram_post_media(data.url)
        if not videos:
            fallback_videos, fallback_error = self._list_videos_from_ytdlp(data)
            if fallback_videos:
                videos = fallback_videos
            elif error is None:
                error = fallback_error

        if error and not (videos or images):
            return None, error

        videos = self._apply_limit(videos, data.limit)
        images = self._apply_limit(images, data.limit)
        return {"video": videos, "image": images, "highlight": []}, None
