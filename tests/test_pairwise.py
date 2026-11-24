"""Pairwise verdict rule and summary (no tokens)."""

from sre_agent.pairwise import PAIRWISE_PROMPT, PairChecks, pairwise_score, summarize


def checks(root_cause="tie", evidence="tie", action="tie", clarity="tie") -> PairChecks:
    return PairChecks(
        reasoning="r",
        root_cause=root_cause,
        evidence=evidence,
        action=action,
        clarity=clarity,
    )


def test_all_ties_split_the_point():
    a, b, _ = pairwise_score(checks())
    assert (a, b) == (0.5, 0.5)


def test_more_criteria_won_is_preferred():
    assert pairwise_score(checks(evidence="2", clarity="2", action="1"))[:2] == (
        0.0,
        1.0,
    )
    assert pairwise_score(checks(evidence="1"))[:2] == (1.0, 0.0)


def test_equal_wins_without_root_cause_is_a_tie():
    assert pairwise_score(checks(evidence="1", clarity="2"))[:2] == (0.5, 0.5)


def test_root_cause_gates_the_preference():
    # better written everywhere else cannot beat the answer with the right cause
    assert pairwise_score(
        checks(root_cause="1", evidence="2", action="2", clarity="2")
    )[:2] == (1.0, 0.0)


def test_every_pair_adds_up_to_one():
    for p in ("1", "2", "tie"):
        a, b, _ = pairwise_score(checks(root_cause=p, clarity="1"))
        assert a + b == 1.0


def test_comment_lists_every_pick():
    why = pairwise_score(checks(action="2"))[2]
    assert "root_cause=tie" in why and "action=2" in why and "clarity=tie" in why


def test_summarize_maps_sessions_to_names_regardless_of_order():
    results = [
        {"sessions": ["s-a", "s-b"], "scores": [1.0, 0.0]},
        {"sessions": ["s-b", "s-a"], "scores": [0.5, 0.5]},  # randomized order
        {"sessions": ["s-b", "s-a"], "scores": [1.0, 0.0]},
    ]
    assert summarize(results, {"s-a": "exp-a", "s-b": "exp-b"}) == {
        "exp-a": 1.5,
        "exp-b": 1.5,
    }


def test_prompt_guards_against_position_and_length_bias():
    text = PAIRWISE_PROMPT.lower()
    assert "position bias" in text and "length is not merit" in text
    assert "untrusted" in text
