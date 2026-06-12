"""TikTok comment parser via public web API."""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from comments_common import ParseError, build_result, make_comment, tiktok_urls

logger = logging.getLogger(__name__)

VIDEO_ID_RE = re.compile(r"/video/(\d+)")
TIKTOK_HOSTS = ("tiktok.com", "www.tiktok.com", "vm.tiktok.com", "vt.tiktok.com", "m.tiktok.com")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


def _build_session(referer: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({**HEADERS, "Referer": referer})
    retry = Retry(total=3, backoff_factor=0.6, status_forcelist=(429, 500, 502, 503, 504))
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _request_json(session: requests.Session, url: str, params: dict[str, Any]) -> dict[str, Any]:
    last_exc: Exception | None = None
    for attempt in range(4):
        try:
            resp = session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            time.sleep(0.8 * (attempt + 1))
    raise ParseError(f"TikTok: ошибка сети — {last_exc}")


def resolve_video_id(url: str, session: requests.Session | None = None) -> tuple[str, str]:
    session = session or requests.Session()
    session.headers.update(HEADERS)

    parsed = urlparse(url.strip())
    if not parsed.scheme:
        url = "https://" + url.strip()
        parsed = urlparse(url)

    host = parsed.netloc.lower().removeprefix("www.")
    if not any(host == h.removeprefix("www.") for h in TIKTOK_HOSTS):
        raise ParseError("Это не ссылка на TikTok")

    match = VIDEO_ID_RE.search(parsed.path)
    if match:
        return match.group(1), url.split("?")[0]

    try:
        resp = session.get(url, allow_redirects=True, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise ParseError(f"Не удалось открыть ссылку: {exc}") from exc

    final_url = resp.url.split("?")[0]
    match = VIDEO_ID_RE.search(final_url)
    if not match:
        raise ParseError("Не удалось определить ID видео из ссылки")

    return match.group(1), final_url


def _parse_comment(comment: dict[str, Any], *, source_url: str, parent_cid: str | None = None, is_reply: bool = False) -> dict[str, Any]:
    user = comment.get("user") or {}
    ts = comment.get("create_time")
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else ""
    username = user.get("unique_id") or user.get("nickname") or "unknown"
    cid = str(comment.get("cid"))
    urls = tiktok_urls(video_url=source_url, username=username, comment_id=cid)
    return make_comment(
        platform="tiktok",
        comment_id=cid,
        username=username,
        text=comment.get("text", ""),
        likes=comment.get("digg_count", 0),
        date=dt,
        is_reply=is_reply,
        source_url=source_url,
        **urls,
    )


def fetch_tiktok_comments(url: str, *, progress_cb=None) -> dict[str, Any]:
    video_id, video_url = resolve_video_id(url)
    session = _build_session(video_url)

    def fetch_page(cursor: int = 0, count: int = 50) -> dict[str, Any]:
        return _request_json(
            session,
            "https://www.tiktok.com/api/comment/list/",
            {
                "aid": "1988",
                "aweme_id": video_id,
                "count": count,
                "cursor": cursor,
                "device_platform": "webapp",
                "webcast_language": "ru-RU",
            },
        )

    def fetch_replies(comment_id: str, cursor: int = 0, count: int = 50) -> dict[str, Any]:
        return _request_json(
            session,
            "https://www.tiktok.com/api/comment/list/reply/",
            {
                "aid": "1988",
                "aweme_id": video_id,
                "comment_id": comment_id,
                "count": count,
                "cursor": cursor,
                "device_platform": "webapp",
                "item_type": "0",
            },
        )

    def load_extra_replies(cid: str, inline_count: int, reply_total: int) -> None:
        reply_cursor = inline_count
        failures = 0
        while reply_cursor < reply_total:
            time.sleep(0.35)
            try:
                reply_data = fetch_replies(str(cid), cursor=reply_cursor)
            except (ParseError, requests.RequestException) as exc:
                failures += 1
                logger.warning("TikTok replies skipped for %s: %s", cid, exc)
                if failures >= 2:
                    return
                continue
            replies = reply_data.get("comments") or []
            if not replies:
                return
            for reply in replies:
                rcid = reply.get("cid")
                if rcid and str(rcid) not in seen_cids:
                    seen_cids.add(str(rcid))
                    all_comments.append(
                        _parse_comment(reply, source_url=video_url, parent_cid=str(cid), is_reply=True)
                    )
            if not reply_data.get("has_more"):
                return
            reply_cursor = reply_data.get("cursor", reply_cursor + len(replies))
            failures = 0

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
            seen_cids.add(str(cid))
            all_comments.append(_parse_comment(comment, source_url=video_url))

            for reply in comment.get("reply_comment") or []:
                rcid = reply.get("cid")
                if rcid and str(rcid) not in seen_cids:
                    seen_cids.add(str(rcid))
                    all_comments.append(_parse_comment(reply, source_url=video_url, parent_cid=str(cid), is_reply=True))

            reply_total = comment.get("reply_comment_total", 0)
            inline_count = len(comment.get("reply_comment") or [])
            if reply_total > inline_count:
                load_extra_replies(str(cid), inline_count, reply_total)

        if not has_more or not comments:
            break
        time.sleep(0.4)

    if not all_comments:
        raise ParseError("TikTok: комментарии не найдены")

    return build_result(
        platform="tiktok",
        source_id=video_id,
        source_url=video_url,
        comments=all_comments,
        reported_total=reported_total,
    )
