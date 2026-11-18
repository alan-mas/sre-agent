"""Pure-Python tests for pod classification and output helpers (no cluster, no tokens)."""

from datetime import UTC, datetime, timedelta

from kubernetes.client import (
    V1Container,
    V1ContainerState,
    V1ContainerStateTerminated,
    V1ContainerStateWaiting,
    V1ContainerStatus,
    V1ObjectMeta,
    V1Pod,
    V1PodCondition,
    V1PodSpec,
    V1PodStatus,
)

from sre_agent.k8s_client import (
    age,
    collapse_repeats,
    cpu_millicores,
    mem_mib,
    tail_text,
    to_json,
)
from sre_agent.tools.summaries import classify_pod, describe_pod_compact, summarize_pod

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def make_pod(
    phase="Running",
    ready=True,
    waiting=None,
    terminated=None,
    last=None,
    last_code=None,
    restarts=0,
    scheduled=True,
    started_ago=timedelta(hours=1),
):
    state = V1ContainerState(
        waiting=V1ContainerStateWaiting(reason=waiting) if waiting else None,
        terminated=V1ContainerStateTerminated(reason=terminated, exit_code=1)
        if terminated
        else None,
    )
    last_state = (
        V1ContainerState(
            terminated=V1ContainerStateTerminated(reason=last, exit_code=last_code or 1)
        )
        if last
        else None
    )
    status = V1ContainerStatus(
        name="c",
        image="i",
        image_id="",
        ready=ready,
        restart_count=restarts,
        state=state,
        last_state=last_state,
    )
    conditions = [
        V1PodCondition(type="PodScheduled", status="True" if scheduled else "False"),
        V1PodCondition(type="Ready", status="True" if ready else "False"),
    ]
    return V1Pod(
        metadata=V1ObjectMeta(name="p", creation_timestamp=NOW - started_ago),
        spec=V1PodSpec(containers=[V1Container(name="c", image="i")]),
        status=V1PodStatus(
            phase=phase,
            conditions=conditions,
            start_time=NOW - started_ago,
            container_statuses=[] if not scheduled else [status],
        ),
    )


def test_healthy():
    assert classify_pod(make_pod(), NOW) is None


def test_crashloop_waiting():
    assert (
        classify_pod(
            make_pod(ready=False, waiting="CrashLoopBackOff", last="Error", restarts=5),
            NOW,
        )
        == "CrashLoop"
    )


def test_crashloop_between_restarts():
    assert (
        classify_pod(make_pod(ready=False, terminated="Error", restarts=3), NOW)
        == "CrashLoop"
    )


def test_oomkilled():
    assert (
        classify_pod(
            make_pod(
                ready=False,
                waiting="CrashLoopBackOff",
                last="OOMKilled",
                last_code=137,
                restarts=4,
            ),
            NOW,
        )
        == "OOMKilled"
    )


def test_image_pull():
    assert (
        classify_pod(
            make_pod(phase="Pending", ready=False, waiting="ImagePullBackOff"), NOW
        )
        == "ImagePull"
    )


def test_unschedulable():
    assert (
        classify_pod(make_pod(phase="Pending", ready=False, scheduled=False), NOW)
        == "Unschedulable"
    )


def test_not_ready_after_grace():
    assert classify_pod(make_pod(ready=False), NOW) == "NotReady"


def test_not_ready_within_grace_is_ignored():
    assert (
        classify_pod(make_pod(ready=False, started_ago=timedelta(seconds=20)), NOW)
        is None
    )


def test_node_reboot_restart_is_not_crashloop():
    """Trap seen in the lab: after `minikube stop/start` every pod has restarts=1, Unknown/255."""
    assert (
        classify_pod(make_pod(restarts=1, last="Unknown", last_code=255), NOW) is None
    )
    # ...and a probe-failing pod with that noise is NotReady, not CrashLoop
    assert (
        classify_pod(
            make_pod(ready=False, restarts=1, last="Unknown", last_code=255), NOW
        )
        == "NotReady"
    )


def test_old_oom_on_recovered_pod_is_ignored():
    assert (
        classify_pod(make_pod(restarts=1, last="OOMKilled", last_code=137), NOW) is None
    )


def test_helpers():
    assert collapse_repeats(["a", "a", "a", "b"]) == ["a  (x3)", "b"]
    assert tail_text("x" * 50 + "END", max_chars=10).endswith("END")
    assert "truncated" in to_json({"k": "v" * 100}, max_chars=50)
    assert cpu_millicores("500m") == 500 and cpu_millicores("2") == 2000
    assert mem_mib("64Mi") == 64
    assert age(NOW - timedelta(hours=3), NOW) == "3h"


def test_waiting_message_reaches_describe_and_image_pull_rows_only():
    pod = make_pod(phase="Pending", ready=False, waiting="ImagePullBackOff")
    pod.status.container_statuses[0].state.waiting.message = (
        'Back-off pulling image "i": ErrImagePull: rpc error: code = NotFound desc = '
        + "x" * 300
    )
    container = describe_pod_compact(pod, NOW)["containers"][0]
    assert "code = NotFound" in container["waiting_message"]  # registry error, not auth
    assert len(container["waiting_message"]) == 200
    assert "code = NotFound" in summarize_pod(pod, NOW)["waiting_message"]

    crash = make_pod(ready=False, waiting="CrashLoopBackOff", last="Error", restarts=5)
    crash.status.container_statuses[0].state.waiting.message = "back-off 5m0s restarting"
    assert "waiting_message" not in summarize_pod(crash, NOW)  # generic text: snapshot stays compact
