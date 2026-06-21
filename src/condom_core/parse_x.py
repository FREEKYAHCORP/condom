from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from typing import Any

from .models import Item


STATUS_RE = re.compile(r"/([^/\s]+)/status/(\d+)")


def stable_hash(*parts: str | None) -> str:
    raw = "\n".join(p or "" for p in parts)
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:24]


def iter_dicts(obj: Any) -> Iterator[dict[str, Any]]:
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from iter_dicts(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from iter_dicts(value)


def unwrap_tweet(obj: dict[str, Any]) -> dict[str, Any] | None:
    if obj.get("__typename") in {"Tweet", "TweetWithVisibilityResults"}:
        if obj.get("__typename") == "TweetWithVisibilityResults":
            return obj.get("tweet")
        return obj
    if "tweet_results" in obj:
        result = obj.get("tweet_results", {}).get("result")
        if isinstance(result, dict):
            return unwrap_tweet(result)
    if "tweet" in obj and isinstance(obj["tweet"], dict):
        return unwrap_tweet(obj["tweet"])
    return None


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _normalized_user_fields(user_result: Any) -> dict[str, Any]:
    user_result = _dict_or_empty(user_result)
    core = _dict_or_empty(user_result.get("core"))
    legacy = _dict_or_empty(user_result.get("legacy"))
    profile_bio = _dict_or_empty(user_result.get("profile_bio"))
    return {
        "core": core,
        "legacy": legacy,
        "handle": core.get("screen_name") or legacy.get("screen_name"),
        "name": core.get("name") or legacy.get("name"),
        "bio": legacy.get("description") or profile_bio.get("description"),
    }

def parse_tweet(tweet: dict[str, Any], session_id: str, source: str) -> Item | None:
    legacy = tweet.get("legacy") or {}
    if not isinstance(legacy, dict):
        legacy = {}
    item_id = str(tweet.get("rest_id") or legacy.get("id_str") or "")

    user_result = (
        tweet.get("core", {})
        .get("user_results", {})
        .get("result", {})
    )
    user_fields = _normalized_user_fields(user_result)

    text = legacy.get("full_text") or legacy.get("text")
    quoted_text = None
    quoted = tweet.get("quoted_status_result", {}).get("result")
    if isinstance(quoted, dict):
        quoted_tweet = unwrap_tweet(quoted)
        if quoted_tweet:
            quoted_text = (quoted_tweet.get("legacy") or {}).get("full_text")

    urls = (((legacy.get("entities") or {}).get("urls")) or [])
    link_url = link_title = link_excerpt = None
    if urls:
        first_url = urls[0] or {}
        link_url = first_url.get("expanded_url") or first_url.get("url")
        link_title = first_url.get("display_url")

    media_entities = (((legacy.get("extended_entities") or {}).get("media")) or [])
    if not media_entities:
        media_entities = (((legacy.get("entities") or {}).get("media")) or [])
    media_desc = None
    if media_entities:
        kinds = sorted({m.get("type", "media") for m in media_entities if isinstance(m, dict)})
        media_desc = ", ".join(kinds)

    author_handle = user_fields["handle"]
    if not item_id:
        item_id = stable_hash(author_handle, text, quoted_text, link_url)
    if not item_id or not text:
        return None

    engagement = {
        "favorite_count": legacy.get("favorite_count"),
        "retweet_count": legacy.get("retweet_count"),
        "reply_count": legacy.get("reply_count"),
        "quote_count": legacy.get("quote_count"),
        "bookmarked": legacy.get("bookmarked"),
        "favorited": legacy.get("favorited"),
    }
    return Item(
        item_id=item_id,
        source=source,
        session_id=session_id,
        author_handle=author_handle,
        author_name=user_fields["name"],
        author_bio=user_fields["bio"],
        text=text,
        quoted_text=quoted_text,
        media_desc=media_desc,
        link_url=link_url,
        link_title=link_title,
        link_excerpt=link_excerpt,
        engagement=engagement,
        raw_json=tweet,
    )


def parse_response(body: dict[str, Any], session_id: str = "", source: str = "x_for_you") -> list[Item]:
    seen: set[str] = set()
    items: list[Item] = []
    for node in iter_dicts(body):
        tweet = unwrap_tweet(node)
        if not tweet:
            continue
        item = parse_tweet(tweet, session_id=session_id, source=source)
        if item and item.item_id not in seen:
            seen.add(item.item_id)
            items.append(item)
    return items

def parse_response_ordered(
    body: dict[str, Any],
    session_id: str = "",
    source: str = "x_for_you",
) -> list[tuple[Item, int]]:
    """Parse GraphQL body preserving first-seen tweet order with 1-based response ranks."""
    seen: set[str] = set()
    ordered: list[tuple[Item, int]] = []
    response_rank = 0
    for node in iter_dicts(body):
        tweet = unwrap_tweet(node)
        if not tweet:
            continue
        item = parse_tweet(tweet, session_id=session_id, source=source)
        if item and item.item_id not in seen:
            seen.add(item.item_id)
            response_rank += 1
            ordered.append((item, response_rank))
    return ordered



def item_from_dom(row: dict[str, Any], session_id: str) -> Item | None:
    item_id = str(row.get("item_id") or "")
    text = row.get("text")
    handle = row.get("author_handle")
    if not item_id:
        url = row.get("url") or ""
        match = STATUS_RE.search(url)
        if match:
            handle = handle or match.group(1)
            item_id = match.group(2)
    if not item_id or not text:
        return None
    return Item(
        item_id=item_id,
        source="x_for_you_dom",
        session_id=session_id,
        author_handle=handle,
        text=text,
        raw_json=row,
    )
