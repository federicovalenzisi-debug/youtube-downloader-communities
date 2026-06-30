#!/usr/bin/env python3
"""Download ONLY the YouTube videos found on senior-living community pages.

Scans each community's Gallery page and its Home page, finds embedded YouTube
videos (iframes, links, data-* attributes and JSON-LD), and downloads them in
the best available quality with yt-dlp. No photos are downloaded.

Usage (command line):
    python youtube_downloader.py "https://example.seniorlivingnearme.com/gallery"
    python youtube_downloader.py --links links.txt

It is also importable from a Google Colab notebook (see README).
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Print Spanish text / symbols safely on Windows consoles too (Colab is already UTF-8).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


__version__ = "1.7"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0 Safari/537.36"
)
REQUEST_TIMEOUT = 30
DEFAULT_DOWNLOAD_ROOT = "downloads"
YOUTUBE_ID_PATTERN = re.compile(r"^[\w-]{11}$")
# Maximum quality regardless of codec/container. We always take the best video
# stream (so 1440p/4K VP9/AV1 are never capped), and prefer m4a audio so the
# common 1080p/720p H.264 case merges into a universally compatible .mp4. When
# only Opus audio or VP9/AV1 video exists (e.g. real 4K) yt-dlp falls back to the
# plain best streams and merges into .mkv -- quality is identical, only the
# container differs.
YOUTUBE_FORMAT = "bestvideo+bestaudio[ext=m4a]/bestvideo+bestaudio/best"
GENERIC_VIDEO_TITLES = {"hubspot video", "video", "watch video", "play video"}

# We let yt-dlp extract and COMBINE the formats from every enabled player client
# in a single pass and pick the single highest-resolution one (it does this cross-
# client selection natively). "default" is yt-dlp's own smart client set, which
# consumes the bgutil PO token when the Colab helper is running -- that token is
# what unlocks 1080p/4K from Colab's datacenter IP, where the plain web client is
# otherwise capped at 360p. tv_embedded + android_vr are robust no-token fallbacks
# for when the PO-token helper is off. Forcing one client at a time (the old
# approach) fought this smart selection and often lost resolution.
YOUTUBE_PLAYER_CLIENTS = ["default", "tv_embedded", "android_vr"]

# YouTube can still throttle Colab's datacenter IP mid-download and hand us a
# lower format than it advertised, so we retry the whole download a few times,
# keep the highest-resolution file across attempts, and pause between tries so
# the rate limit cools down.
MAX_DOWNLOAD_ATTEMPTS = 3
ATTEMPT_COOLDOWN_SECONDS = 5


@dataclass
class LinkEntry:
    url: str
    community_name: str = ""


@dataclass
class VideoCandidate:
    url: str
    title: str = ""
    source: str = ""
    page_source: str = "Gallery"


@dataclass
class CommunityResult:
    community_name: str
    gallery_url: str
    home_url: str
    output_folder: Path
    videos_found: int = 0
    videos_downloaded: int = 0
    videos_failed: int = 0
    home_skipped_duplicates: int = 0
    rows: list[dict] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# HTTP / rendering
# --------------------------------------------------------------------------- #
def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.6,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "HEAD"),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def fetch_html(url: str, session: requests.Session | None = None) -> str:
    session = session or create_session()
    response = session.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.text


def render_with_playwright_if_needed(url: str) -> str | None:
    """Render the page with Playwright for JavaScript/lazy-loaded content."""
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(user_agent=USER_AGENT, viewport={"width": 1440, "height": 1200})
            page.goto(url, wait_until="networkidle", timeout=60000)

            previous_height = 0
            stable_scrolls = 0
            for _ in range(12):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(800)
                current_height = page.evaluate("document.body.scrollHeight")
                if current_height == previous_height:
                    stable_scrolls += 1
                    if stable_scrolls >= 2:
                        break
                else:
                    stable_scrolls = 0
                    previous_height = current_height

            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except PlaywrightTimeoutError:
                pass

            html = page.content()
            browser.close()
            return html
    except Exception as exc:
        print(f"Playwright fallback failed: {exc}", file=sys.stderr)
        return None


# --------------------------------------------------------------------------- #
# YouTube URL helpers
# --------------------------------------------------------------------------- #
def extract_youtube_video_id(url: str) -> str | None:
    """Extract a YouTube video ID from common embed/watch/short URL formats."""
    if not url:
        return None

    cleaned = url.strip().replace("&amp;", "&")
    if cleaned.startswith("//"):
        cleaned = f"https:{cleaned}"
    parsed = urlparse(cleaned)
    host = parsed.netloc.lower()
    path = parsed.path

    if host in {"youtu.be", "www.youtu.be"}:
        video_id = path.strip("/").split("/")[0]
        return video_id if YOUTUBE_ID_PATTERN.fullmatch(video_id or "") else None

    if "youtube.com" in host or "youtube-nocookie.com" in host:
        video_id = ""
        if path.startswith("/embed/"):
            video_id = path.split("/embed/", 1)[1].split("/")[0]
        elif path.startswith("/shorts/"):
            video_id = path.split("/shorts/", 1)[1].split("/")[0]
        elif path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        return video_id if YOUTUBE_ID_PATTERN.fullmatch(video_id or "") else None

    return None


def canonical_youtube_url(url: str) -> str:
    video_id = extract_youtube_video_id(url)
    return f"https://www.youtube.com/watch?v={video_id}" if video_id else url


def looks_like_youtube_url(url: str) -> bool:
    return extract_youtube_video_id(url) is not None


def youtube_key(url: str) -> str:
    video_id = extract_youtube_video_id(url)
    return f"youtube:{video_id}" if video_id else url.lower()


def get_home_url(gallery_url: str) -> str:
    """Return the community Home URL from a Gallery URL."""
    parsed = urlparse(gallery_url)
    return urlunparse((parsed.scheme, parsed.netloc, "/", "", "", ""))


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #
def find_main_content_area(soup: BeautifulSoup) -> Tag:
    for selector in (".body-wrapper", ".dnd_area", "main", "body"):
        match = soup.select_one(selector)
        if match:
            return match
    return soup


def parse_json_ld_youtube_titles(soup: BeautifulSoup) -> dict[str, str]:
    """Map youtube_key -> title from JSON-LD VideoObject entries."""
    titles: dict[str, str] = {}
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            payload = json.loads(script.string)
        except json.JSONDecodeError:
            continue
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict):
                continue
            item_type = item.get("@type", "")
            types = item_type if isinstance(item_type, list) else [item_type]
            if "VideoObject" not in types:
                continue
            title = str(item.get("name") or "").strip()
            for url_key in ("contentUrl", "embedUrl"):
                media_url = item.get(url_key)
                if media_url and looks_like_youtube_url(media_url) and title:
                    titles.setdefault(youtube_key(media_url), title)
    return titles


def extract_youtube_videos(html: str, page_source: str = "Gallery") -> list[VideoCandidate]:
    """Extract YouTube video candidates in DOM order from a page."""
    soup = BeautifulSoup(html, "html.parser")
    main_content = find_main_content_area(soup)
    json_ld_titles = parse_json_ld_youtube_titles(soup)

    ordered: list[VideoCandidate] = []
    seen: set[str] = set()

    def add(raw_url: str | None, title: str = "", source: str = "") -> None:
        if not raw_url or not looks_like_youtube_url(raw_url):
            return
        normalized = canonical_youtube_url(raw_url)
        key = youtube_key(normalized)
        if key in seen:
            return
        seen.add(key)
        resolved_title = title.strip()
        if not resolved_title or resolved_title.lower() in GENERIC_VIDEO_TITLES:
            resolved_title = json_ld_titles.get(key, resolved_title)
        ordered.append(
            VideoCandidate(
                url=normalized,
                title=resolved_title,
                source=source or "dom",
                page_source=page_source,
            )
        )

    for element in main_content.descendants:
        if not isinstance(element, Tag):
            continue

        if element.name == "a":
            add(element.get("href"), title=element.get_text(" ", strip=True), source="youtube-link")

        if element.name == "iframe":
            for attr in ("data-hsv-src", "src", "data-src"):
                add(element.get(attr), title=element.get("title") or "", source="youtube-iframe")

        for attr in ("data-video", "data-video-url", "data-src", "data-url"):
            add(element.get(attr), source=f"data-{attr}")

    # JSON-LD entries that were not linked in the DOM.
    for key, title in json_ld_titles.items():
        video_id = key.split("youtube:", 1)[-1]
        add(f"https://www.youtube.com/watch?v={video_id}", title=title, source="json-ld")

    return ordered


def page_suggests_videos(html: str) -> bool:
    lowered = html.lower()
    return any(
        token in lowered
        for token in (
            "youtube.com/embed/",
            "youtube-nocookie.com/embed/",
            "youtu.be/",
            "youtube.com/watch",
            "videoobject",
        )
    )


def fetch_page_youtube_videos(
    page_url: str,
    page_source: str,
    session: requests.Session,
    use_playwright: bool,
) -> list[VideoCandidate]:
    """Fetch one page and return its YouTube videos, using Playwright if needed."""
    html = fetch_html(page_url, session=session)
    videos = deduplicate_videos(extract_youtube_videos(html, page_source=page_source))

    if use_playwright and not videos and page_suggests_videos(html):
        print(f"Trying Playwright fallback for {page_source} videos on {page_url}...")
        rendered = render_with_playwright_if_needed(page_url)
        if rendered:
            rendered_videos = deduplicate_videos(extract_youtube_videos(rendered, page_source=page_source))
            if len(rendered_videos) > len(videos):
                videos = rendered_videos
    return videos


def deduplicate_videos(videos: list[VideoCandidate]) -> list[VideoCandidate]:
    deduped: list[VideoCandidate] = []
    seen: set[str] = set()
    for video in videos:
        key = youtube_key(video.url)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(video)
    return deduped


# --------------------------------------------------------------------------- #
# Naming
# --------------------------------------------------------------------------- #
def sanitize_folder_name(name: str) -> str:
    name = html.unescape(name or "")
    name = re.sub(r'[<>:"/\\|?*]+', " ", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(".")
    return name or "Video"


def video_display_name(video: VideoCandidate) -> str:
    title = video.title.strip()
    if title and title.lower() not in GENERIC_VIDEO_TITLES:
        cleaned = re.sub(r"\s*\(\d+\)\s*(\(\d+\))?$", "", title).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        if cleaned:
            return sanitize_folder_name(cleaned)
    return sanitize_folder_name(f"Video {extract_youtube_video_id(video.url) or ''}".strip())


def community_folder_name(html: str, page_url: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    title = re.sub(r"\s*[-|]\s*gallery\s*$", "", title, flags=re.I)
    title = re.sub(r"\bgallery\b", "", title, flags=re.I).strip(" -|")
    title = re.sub(r"^lakehouse\s+", "", title, flags=re.I).strip()
    if not title:
        hostname = urlparse(page_url).hostname or "community"
        title = re.sub(r"[-_]+", " ", hostname.split(".")[0]).title()
    return sanitize_folder_name(title or "Community")


# --------------------------------------------------------------------------- #
# Download + screenshot
# --------------------------------------------------------------------------- #
class _QuietLogger:
    """Swallow yt-dlp's own output so failed retry attempts don't print scary
    ERROR lines. We keep only the last error to summarize it ourselves."""

    def __init__(self) -> None:
        self.last_error = ""

    def debug(self, msg: str) -> None:  # noqa: D401
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        text = str(msg).strip()
        if text:
            self.last_error = text


def _clean_error(message: str) -> str:
    """Shorten a yt-dlp error into something a non-technical user can read."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", message or "").strip()
    text = re.sub(r"^ERROR:\s*", "", text)
    lowered = text.lower()
    if "not available" in lowered or "video unavailable" in lowered:
        return "the video is no longer available on YouTube"
    if "private" in lowered:
        return "the video is private"
    if "403" in lowered or "forbidden" in lowered:
        return "YouTube blocked the download (try again)"
    if "requested format is not available" in lowered:
        return "no downloadable format was found"
    return text[:140] if text else "unknown error"


def _max_height(formats: list[dict] | None) -> int | None:
    """Best video height available among yt-dlp formats."""
    if not formats:
        return None
    heights = [f.get("height") or 0 for f in formats if (f.get("vcodec") or "none") != "none"]
    top = max(heights, default=0)
    return top or None


def _find_cookies_file() -> str | None:
    """Use a cookies.txt from the working directory if the user dropped one in.

    On a YouTube-flagged Colab IP, authenticated cookies are the infallible way
    to still get maximum quality (see the README). It is fully optional.
    """
    for name in ("cookies.txt", "youtube_cookies.txt"):
        if Path(name).is_file():
            return name
    return None


def _ydl_opts(video_folder: Path, logger: _QuietLogger) -> dict:
    opts = {
        "format": YOUTUBE_FORMAT,
        "format_sort": ["res", "fps", "vcodec:h264", "br"],
        "outtmpl": str(video_folder / "video.%(ext)s"),
        "merge_output_format": "mp4/mkv",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "noprogress": True,
        "logger": logger,
        "retries": 10,
        "fragment_retries": 10,
        "extractor_retries": 3,
        "extractor_args": {"youtube": {"player_client": YOUTUBE_PLAYER_CLIENTS}},
        "http_headers": {"User-Agent": USER_AGENT},
    }
    cookies = _find_cookies_file()
    if cookies:
        opts["cookiefile"] = cookies
    return opts


def _attempt_download(yt_dlp_mod, video_url: str, attempt_dir: Path,
                      logger: _QuietLogger) -> tuple[Path | None, int, int, str]:
    """One full download attempt into attempt_dir.

    yt-dlp combines the formats from every enabled player client and picks the
    single best one, so a single attempt already chases the maximum resolution.
    Returns (file, downloaded_height, available_height, error).
    """
    try:
        for leftover in attempt_dir.glob("video.*"):
            leftover.unlink(missing_ok=True)
        with yt_dlp_mod.YoutubeDL(_ydl_opts(attempt_dir, logger)) as ydl:
            info = ydl.extract_info(video_url, download=True)
        available = _max_height((info or {}).get("formats")) or 0
        files = [f for f in attempt_dir.glob("video.*") if f.suffix.lower() != ".jpg"]
        if not files:
            raise RuntimeError("yt-dlp did not produce an output file.")
        downloaded = get_video_height(files[0]) or (info or {}).get("height") or 0
        return files[0], downloaded, available, ""
    except Exception as exc:
        return None, 0, 0, (logger.last_error or str(exc))


def download_youtube_video(video: VideoCandidate, video_folder: Path) -> dict:
    """Download one YouTube video at the highest resolution available.

    yt-dlp already extracts the formats from every enabled player client and
    picks the single highest-resolution one in one pass, so we just run that and
    retry the whole download a few times: YouTube can throttle Colab's datacenter
    IP mid-download and hand us a lower format than it advertised. We keep the
    best file across attempts and pause between tries so the rate limit cools
    down. Reports the best resolution YouTube exposed vs. what we actually got.
    """
    video_folder.mkdir(parents=True, exist_ok=True)
    status = "failed"
    error_message = ""
    video_filename = "video.mp4"
    video_path = video_folder / video_filename
    file_size = ""

    try:
        import yt_dlp
    except ImportError:
        return {
            "video_filename": video_filename,
            "video_path": str(video_path),
            "video_file_size": file_size,
            "download_status": "failed",
            "error_message": "yt-dlp is not installed. Run: pip install yt-dlp",
            "available_height": None,
            "downloaded_height": None,
        }

    logger = _QuietLogger()
    attempt_dir = video_folder / "_attempt"
    attempt_dir.mkdir(parents=True, exist_ok=True)

    true_max = 0          # best resolution YouTube exposed (the real max)
    best_height = 0       # best resolution we actually downloaded
    best_file: Path | None = None

    for attempt in range(MAX_DOWNLOAD_ATTEMPTS):
        cand, cand_height, available, err = _attempt_download(
            yt_dlp, video.url, attempt_dir, logger
        )
        if err:
            error_message = err
        if available > true_max:
            true_max = available
        if cand and cand_height > best_height:
            # Promote this better file to the real folder, replacing the old one.
            for old in video_folder.glob("video.*"):
                if old.suffix.lower() != ".jpg":
                    old.unlink(missing_ok=True)
            dest = video_folder / cand.name
            cand.replace(dest)
            best_file, best_height = dest, cand_height

        if best_height >= 2160:
            break  # 4K — nothing higher to chase
        if best_height > 0 and best_height >= true_max:
            break  # we already have the maximum YouTube exposed
        if attempt < MAX_DOWNLOAD_ATTEMPTS - 1:
            time.sleep(ATTEMPT_COOLDOWN_SECONDS)

    shutil.rmtree(attempt_dir, ignore_errors=True)

    if best_file and best_file.exists():
        status = "downloaded"
        video_path = best_file
        video_filename = best_file.name
        file_size = best_file.stat().st_size
        error_message = ""

    return {
        "video_filename": video_filename,
        "video_path": str(video_path),
        "video_file_size": file_size,
        "download_status": status,
        "error_message": _clean_error(error_message) if status != "downloaded" else "",
        "available_height": true_max or None,
        "downloaded_height": best_height or None,
    }


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def get_video_duration_seconds(video_path: Path) -> float | None:
    if not ffmpeg_available():
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True, text=True, check=True, timeout=60,
        )
        return float(result.stdout.strip())
    except Exception:
        return None


def get_video_height(video_path: Path) -> int | None:
    """Actual vertical resolution (e.g. 1080) of the downloaded file."""
    if not ffmpeg_available():
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=height",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True, text=True, check=True, timeout=60,
        )
        return int(result.stdout.strip().splitlines()[0])
    except Exception:
        return None


def create_video_screenshot(video_path: Path, screenshot_path: Path, target_second: float = 6.0) -> dict:
    """Create a screenshot, preferring second 6 or the middle for short clips."""
    if not video_path.exists() or video_path.stat().st_size == 0:
        return {"status": "failed", "path": "", "error": "Video file is missing or empty."}
    if not ffmpeg_available():
        return {"status": "skipped", "path": "", "error": "FFmpeg/ffprobe not available."}

    duration = get_video_duration_seconds(video_path)
    seek_second = max(0.0, duration / 2) if (duration is not None and duration <= target_second) else target_second

    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-ss", str(seek_second), "-i", str(video_path),
                "-frames:v", "1", "-q:v", "2", str(screenshot_path),
            ],
            capture_output=True, check=True, timeout=120,
        )
        if screenshot_path.exists() and screenshot_path.stat().st_size > 0:
            return {"status": "created", "path": str(screenshot_path), "error": ""}
        return {"status": "failed", "path": "", "error": "FFmpeg did not produce a screenshot."}
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", errors="replace") if exc.stderr else str(exc)
        return {"status": "failed", "path": "", "error": stderr.strip() or "FFmpeg screenshot failed."}
    except Exception as exc:
        return {"status": "failed", "path": "", "error": str(exc)}


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def res_label(height: int | None) -> str:
    """Human label for a vertical resolution, e.g. 1080 -> '1080p'."""
    if not height:
        return "unknown"
    if height >= 2160:
        return f"{height}p (4K)"
    return f"{height}p"


def process_community(
    gallery_url: str,
    community_name: str,
    output_root: Path,
    session: requests.Session,
    use_playwright: bool = True,
    scan_home: bool = True,
    make_screenshots: bool = True,
) -> CommunityResult:
    """Find and download all YouTube videos for one community (Gallery + Home)."""
    print(f"\nScanning gallery: {gallery_url}")
    gallery_html = fetch_html(gallery_url, session=session)
    gallery_videos = deduplicate_videos(extract_youtube_videos(gallery_html, page_source="Gallery"))

    if use_playwright and not gallery_videos and page_suggests_videos(gallery_html):
        print("Retrying with browser (Playwright) for the gallery...")
        rendered = render_with_playwright_if_needed(gallery_url)
        if rendered:
            rendered_videos = deduplicate_videos(extract_youtube_videos(rendered, page_source="Gallery"))
            if len(rendered_videos) > len(gallery_videos):
                gallery_videos = rendered_videos

    resolved_name = community_name.strip() or community_folder_name(gallery_html, gallery_url)
    home_url = get_home_url(gallery_url)

    home_videos: list[VideoCandidate] = []
    home_skipped = 0
    if scan_home:
        print(f"Scanning home page: {home_url}")
        try:
            home_videos = fetch_page_youtube_videos(home_url, "Home", session, use_playwright)
        except Exception as exc:
            print(f"Warning: could not read home page {home_url}: {exc}", file=sys.stderr)
        gallery_keys = {youtube_key(v.url) for v in gallery_videos}
        filtered = []
        for video in home_videos:
            if youtube_key(video.url) in gallery_keys:
                home_skipped += 1
                continue
            filtered.append(video)
        home_videos = filtered

    all_videos = gallery_videos + home_videos
    print(f"YouTube videos found: {len(all_videos)} (Gallery: {len(gallery_videos)}, Home: {len(home_videos)})")
    if home_skipped:
        print(f"Skipped {home_skipped} home video(s) already found in the gallery.")

    community_folder = output_root / sanitize_folder_name(resolved_name)
    community_folder.mkdir(parents=True, exist_ok=True)

    result = CommunityResult(
        community_name=resolved_name,
        gallery_url=gallery_url,
        home_url=home_url,
        output_folder=community_folder,
        videos_found=len(all_videos),
        home_skipped_duplicates=home_skipped,
    )

    for index, video in enumerate(all_videos, start=1):
        display_name = video_display_name(video)
        folder_name = f"{index:03d} - {display_name}"
        video_folder = community_folder / folder_name
        print(f"  [{index}/{len(all_videos)}] {display_name}")
        print("        Downloading...")

        download = download_youtube_video(video, video_folder)

        screenshot = {"status": "skipped", "path": "", "error": ""}
        if make_screenshots and download["download_status"] == "downloaded":
            screenshot = create_video_screenshot(
                Path(download["video_path"]), video_folder / "screenshot.jpg"
            )

        available_h = download["available_height"]
        downloaded_h = download["downloaded_height"]
        if download["download_status"] == "downloaded":
            result.videos_downloaded += 1
            if available_h and downloaded_h and downloaded_h < available_h - 1:
                print(
                    f"        ⚠ Downloaded in {res_label(downloaded_h)} "
                    f"(YouTube had {res_label(available_h)}; couldn't get the maximum)"
                )
            else:
                extra = " (the maximum on YouTube)" if available_h else ""
                print(f"        ✔ Downloaded in {res_label(downloaded_h)}{extra}")
        else:
            result.videos_failed += 1
            print(f"        ✘ Could not download: {download['error_message']}")

        result.rows.append(
            {
                "index": index,
                "source_page": video.page_source,
                "video_name": display_name,
                "video_folder": folder_name,
                "youtube_url": video.url,
                "youtube_key": youtube_key(video.url),
                "video_filename": download["video_filename"],
                "video_file_size": download["video_file_size"],
                "youtube_resolution": res_label(available_h) if available_h else "",
                "downloaded_resolution": res_label(downloaded_h) if downloaded_h else "",
                "download_status": download["download_status"],
                "error_message": download["error_message"],
                "screenshot_status": screenshot["status"],
                "screenshot_error": screenshot["error"],
            }
        )
        time.sleep(0.1)

    write_community_manifest(community_folder, result.rows)
    return result


def write_community_manifest(community_folder: Path, rows: list[dict]) -> Path:
    manifest_path = community_folder / "manifest.csv"
    fieldnames = [
        "index", "source_page", "video_name", "video_folder", "youtube_url",
        "youtube_key", "video_filename", "video_file_size",
        "youtube_resolution", "downloaded_resolution", "download_status",
        "error_message", "screenshot_status", "screenshot_error",
    ]
    with manifest_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return manifest_path


def write_batch_manifest(results: list[CommunityResult], output_root: Path) -> Path:
    manifest_path = output_root / "batch_manifest.csv"
    fieldnames = [
        "community_name", "gallery_url", "home_url", "output_folder",
        "videos_found", "videos_downloaded", "videos_failed", "home_skipped_duplicates",
    ]
    with manifest_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "community_name": result.community_name,
                    "gallery_url": result.gallery_url,
                    "home_url": result.home_url,
                    "output_folder": str(result.output_folder.resolve()),
                    "videos_found": result.videos_found,
                    "videos_downloaded": result.videos_downloaded,
                    "videos_failed": result.videos_failed,
                    "home_skipped_duplicates": result.home_skipped_duplicates,
                }
            )
    return manifest_path


def download_from_entries(
    entries: list[LinkEntry],
    output_root: Path | str = DEFAULT_DOWNLOAD_ROOT,
    use_playwright: bool = True,
    scan_home: bool = True,
    make_screenshots: bool = True,
    clean: bool = True,
) -> list[CommunityResult]:
    """High-level entry point used by both the CLI and the Colab notebook.

    When clean=True (the default) the output folder is wiped before downloading,
    so each run's zip only contains the communities from that run. This matters
    in Colab, where the folder otherwise persists between cell runs and old
    downloads pile up.
    """
    output_root = Path(output_root)
    if clean and output_root.exists():
        shutil.rmtree(output_root, ignore_errors=True)
    output_root.mkdir(parents=True, exist_ok=True)
    session = create_session()

    results: list[CommunityResult] = []
    for position, entry in enumerate(entries, start=1):
        print(f"\n=== Community {position}/{len(entries)} ===")
        try:
            result = process_community(
                gallery_url=entry.url,
                community_name=entry.community_name,
                output_root=output_root,
                session=session,
                use_playwright=use_playwright,
                scan_home=scan_home,
                make_screenshots=make_screenshots,
            )
        except Exception as exc:
            print(f"Could not process {entry.url}: {exc}", file=sys.stderr)
            result = CommunityResult(
                community_name=entry.community_name or entry.url,
                gallery_url=entry.url,
                home_url=get_home_url(entry.url),
                output_folder=output_root,
            )
        results.append(result)

    write_batch_manifest(results, output_root)
    print_summary(results, output_root)
    return results


def print_summary(results: list[CommunityResult], output_root: Path) -> None:
    total_found = sum(r.videos_found for r in results)
    total_downloaded = sum(r.videos_downloaded for r in results)
    total_failed = sum(r.videos_failed for r in results)

    print("\n=================== SUMMARY ===================")
    for result in results:
        print(f"\n{result.community_name}")
        if not result.rows:
            print("  (no YouTube videos found)")
            continue
        for row in result.rows:
            name = row["video_name"]
            if row["download_status"] == "downloaded":
                yt = row["youtube_resolution"] or "?"
                got = row["downloaded_resolution"] or "?"
                low = (
                    row["downloaded_resolution"]
                    and row["youtube_resolution"]
                    and row["downloaded_resolution"] != row["youtube_resolution"]
                )
                mark = "⚠" if low else "✔"
                note = "   <- couldn't get the maximum" if low else ""
                print(f"  {mark} {name}")
                print(f"      YouTube: {yt}   |   Downloaded: {got}{note}")
            else:
                print(f"  ✘ {name}")
                print(f"      Not downloaded: {row['error_message']}")

    print("\n----------------------------------------------")
    print(f"Communities processed: {len(results)}")
    print(f"Videos found:          {total_found}")
    print(f"Downloaded:            {total_downloaded}")
    print(f"With problems:         {total_failed}")
    print(f"Output folder:         {output_root.resolve()}")
    print("==============================================")


# --------------------------------------------------------------------------- #
# Links parsing helpers
# --------------------------------------------------------------------------- #
def parse_links_line(line: str) -> LinkEntry | None:
    """Parse one line: 'URL' or 'URL | Community Name'."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if "|" in stripped:
        url_part, name_part = stripped.split("|", 1)
        return LinkEntry(url=url_part.strip(), community_name=name_part.strip())
    return LinkEntry(url=stripped, community_name="")


def read_links_file(links_path: Path) -> list[LinkEntry]:
    if not links_path.exists():
        raise FileNotFoundError(f"Links file not found: {links_path}")
    entries: list[LinkEntry] = []
    with links_path.open("r", encoding="utf-8") as file:
        for line in file:
            entry = parse_links_line(line)
            if entry and entry.url:
                entries.append(entry)
    return entries


def parse_urls_text(text: str) -> list[LinkEntry]:
    """Parse a multi-line block of 'URL' or 'URL | Name' lines (for Colab)."""
    entries: list[LinkEntry] = []
    for line in text.splitlines():
        entry = parse_links_line(line)
        if entry and entry.url:
            entries.append(entry)
    return entries


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download ONLY the YouTube videos from senior-living community Gallery + Home pages."
    )
    parser.add_argument("url", nargs="?", help="Single gallery page URL to process.")
    parser.add_argument("--links", help="Links file (one 'URL | Community Name' per line).")
    parser.add_argument("--out", default=DEFAULT_DOWNLOAD_ROOT, help=f"Output directory (default: {DEFAULT_DOWNLOAD_ROOT}).")
    parser.add_argument("--no-home", action="store_true", help="Do not scan the Home page, only the given URL.")
    parser.add_argument("--no-playwright", action="store_true", help="Disable Playwright fallback rendering.")
    parser.add_argument("--no-screenshots", action="store_true", help="Do not create video screenshots.")
    parser.add_argument("--no-clean", action="store_true", help="Keep existing files in the output folder (do not wipe it first).")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.links:
        if args.url:
            print("Use either a single URL or --links, not both.", file=sys.stderr)
            return 1
        try:
            entries = read_links_file(Path(args.links))
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 1
    elif args.url:
        entries = [LinkEntry(url=args.url, community_name="")]
    else:
        print("Provide a gallery URL or use --links links.txt", file=sys.stderr)
        return 1

    if not entries:
        print("No valid URLs to process.", file=sys.stderr)
        return 1

    results = download_from_entries(
        entries,
        output_root=args.out,
        use_playwright=not args.no_playwright,
        scan_home=not args.no_home,
        make_screenshots=not args.no_screenshots,
        clean=not args.no_clean,
    )
    return 0 if all(r.videos_failed == 0 for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
