from condom_core.parse_llm_outputs import parse_llm_output


def test_parse_llm_output():
    rows = parse_llm_output("<8842> me: useful\n-> stop:y  open:n  save:y  look:11")
    assert rows[0]["item_id"] == "8842"
    assert rows[0]["pred_save"] == 1
    assert rows[0]["pred_look_sec"] == 11


def test_parse_unicode_sentinel_and_arrow():
    rows = parse_llm_output("⟦abc⟧ me: useful\n→ stop:yes, open:no, save:1, look:3.5")
    assert rows[0]["item_id"] == "abc"
    assert rows[0]["pred_stop"] == 1
    assert rows[0]["pred_open"] == 0
    assert rows[0]["pred_save"] == 1
    assert rows[0]["pred_look_sec"] == 3.5


def test_parse_metrics_without_arrow():
    rows = parse_llm_output("<1> me: nothing much\nstop:n open:n save:n look:0")
    assert rows[0]["item_id"] == "1"
    assert rows[0]["pred_stop"] == 0


def test_parse_prose_arrow_does_not_steal_metrics():
    text = "<1> me: I think about a -> b here, not metrics\n-> stop:y open:n save:n look:4"
    rows = parse_llm_output(text)
    assert rows[0]["reaction_text"] == "I think about a -> b here, not metrics"
    assert rows[0]["pred_look_sec"] == 4


def test_incomplete_block_is_dropped_not_crash():
    rows = parse_llm_output("<1> me: partial\n-> stop:y open:n")
    assert rows == []
