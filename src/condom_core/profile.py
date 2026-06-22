from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from condom_core.config import PROFILE, ensure_dirs
from condom_core.db import connect

DEFAULT_POSITIVE_PROFILE = (
    "machine learning\n"
    "agents\n"
    "AI infrastructure\n"
    "benchmarks\n"
    "evaluations\n"
    "open source\n"
)

PROFILE_FIELD_NAMES = (
    "state_preamble",
    "identity_revealed",
    "identity_endorsed",
    "positive_profile",
    "negative_profile",
)

DEFAULT_STATE_PREAMBLE = "ordinary scroll session. a few minutes to look around."

DEFAULT_PROFILE_FIELDS: dict[str, str] = {
    "state_preamble": DEFAULT_STATE_PREAMBLE,
    "identity_revealed": "",
    "identity_endorsed": "",
    "positive_profile": DEFAULT_POSITIVE_PROFILE,
    "negative_profile": "",
}


def user_profile_json_path() -> str:
    return str(PROFILE / "user_profile.json")


def positive_profile_path() -> str:
    return str(PROFILE / "positive_profile.txt")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_profile_version_id() -> str:
    return f"pv_{uuid.uuid4().hex[:12]}"


def _normalize_field_text(value: Any, *, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    return value


def _default_version(*, source: str = "default") -> dict[str, Any]:
    vid = _new_profile_version_id()
    return {
        "profile_version_id": vid,
        "created_at": _utc_now_iso(),
        "source": source,
        **DEFAULT_PROFILE_FIELDS,
    }


def _empty_store() -> dict[str, Any]:
    version = _default_version(source="bootstrap")
    return {
        "active_version_id": version["profile_version_id"],
        "versions": [version],
    }


def _version_by_id(store: dict[str, Any], version_id: str) -> dict[str, Any] | None:
    for entry in store.get("versions") or []:
        if entry.get("profile_version_id") == version_id:
            return entry
    return None


def _coerce_store(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return _empty_store()
    versions = raw.get("versions")
    if not isinstance(versions, list) or not versions:
        return _empty_store()
    cleaned: list[dict[str, Any]] = []
    for entry in versions:
        if not isinstance(entry, dict):
            continue
        vid = entry.get("profile_version_id")
        if not isinstance(vid, str) or not vid:
            continue
        row: dict[str, Any] = {
            "profile_version_id": vid,
            "created_at": str(entry.get("created_at") or _utc_now_iso()),
            "source": str(entry.get("source") or "unknown"),
        }
        for field in PROFILE_FIELD_NAMES:
            raw_val = entry.get(field)
            if raw_val is None:
                row[field] = DEFAULT_PROFILE_FIELDS[field]
            else:
                row[field] = _normalize_field_text(raw_val, field=field)
        cleaned.append(row)
    if not cleaned:
        return _empty_store()
    active = raw.get("active_version_id")
    if not isinstance(active, str) or _version_by_id({"versions": cleaned}, active) is None:
        active = cleaned[-1]["profile_version_id"]
    return {"active_version_id": active, "versions": cleaned}


def load_user_profile_store(*, create_if_missing: bool = True) -> dict[str, Any]:
    ensure_dirs()
    path = PROFILE / "user_profile.json"
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raw = None
        return _coerce_store(raw)
    store = _empty_store()
    if create_if_missing:
        save_user_profile_store(store)
    return store


def save_user_profile_store(store: dict[str, Any]) -> dict[str, Any]:
    ensure_dirs()
    normalized = _coerce_store(store)
    path = PROFILE / "user_profile.json"
    path.write_text(json.dumps(normalized, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return normalized


def get_active_profile_version(store: dict[str, Any] | None = None) -> dict[str, Any]:
    data = store if store is not None else load_user_profile_store()
    active_id = data["active_version_id"]
    version = _version_by_id(data, active_id)
    if version is None:
        raise ValueError(f"active_version_id {active_id!r} not found in versions")
    return version


def load_active_profile() -> dict[str, Any]:
    return get_active_profile_version()


def _active_field_values(version: dict[str, Any]) -> dict[str, str]:
    return {key: _normalize_field_text(version.get(key), field=key) for key in PROFILE_FIELD_NAMES}


def _fields_from_mapping(
    fields: dict[str, Any],
    *,
    base: dict[str, str] | None = None,
) -> dict[str, str]:
    out = dict(base if base is not None else DEFAULT_PROFILE_FIELDS)
    for key in PROFILE_FIELD_NAMES:
        if key in fields:
            out[key] = _normalize_field_text(fields[key], field=key)
    return out


def save_profile_version(
    fields: dict[str, Any],
    *,
    source: str = "user",
    activate: bool = True,
    store: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = _coerce_store(store if store is not None else load_user_profile_store(create_if_missing=False))
    active = get_active_profile_version(data)
    merged = _fields_from_mapping(fields, base=_active_field_values(active))
    version = {
        "profile_version_id": _new_profile_version_id(),
        "created_at": _utc_now_iso(),
        "source": source,
        **merged,
    }
    data["versions"] = list(data["versions"]) + [version]
    if activate:
        data["active_version_id"] = version["profile_version_id"]
    save_user_profile_store(data)
    return version


def reset_user_profile(*, source: str = "reset") -> dict[str, Any]:
    data = load_user_profile_store(create_if_missing=True)
    version = _default_version(source=source)
    data["versions"] = list(data["versions"]) + [version]
    data["active_version_id"] = version["profile_version_id"]
    save_user_profile_store(data)
    return version


def _legacy_positive_profile_text() -> str | None:
    path = PROFILE / "positive_profile.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


def load_positive_profile_text() -> str:
    store_path = PROFILE / "user_profile.json"
    if store_path.exists():
        try:
            active = get_active_profile_version(load_user_profile_store())
            text = str(active.get("positive_profile") or "").strip()
            if text:
                return str(active["positive_profile"])
        except (ValueError, TypeError, json.JSONDecodeError, OSError):
            pass
    legacy = _legacy_positive_profile_text()
    if legacy is not None:
        return legacy
    return DEFAULT_POSITIVE_PROFILE


def compile_ambient_m3_prompt_fields(profile: dict[str, Any] | None = None) -> dict[str, str]:
    if profile is None:
        profile = load_active_profile()
    fields = _fields_from_mapping(profile)
    return {
        "state_preamble": fields["state_preamble"].strip() or DEFAULT_STATE_PREAMBLE,
        "identity_revealed": fields["identity_revealed"],
        "identity_endorsed": fields["identity_endorsed"],
        "positive_profile": fields["positive_profile"],
        "negative_profile": fields["negative_profile"],
    }


def _prompt_hash(prompt: str) -> str:
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def build_profile_prompt_preview(*, candidate_items: str = "(no candidates — preview only)") -> dict[str, Any]:
    from condom_core.prompts import (
        AMBIENT_M3_ITEM_SCORE_PROMPT_VERSION,
        build_ambient_m3_item_score_prompt,
    )

    active = load_active_profile()
    fields = compile_ambient_m3_prompt_fields(active)
    prompt = build_ambient_m3_item_score_prompt(
        candidate_items,
        identity_revealed=fields["identity_revealed"],
        identity_endorsed=fields["identity_endorsed"],
        state_preamble=fields["state_preamble"],
        negative_profile=fields["negative_profile"],
    )
    return {
        "profile_version_id": active["profile_version_id"],
        "prompt_version": AMBIENT_M3_ITEM_SCORE_PROMPT_VERSION,
        "prompt_hash": _prompt_hash(prompt),
        "fields": fields,
        "prompt": prompt,
    }


def ambient_m3_request_metadata(prompt: str, profile_version_id: str) -> dict[str, Any]:
    return {
        "prompt": prompt,
        "profile_version_id": profile_version_id,
        "prompt_hash": _prompt_hash(prompt),
    }


def write_profile_from_saved_items(conn=None) -> str:
    """Rebuild positive_profile.txt from saved items, or ensure default fallback exists."""
    ensure_dirs()
    if conn is None:
        conn = connect()
    rows = conn.execute(
        """
        SELECT DISTINCT i.rendered_text
        FROM items i
        JOIN events e ON e.item_id = i.item_id AND e.session_id = i.session_id
        WHERE e.save = 1
        ORDER BY i.harvested_at
        """
    ).fetchall()
    path = PROFILE / "positive_profile.txt"
    if rows:
        path.write_text("\n\n".join(row["rendered_text"] for row in rows), encoding="utf-8")
        return f"wrote profile from {len(rows)} saved items"
    if not path.exists():
        path.write_text(DEFAULT_POSITIVE_PROFILE, encoding="utf-8")
        return "wrote default fallback profile"
    return "kept existing profile"