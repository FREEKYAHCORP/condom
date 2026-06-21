from condom_core.models import Item
from condom_core.render_timeline import render_timeline


def test_render_does_not_include_behavior_labels():
    text = render_timeline(Item(item_id="1", source="x", session_id="s", author_handle="a", text="hello"))
    assert "@a" in text
    assert "<1>" in text
    assert "save:" not in text
    assert "opened" not in text
