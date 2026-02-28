from typing import Dict, Optional, List, Any
import os
import json
import re
import html as html_lib
import urllib.request
import http.cookiejar
from datetime import datetime

from app.core.config import COOKIE_FILE
from app.models.requests import ListingRequest


class InstagramService:
    _INSTAGRAM_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.instagram.com/",
        "Origin": "https://www.instagram.com",
        "X-IG-App-ID": "936619743392459",
        "X-ASBD-ID": "198387",
        "X-IG-WWW-Claim": "0",
    }
    _INSTAGRAM_API_UA = "Instagram 219.0.0.12.117 Android"
    _INSTASCRAPE_UA = (
        "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/87.0.4280.88 Mobile Safari/537.36 Edg/87.0.664.57"
    )

    def list_videos(
        self, data: ListingRequest
    ) -> tuple[Optional[List[Dict[str, Optional[str]]]], Optional[str]]:
        return None, "Instagram listing is only available via /list-instagram-media."

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

    def _read_instagram_cookies(self) -> Dict[str, str]:
        cookies: Dict[str, str] = {}
        if not os.path.exists(COOKIE_FILE):
            return cookies
        try:
            with open(COOKIE_FILE, "r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    line = line.strip("\n")
                    if not line:
                        continue
                    if line.startswith("#HttpOnly_"):
                        line = line[len("#HttpOnly_") :]
                    if line.startswith("#"):
                        continue
                    parts = line.split("\t")
                    if len(parts) < 7:
                        continue
                    domain, name, value = parts[0], parts[5], parts[6]
                    if "instagram.com" not in domain:
                        continue
                    cookies[name] = value
        except Exception:
            return {}
        return cookies

    @staticmethod
    def _format_cookie_header(cookies: Dict[str, str]) -> Optional[str]:
        if not cookies:
            return None
        return "; ".join(f"{name}={value}" for name, value in cookies.items())

    def _build_instascrape_headers(self, username: Optional[str] = None) -> Dict[str, str]:
        headers = {"user-agent": self._INSTASCRAPE_UA}
        if username:
            headers["referer"] = f"https://www.instagram.com/{username}/"
        cookies = self._read_instagram_cookies()
        cookie_header = self._format_cookie_header(cookies)
        if cookie_header:
            headers["cookie"] = cookie_header
            headers["Cookie"] = cookie_header
        return headers

    def _download_instagram_text(self, url: str, headers: Optional[Dict[str, str]] = None) -> str:
        opener = self._build_instagram_opener()
        request_headers = dict(self._INSTAGRAM_HEADERS)
        if headers:
            request_headers.update(headers)
        cookies = self._read_instagram_cookies()
        if cookies and "Cookie" not in request_headers:
            cookie_header = self._format_cookie_header(cookies)
            if cookie_header:
                request_headers["Cookie"] = cookie_header
        if cookies and "X-CSRFToken" not in request_headers and "csrftoken" in cookies:
            request_headers["X-CSRFToken"] = cookies["csrftoken"]
        req = urllib.request.Request(url, headers=request_headers)
        with opener.open(req, timeout=20) as resp:
            return resp.read().decode("utf-8", "ignore")

    def _download_instagram_json(self, url: str, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        text = self._download_instagram_text(url, headers=headers)
        payload = self._try_parse_json(text)
        if payload is None:
            payload = self._extract_json_from_html(text)
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

    @staticmethod
    def _extract_instagram_username(url: str) -> Optional[str]:
        match = re.match(r"^https?://(?:www\.)?instagram\.com/([^/?#]+)/?", url)
        if not match:
            return None
        username = match.group(1)
        if not username:
            return None
        return username

    def _build_instascrape_profile_from_api(self, username: str):
        try:
            from instascrape import Profile as InstaProfile
        except Exception as exc:
            return None, f"insta-scrape is not installed: {exc}"

        api_headers = {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"https://www.instagram.com/{username}/",
            "User-Agent": self._INSTAGRAM_API_UA,
        }
        api_urls = [
            f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}",
            f"https://i.instagram.com/api/v1/users/web_profile_info/?username={username}",
        ]

        payload = None
        last_error: Optional[str] = None
        for api_url in api_urls:
            try:
                payload = self._download_instagram_json(api_url, headers=api_headers)
                if payload is not None:
                    break
            except Exception as exc:
                last_error = str(exc)
                continue

        if payload is None:
            return None, last_error or "Instagram API request failed."

        user = None
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict):
                user = data.get("user")
            if user is None:
                user = payload.get("user")
            if user is None and isinstance(payload.get("graphql"), dict):
                user = payload["graphql"].get("user")

        if not isinstance(user, dict):
            return None, "Unable to locate Instagram profile payload."

        normalized = {"entry_data": {"ProfilePage": [{"graphql": {"user": user}}]}}
        profile = InstaProfile(normalized)
        try:
            profile.scrape(headers=self._build_instascrape_headers(username))
        except Exception as exc:
            return None, str(exc)

        return profile, None

    def _list_instagram_profile_media_with_instascrape(
        self, url: str, limit: Optional[int]
    ) -> tuple[List[Dict[str, Optional[str]]], List[Dict[str, Optional[str]]], Optional[str]]:
        try:
            from instascrape import Profile as InstaProfile
            from instascrape import Post as InstaPost
        except Exception as exc:
            return [], [], f"insta-scrape is not installed: {exc}"

        username = self._extract_instagram_username(url)
        if not username:
            return [], [], "Invalid Instagram profile URL."

        headers = self._build_instascrape_headers(username)
        if "cookie" not in headers or "sessionid=" not in headers.get("cookie", ""):
            return [], [], "Instagram cookies missing sessionid. Refresh cookies.txt with a logged-in session."

        profile = InstaProfile(username)
        profile_error = None
        try:
            profile.scrape(headers=headers)
        except Exception as exc:
            profile_error = str(exc)

        amt = limit if limit and limit > 0 else 12
        if amt > 12:
            amt = 12

        try:
            posts = profile.get_recent_posts(amt=amt)
        except Exception as exc:
            api_profile, api_error = self._build_instascrape_profile_from_api(username)
            if api_profile is None:
                return [], [], api_error or profile_error or str(exc)
            try:
                posts = api_profile.get_recent_posts(amt=amt)
            except Exception as inner_exc:
                return [], [], str(inner_exc)

        videos: List[Dict[str, Optional[str]]] = []
        images: List[Dict[str, Optional[str]]] = []
        for post in posts:
            is_video = bool(getattr(post, "is_video", False))
            caption = self._as_str(getattr(post, "caption", "")) or ""
            timestamp = getattr(post, "timestamp", None)
            shortcode = self._as_str(getattr(post, "shortcode", None) or getattr(post, "id", None))
            source_url = f"https://www.instagram.com/p/{shortcode}/" if shortcode else url
            dimensions = getattr(post, "dimensions", None)
            width = None
            height = None
            if isinstance(dimensions, dict):
                width = dimensions.get("width")
                height = dimensions.get("height")
            thumbnail = self._as_str(getattr(post, "display_url", None))

            if is_video:
                video_url = self._as_str(getattr(post, "video_url", None))
                if not video_url and shortcode:
                    try:
                        full_post = InstaPost(shortcode)
                        full_post.scrape(headers=headers)
                        video_url = self._as_str(getattr(full_post, "video_url", None))
                        if not thumbnail:
                            thumbnail = self._as_str(getattr(full_post, "display_url", None))
                    except Exception:
                        pass
                if not video_url:
                    video_url = source_url
                videos.append(
                    self._build_video_item(
                        video_url=video_url,
                        caption=caption,
                        video_id=shortcode,
                        thumbnail_url=thumbnail,
                        timestamp=timestamp if isinstance(timestamp, int) else None,
                        width=width,
                        height=height,
                        source_url=source_url,
                    )
                )
            else:
                image_url = self._as_str(getattr(post, "display_url", None))
                images.append(
                    self._build_image_item(
                        image_url=image_url,
                        caption=caption,
                        image_id=shortcode,
                        thumbnail_url=image_url or thumbnail,
                        timestamp=timestamp if isinstance(timestamp, int) else None,
                        width=width,
                        height=height,
                        source_url=source_url,
                    )
                )

        if not videos and not images:
            return [], [], "No posts found on the profile."

        return videos, images, None

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

    def _extract_caption_from_graphql(self, media: Dict[str, Any]) -> str:
        edges = media.get("edge_media_to_caption", {}).get("edges")
        if isinstance(edges, list) and edges:
            node = edges[0].get("node") if isinstance(edges[0], dict) else None
            caption = node.get("text") if isinstance(node, dict) else None
            if caption:
                return self._as_str(caption) or ""
        return self._as_str(media.get("caption")) or ""

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

    @staticmethod
    def _is_instagram_profile_url(url: str) -> bool:
        return (
            re.match(r"^https?://(?:www\.)?instagram\.com/[^/?#]+/?(?:[?#].*)?$", url)
            is not None
        )

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
            videos, images, error = self._list_instagram_profile_media_with_instascrape(
                data.url, data.limit
            )
            if error and not (videos or images):
                return None, error
            return {"video": videos, "image": images, "highlight": []}, None

        videos, images, error = self._extract_instagram_post_media(data.url)
        if error and not (videos or images):
            return None, error

        videos = self._apply_limit(videos, data.limit)
        images = self._apply_limit(images, data.limit)
        return {"video": videos, "image": images, "highlight": []}, None
