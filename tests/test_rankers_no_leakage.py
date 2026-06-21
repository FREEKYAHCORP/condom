from condom_core.rankers.features import rank_text


def test_rank_text_no_behavior_columns():
    row = {
        "author_handle": "a",
        "author_name": "A",
        "text": "hello",
        "quoted_text": None,
        "thread_context": None,
        "media_desc": None,
        "link_title": None,
        "link_excerpt": None,
        "save": 1,
    }
    assert "save" not in rank_text(row)
