"""Detect social platform from URL and fetch comments."""

from __future__ import annotations

import re
from typing import Callable
from urllib.parse import urlparse

from comments_common import ParseError, build_result

URL_PATTERNS: list[tuple[str, re.Pattern[str], Callable]] = []


def _register(platform: str, pattern: str, fetcher: Callable) -> None:
    URL_PATTERNS.append((platform, re.compile(pattern, re.I), fetcher))


def detect_url(text: str) -> tuple[str, str] | None:
    for platform, pattern, _fetcher in URL_PATTERNS:
        match = pattern.search(text)
        if match:
            return platform, match.group(0).rstrip(".,)")
    return None


def fetch_comments(url: str, *, progress_cb=None) -> dict:
    for platform, pattern, fetcher in URL_PATTERNS:
        if pattern.search(url):
            return fetcher(url, progress_cb=progress_cb)
    raise ParseError(
        "Неизвестная ссылка. Поддерживаются: TikTok, YouTube, VK."
    )


def register_parsers() -> None:
    from tiktok_parser import fetch_tiktok_comments
    from vk_parser import fetch_vk_comments
    from youtube_parser import fetch_youtube_comments

    _register("tiktok", r"https?://(?:www\.)?(?:vm|vt|m)\.tiktok\.com/\S+|https?://(?:www\.)?tiktok\.com/\S+", fetch_tiktok_comments)
    _register("youtube", r"https?://(?:www\.)?(?:youtube\.com/\S+|youtu\.be/\S+)", fetch_youtube_comments)
    _register("vk", r"https?://(?:www\.)?(?:vk\.com|vk\.ru|m\.vk\.com|m\.vk\.ru)/(?:video|wall|clip|clips)[^\s]+", fetch_vk_comments)


register_parsers()
