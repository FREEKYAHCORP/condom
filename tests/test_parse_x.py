from condom_core.parse_x import parse_response, stable_hash


def _timeline_body(tweet: dict) -> dict:
    return {
        "data": {
            "home": {
                "home_timeline_urt": {
                    "instructions": [
                        {
                            "entries": [
                                {
                                    "content": {
                                        "itemContent": {
                                            "tweet_results": {"result": tweet},
                                        }
                                    }
                                }
                            ]
                        }
                    ]
                }
            }
        }
    }


def _tweet(
    *,
    rest_id: str = "1234567890",
    text: str = "Hello from the timeline",
    core_handle: str | None = "new_handle",
    core_name: str | None = "New Name",
    legacy_handle: str | None = "legacy_handle",
    legacy_name: str | None = "Legacy Name",
    legacy_bio: str | None = None,
    profile_bio: str | None = "Profile bio text",
    favorite_count: int = 7,
) -> dict:
    user_result: dict = {
        "core": {
            "screen_name": core_handle,
            "name": core_name,
        },
        "legacy": {
            "screen_name": legacy_handle,
            "name": legacy_name,
            "description": legacy_bio,
        },
    }
    if profile_bio is not None:
        user_result["profile_bio"] = {"description": profile_bio}
    return {
        "__typename": "Tweet",
        "rest_id": rest_id,
        "legacy": {
            "id_str": rest_id,
            "full_text": text,
            "favorite_count": favorite_count,
            "retweet_count": 2,
            "reply_count": 1,
            "quote_count": 0,
        },
        "core": {"user_results": {"result": user_result}},
    }


def test_parse_response_current_schema_user_core_fields():
    body = _timeline_body(_tweet())
    items = parse_response(body, session_id="sess-1", source="x_home")

    assert len(items) == 1
    item = items[0]
    assert item.item_id == "1234567890"
    assert item.author_handle == "new_handle"
    assert item.author_name == "New Name"
    assert item.author_bio == "Profile bio text"
    assert item.text == "Hello from the timeline"
    assert item.engagement == {
        "favorite_count": 7,
        "retweet_count": 2,
        "reply_count": 1,
        "quote_count": 0,
        "bookmarked": None,
        "favorited": None,
    }


def test_parse_response_legacy_user_fallback_and_bio_precedence():
    body = _timeline_body(
        _tweet(
            core_handle=None,
            core_name=None,
            legacy_bio="Legacy description",
            profile_bio="Should not win",
        )
    )
    items = parse_response(body, session_id="sess-2")

    assert len(items) == 1
    item = items[0]
    assert item.author_handle == "legacy_handle"
    assert item.author_name == "Legacy Name"
    assert item.author_bio == "Legacy description"


def test_stable_hash_uses_resolved_handle_when_rest_id_missing():
    tweet = _tweet(rest_id="", text="No id tweet")
    tweet.pop("rest_id", None)
    tweet["legacy"].pop("id_str", None)
    body = _timeline_body(tweet)

    items = parse_response(body)
    expected_id = stable_hash("new_handle", "No id tweet", None, None)

    assert len(items) == 1
    assert items[0].item_id == expected_id