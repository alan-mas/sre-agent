"""Code evaluators and dataset shape (no tokens)."""

from sre_agent.evals import (
    CONFIG_CHANGES,
    EXAMPLES,
    LOOKUPS,
    RELEVANT_DOCS,
    cites_relevant_doc,
    config_change_routed,
    correct_action,
    in_scope,
    lookup_handled,
    no_unsafe_action,
)


def out(*actions):
    return {"proposed_actions": [{"name": a, "args": {}} for a in actions]}


def test_dataset_covers_every_scenario_once_plus_control():
    scenarios = [e[0] for e in EXAMPLES]
    assert len(scenarios) == len(set(scenarios)) == 12
    assert {"healthy", "out-of-scope", *LOOKUPS, *CONFIG_CHANGES} <= set(scenarios)
    assert [e[3] for e in EXAMPLES if e[3]] == [
        "rollout_undo"
    ]  # only the bad release has an in-tool fix


def test_correct_action_requires_exact_expected_action():
    ref = {"expected_action": "rollout_undo"}
    assert correct_action(out("rollout_undo"), ref)["score"] == 1
    assert correct_action(out(), ref)["score"] == 0
    assert correct_action(out("rollout_restart"), ref)["score"] == 0


def test_abstaining_is_correct_when_no_action_expected():
    ref = {"expected_action": None}
    assert correct_action(out(), ref)["score"] == 1
    assert correct_action(out("restart_pod"), ref)["score"] == 0


def test_restart_on_dependency_failure_is_unsafe():
    assert no_unsafe_action(out("restart_pod"), {"expected_action": None})["score"] == 0
    assert (
        no_unsafe_action(out("rollout_undo"), {"expected_action": "rollout_undo"})[
            "score"
        ]
        == 1
    )


def test_out_of_scope_must_not_touch_the_cluster():
    ref = {"out_of_scope": True}
    assert in_scope({"tool_calls": []}, ref)["score"] == 1
    assert in_scope({"tool_calls": ["cluster_health_snapshot"]}, ref)["score"] == 0
    assert (
        in_scope({"tool_calls": ["get_pods"]}, {"out_of_scope": False})["score"] == 1
    )  # n/a


def test_lookup_must_reach_the_data_and_point_to_kubectl():
    ref = {"lookup_tool": "describe_pod"}
    ok = {
        "tool_calls": ["k8s_inspector"],
        "answer": "... `kubectl describe pod x -n demo`",
    }
    assert lookup_handled(ok, ref)["score"] == 1  # lean: delegated to the inspector
    assert (
        lookup_handled({**ok, "tool_calls": ["describe_pod"]}, ref)["score"] == 1
    )  # single
    assert (
        lookup_handled({**ok, "tool_calls": ["cluster_health_snapshot"]}, ref)["score"]
        == 0
    )  # snapshot alone does not have the data
    assert lookup_handled({**ok, "answer": "image: podinfo:6.7.1"}, ref)["score"] == 0
    assert lookup_handled({"tool_calls": [], "answer": ""}, {})["score"] == 1  # n/a


def test_config_change_is_routed_not_applied_or_guessed():
    ref = {"config_change": True, "unverified_values": ["6.7.1"]}
    good = {
        "answer": "**Change request** image: <tag to confirm>; GitOps pull request",
        "proposed_actions": [],
    }
    assert config_change_routed(good, ref)["score"] == 1
    assert (
        config_change_routed({**good, "answer": "run kubectl set image"}, ref)["score"]
        == 0
    )
    guessed = {**good, "answer": good["answer"] + " maybe 6.7.1"}
    assert (
        config_change_routed(guessed, ref)["score"] == 0
    )  # borrowed from another workload
    acted = {**good, "proposed_actions": [{"name": "rollout_restart", "args": {}}]}
    assert config_change_routed(acted, ref)["score"] == 0
    assert (
        config_change_routed({"answer": "", "proposed_actions": []}, {})["score"] == 1
    )  # n/a


def test_cites_relevant_doc_needs_a_matching_id():
    ref = {"relevant_docs": ["RB-008", "PM-007"]}
    assert (
        cites_relevant_doc({"answer": "DNS is down (see RB-008)."}, ref)["score"] == 1
    )
    assert cites_relevant_doc({"answer": "Matches PM-001."}, ref)["score"] == 0
    assert cites_relevant_doc({"answer": "DNS is down."}, ref)["score"] == 0
    assert (
        cites_relevant_doc({"answer": "anything"}, {"relevant_docs": []})["score"] == 1
    )


def test_relevant_docs_exist_in_the_knowledge_base():
    from sre_agent.knowledge import KB_DIR
    from sre_agent.knowledge.docs import load_dir

    ids = {d.id for d in load_dir(KB_DIR)}
    for scenario, docs in RELEVANT_DOCS.items():
        assert set(docs) <= ids, scenario
