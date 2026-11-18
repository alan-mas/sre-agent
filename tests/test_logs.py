"""Log tool behaviour with a fake API (no cluster, no tokens)."""

from sre_agent.tools import logs


class FakeCore:
    def __init__(self, previous_text):
        self.previous_text = previous_text

    def read_namespaced_pod_log(self, pod, ns, previous=False, **_):
        if previous:
            return self.previous_text
        return "ERROR failed to connect to postgres\nERROR failed to connect to postgres\nFATAL exiting"


def test_previous_logs_fall_back_to_current_when_recycling(monkeypatch):
    monkeypatch.setattr(
        logs,
        "core",
        lambda: FakeCore("unable to retrieve container logs for containerd://abc"),
    )
    out = logs.get_pod_logs.invoke({"namespace": "demo", "pod": "p", "previous": True})
    assert out.startswith("[previous instance logs unavailable")
    assert "failed to connect to postgres  (x2)" in out  # repeats collapsed


def test_previous_logs_used_when_available(monkeypatch):
    monkeypatch.setattr(logs, "core", lambda: FakeCore("OOM in previous run"))
    assert (
        logs.get_pod_logs.invoke({"namespace": "demo", "pod": "p", "previous": True})
        == "OOM in previous run"
    )
