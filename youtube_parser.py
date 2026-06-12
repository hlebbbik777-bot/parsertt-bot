"""YouTube comment parser."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from youtube_comment_downloader import YoutubeCommentDownloader

from comments_common import ParseError, build_result, make_comment, youtube_urls

YOUTUBE_RE = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com/(?:watch\?[^\s]*v=|shorts/|live/)|youtu\.be/)([A-Za-z0-9_-]{6,})",
    re.I,
)


def resolve_youtube_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url.strip())
    if "youtu.be" in parsed.netloc:
        video_id = parsed.path.strip("/").split("/")[0]
    else:
        video_id = parse_qs(parsed.query).get("v", [None])[0]
        if not video_id:
            match = re.search(r"/(?:shorts|live)/([A-Za-z0-9_-]{6,})", parsed.path)
            video_id = match.group(1) if match else None
    if not video_id:
        raise ParseError("Не удалось определить ID YouTube-видео")
    canonical = f"https://www.youtube.com/watch?v={video_id}"
    return video_id, canonical


def _parse_likes(value: Any) -> int:
    if isinstance(value, int):
        return value
    if not value:
        return 0
    text = str(value).replace("\xa0", " ").replace(",", "").replace(" ", "")
    multipliers = {"K": 1_000, "M": 1_000_000, "тыс": 1_000, "млн": 1_000_000}
    for suffix, mult in multipliers.items():
        if suffix.lower() in text.lower():
            num = re.sub(r"[^\d.]", "", text)
            return int(float(num or 0) * mult)
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else 0


def fetch_youtube_comments(url: str, *, progress_cb=None) -> dict[str, Any]:
    video_id, canonical = resolve_youtube_url(url)
    downloader = YoutubeCommentDownloader()
    comments: list[dict[str, Any]] = []
    seen: set[str] = set()

    try:
        for idx, item in enumerate(downloader.get_comments_from_url(canonical)):
            cid = item.get("cid")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            author = (item.get("author") or "").lstrip("@") or "unknown"
            ts = item.get("time_parsed")
            date = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else item.get("time", "")
            cid = str(cid)
            urls = youtube_urls(
                video_url=canonical,
                video_id=video_id,
                author=author,
                channel=str(item.get("channel") or ""),
                comment_id=cid,
            )
            comments.append(
                make_comment(
                    platform="youtube",
                    comment_id=cid,
                    username=author,
                    text=item.get("text", ""),
                    likes=_parse_likes(item.get("votes")),
                    date=date,
                    is_reply=bool(item.get("reply")),
                    source_url=canonical,
                    **urls,
                )
            )
            if progress_cb and idx % 50 == 0:
                progress_cb(page=idx // 50 + 1, collected=len(comments), reported_total=None)
    except Exception as exc:
        raise ParseError(f"YouTube: не удалось получить комментарии — {exc}") from exc

    if not comments:
        raise ParseError("YouTube: комментарии не найдены или отключены на видео")

    return build_result(
        platform="youtube",
        source_id=video_id,
        source_url=canonical,
        comments=comments,
        reported_total=len(comments),
    )
