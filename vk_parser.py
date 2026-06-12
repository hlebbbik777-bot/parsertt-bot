"""VK comment parser."""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from typing import Any

import requests

from comments_common import ParseError, build_result, make_comment, vk_urls

VK_VIDEO_RE = re.compile(r"video(-?\d+)_(\d+)", re.I)
VK_WALL_RE = re.compile(r"wall(-?\d+)_(\d+)", re.I)
VK_CLIP_RE = re.compile(r"clip(-?\d+)_(\d+)", re.I)
DEFAULT_VK_APP_ID = "6287487"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}


def _get_token(session: requests.Session) -> str:
    token = os.environ.get("VK_ACCESS_TOKEN", "").strip()
    if token.startswith("anonym."):
        return token

    app_id = os.environ.get("VK_APP_ID", DEFAULT_VK_APP_ID).strip() or DEFAULT_VK_APP_ID
    data: dict[str, str] = {"client_id": app_id}
    if token:
        data["client_secret"] = token

    resp = session.post(
        "https://api.vk.com/oauth/get_anonym_token",
        data=data,
        timeout=20,
    )
    payload = resp.json()
    if "token" not in payload:
        msg = payload.get("error_description") or payload.get("error") or payload
        raise ParseError(f"VK: не удалось получить токен ({msg})")
    return payload["token"]


def resolve_vk_target(url: str, session: requests.Session) -> tuple[str, dict[str, Any], str]:
    resp = session.get(url, allow_redirects=True, timeout=20)
    resp.raise_for_status()
    final_url = resp.url.split("?")[0]

    for regex, kind in ((VK_VIDEO_RE, "video"), (VK_CLIP_RE, "video"), (VK_WALL_RE, "wall")):
        match = regex.search(final_url)
        if match:
            owner_id, item_id = match.groups()
            return kind, {"owner_id": int(owner_id), "item_id": int(item_id)}, final_url

    raise ParseError("Не удалось определить VK-пост или видео из ссылки")


def _fetch_page(session: requests.Session, token: str, kind: str, params: dict[str, Any]) -> dict[str, Any]:
    method = "video.getComments" if kind == "video" else "wall.getComments"
    base = {"access_token": token, "v": "5.199", "extended": 1, "fields": "domain", "count": 100}
    if kind == "video":
        base.update({"owner_id": params["owner_id"], "video_id": params["item_id"]})
    else:
        base.update({"owner_id": params["owner_id"], "post_id": params["item_id"]})

    resp = session.get(f"https://api.vk.com/method/{method}", params=base, timeout=30)
    payload = resp.json()
    if "error" in payload:
        raise ParseError(f"VK API: {payload['error'].get('error_msg', payload['error'])}")
    return payload["response"]


def fetch_vk_comments(url: str, *, progress_cb=None) -> dict[str, Any]:
    session = requests.Session()
    session.headers.update(HEADERS)
    token = _get_token(session)

    kind, target, canonical = resolve_vk_target(url, session)
    response = _fetch_page(session, token, kind, target)

    raw_items = response.get("items") or []
    profiles = {p["id"]: p.get("domain") or f"id{p['id']}" for p in response.get("profiles") or []}
    groups = {-(p["id"]): (p.get("screen_name") or f"club{p['id']}") for p in response.get("groups") or []}
    names = {**profiles, **groups}

    comments: list[dict[str, Any]] = []
    offset = len(raw_items)
    total = response.get("count", len(raw_items))

    def append_items(items: list[dict[str, Any]]) -> None:
        for item in items:
            from_id = item.get("from_id")
            username = names.get(from_id, f"id{from_id}" if from_id else "unknown")
            ts = item.get("date")
            date = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else ""
            parent = item.get("reply_to_comment") or item.get("parent_comment")
            cid = str(item.get("id"))
            urls = vk_urls(video_url=canonical, username=str(username), comment_id=cid)
            comments.append(
                make_comment(
                    platform="vk",
                    comment_id=cid,
                    username=str(username),
                    text=item.get("text", ""),
                    likes=(item.get("likes") or {}).get("count", 0),
                    date=date,
                    is_reply=bool(parent),
                    source_url=canonical,
                    **urls,
                )
            )

    append_items(raw_items)

    while offset < total:
        time.sleep(0.35)
        method = "video.getComments" if kind == "video" else "wall.getComments"
        params = {
            "access_token": token,
            "v": "5.199",
            "extended": 1,
            "fields": "domain",
            "count": 100,
            "offset": offset,
            "owner_id": target["owner_id"],
        }
        if kind == "video":
            params["video_id"] = target["item_id"]
        else:
            params["post_id"] = target["item_id"]

        resp = session.get(f"https://api.vk.com/method/{method}", params=params, timeout=30)
        payload = resp.json()
        if "error" in payload:
            break
        batch = payload["response"].get("items") or []
        if not batch:
            break
        append_items(batch)
        offset += len(batch)
        if progress_cb:
            progress_cb(page=offset // 100 + 1, collected=len(comments), reported_total=total)

    if not comments:
        raise ParseError("VK: комментарии не найдены")

    source_id = f"{target['owner_id']}_{target['item_id']}"
    return build_result(
        platform="vk",
        source_id=source_id,
        source_url=canonical,
        comments=comments,
        reported_total=total,
    )
