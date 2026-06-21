from __future__ import annotations

from .models import Item


def _clean(value: str | None) -> str | None:
    if not value:
        return None
    return " ".join(str(value).split())


def render_timeline(item: Item) -> str:
    lines: list[str] = []
    handle = _clean(item.author_handle)
    name = _clean(item.author_name)
    if handle:
        lines.append(f"@{handle}")
    elif name:
        lines.append(name)
    text = _clean(item.text)
    if text:
        lines.append(text)
    media = _clean(item.media_desc)
    if media:
        lines.append(f"[image: {media}]")
    quoted = _clean(item.quoted_text)
    if quoted:
        lines.append(f"quoting: {quoted}")
    title = _clean(item.link_title)
    excerpt = _clean(item.link_excerpt)
    if title or excerpt:
        link_text = " - ".join(p for p in (title, excerpt) if p)
        lines.append(f"<link: {link_text}>")
    lines.append(f"<{item.item_id}>")
    return "\n".join(lines)
