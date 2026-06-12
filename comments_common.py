"""Shared comment models and CSV export."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any


class ParseError(Exception):
    pass


CSV_FIELDS = [
    "platform",
    "username",
    "text",
    "likes",
    "date",
    "is_reply",
    "comment_id",
    "video_url",
    "comment_url",
    "profile_url",
]


def tiktok_urls(*, video_url: str, username: str, comment_id: str) -> dict[str, str]:
    handle = username.lstrip("@")
    return {
        "video_url": video_url,
        "profile_url": f"https://www.tiktok.com/@{handle}",
        "comment_url": f"{video_url}?comment_id={comment_id}",
    }


def youtube_urls(*, video_url: str, video_id: str, author: str, channel: str, comment_id: str) -> dict[str, str]:
    author = author.lstrip("@") or "unknown"
    if channel and channel.startswith("UC"):
        profile_url = f"https://www.youtube.com/channel/{channel}"
    else:
        profile_url = f"https://www.youtube.com/@{author}"
    return {
        "video_url": video_url,
        "profile_url": profile_url,
        "comment_url": f"https://www.youtube.com/watch?v={video_id}&lc={comment_id}",
    }


def vk_urls(*, video_url: str, username: str, comment_id: str) -> dict[str, str]:
    handle = username.lstrip("@")
    return {
        "video_url": video_url,
        "profile_url": f"https://vk.com/{handle}",
        "comment_url": f"{video_url}?reply={comment_id}",
    }


def make_comment(
    *,
    platform: str,
    comment_id: str,
    username: str,
    text: str,
    likes: int = 0,
    date: str | None = None,
    is_reply: bool = False,
    source_url: str = "",
    video_url: str = "",
    comment_url: str = "",
    profile_url: str = "",
) -> dict[str, Any]:
    video = video_url or source_url
    return {
        "platform": platform,
        "comment_id": comment_id,
        "username": username,
        "text": text or "",
        "likes": likes,
        "date": date or "",
        "is_reply": is_reply,
        "source_url": source_url or video,
        "video_url": video,
        "comment_url": comment_url,
        "profile_url": profile_url,
    }


def to_csv_bytes(comments: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for comment in comments:
        writer.writerow({key: comment.get(key, "") for key in CSV_FIELDS})
    return buf.getvalue().encode("utf-8-sig")


def build_result(
    *,
    platform: str,
    source_id: str,
    source_url: str,
    comments: list[dict[str, Any]],
    reported_total: int | None = None,
) -> dict[str, Any]:
    top_level = sum(1 for c in comments if not c.get("is_reply"))
    return {
        "platform": platform,
        "source_id": source_id,
        "source_url": source_url,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "reported_total": reported_total,
        "total_comments": len(comments),
        "top_level_comments": top_level,
        "replies": len(comments) - top_level,
        "comments": comments,
    }
