"""TikTok comment parser via public web API."""

from __future__ import annotations

import csv
import io
import json
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import requests

VIDEO_ID_RE = re.compile(r"/video/(\d+)")
TIKTOK_HOSTS = ("tiktok.com", "www.tiktok.com", "vm.tiktok.com", "vt.tiktok.com")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


class TikTokParseError(Exception):
    pass


def resolve_video_id(url: str, session: requests.Session | None = None) -> tuple[str, str]:
    """Return (video_id, canonical_url) from any TikTok link."""
    session = session or requests.Session()
    session.headers.update(HEADERS)

    parsed = urlparse(url.strip())
    if not parsed.scheme:
        url = "https://" + url.strip()
        parsed = urlparse(url)

    host = parsed.netloc.lower().removeprefix("www.")
    if not any(host == h.removeprefix("www.") for h in TIKTOK_HOSTS):
        raise TikTokParseError("Это не ссылка на TikTok")

    match = VIDEO_ID_RE.search(parsed.path)
    if match:
        video_id = match.group(1)
        return video_id, url.split("?")[0]

    try:
        resp = session.get(url, allow_redirects=True, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise TikTokParseError(f"Не удалось открыть ссылку: {exc}") from exc

    final_url = resp.url.split("?")[0]
    match = VIDEO_ID_RE.search(final_url)
    if not match:
        raise TikTokParseError("Не удалось определить ID видео из ссылки")

    return match.group(1), final_url


def _parse_comment(comment: dict[str, Any], *, parent_cid: str | None = None, is_reply: bool = False) -> dict[str, Any]:
    user = comment.get("user") or {}
    ts = comment.get("create_time")
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None
    return {
        "cid": comment.get("cid"),
        "parent_cid": parent_cid,
        "is_reply": is_reply,
        "text": comment.get("text", ""),
        "username": user.get("unique_id") or user.get("nickname"),
        "nickname": user.get("nickname"),
        "likes": comment.get("digg_count", 0),
        "reply_count": comment.get("reply_comment_total", 0),
        "create_time": ts,
        "create_time_iso": dt,
        "language": comment.get("comment_language"),
        "author_liked": comment.get("is_author_digged", False),
        "pinned": comment.get("author_pin", False),
    }


def fetch_all_comments(video_id: str, video_url: str, *, progress_cb=None) -> dict[str, Any]:
    session = requests.Session()
    session.headers.update({**HEADERS, "Referer": video_url})

    def fetch_page(cursor: int = 0, count: int = 50) -> dict[str, Any]:
        params = {
            "aid": "1988",
            "aweme_id": video_id,
            "count": count,
            "cursor": cursor,
            "device_platform": "webapp",
            "webcast_language": "ru-RU",
        }
        resp = session.get("https://www.tiktok.com/api/comment/list/", params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def fetch_replies(comment_id: str, cursor: int = 0, count: int = 50) -> dict[str, Any]:
        params = {
            "aid": "1988",
            "aweme_id": video_id,
            "comment_id": comment_id,
            "count": count,
            "cursor": cursor,
            "device_platform": "webapp",
            "item_type": "0",
        }
        resp = session.get("https://www.tiktok.com/api/comment/list/reply/", params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    all_comments: list[dict[str, Any]] = []
    seen_cids: set[str] = set()
    cursor = 0
    page = 0
    reported_total = None

    while True:
        page += 1
        data = fetch_page(cursor=cursor)
        comments = data.get("comments") or []
        reported_total = data.get("total", reported_total)
        has_more = data.get("has_more", 0)
        cursor = data.get("cursor", 0)

        if progress_cb:
            progress_cb(page=page, collected=len(all_comments), reported_total=reported_total)

        for comment in comments:
            cid = comment.get("cid")
            if not cid or cid in seen_cids:
                continue
            seen_cids.add(cid)
            all_comments.append(_parse_comment(comment))

            for reply in comment.get("reply_comment") or []:
                rcid = reply.get("cid")
                if rcid and rcid not in seen_cids:
                    seen_cids.add(rcid)
                    all_comments.append(_parse_comment(reply, parent_cid=cid, is_reply=True))

            reply_total = comment.get("reply_comment_total", 0)
            inline_count = len(comment.get("reply_comment") or [])
            if reply_total > inline_count:
                reply_cursor = inline_count
                while True:
                    time.sleep(0.25)
                    reply_data = fetch_replies(cid, cursor=reply_cursor)
                    replies = reply_data.get("comments") or []
                    if not replies:
                        break
                    for reply in replies:
                        rcid = reply.get("cid")
                        if rcid and rcid not in seen_cids:
                            seen_cids.add(rcid)
                            all_comments.append(_parse_comment(reply, parent_cid=cid, is_reply=True))
                    if not reply_data.get("has_more"):
                        break
                    reply_cursor = reply_data.get("cursor", reply_cursor + len(replies))

        if not has_more or not comments:
            break
        time.sleep(0.4)

    top_level = sum(1 for c in all_comments if not c["is_reply"])
    replies = len(all_comments) - top_level

    return {
        "video_id": video_id,
        "video_url": video_url,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "reported_total": reported_total,
        "total_comments": len(all_comments),
        "top_level_comments": top_level,
        "replies": replies,
        "comments": all_comments,
    }


def to_json_bytes(data: dict[str, Any]) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def to_csv_bytes(comments: list[dict[str, Any]]) -> bytes:
    fields = [
        "cid",
        "parent_cid",
        "is_reply",
        "username",
        "nickname",
        "text",
        "likes",
        "reply_count",
        "create_time_iso",
        "language",
        "author_liked",
        "pinned",
    ]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields)
    writer.writeheader()
    for comment in comments:
        writer.writerow({key: comment.get(key) for key in fields})
    return buf.getvalue().encode("utf-8")
