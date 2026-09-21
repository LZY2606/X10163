"""End-to-end HTTP smoke tests against the real threaded server."""
import json
import urllib.request

import pytest

from cycleaccord.persistence import Store
from cycleaccord.sample import SAMPLE_GRAPH
from cycleaccord.server import create_server
from cycleaccord.service import AccordService


@pytest.fixture()
def server(tmp_path):
    store = Store(str(tmp_path / "http.db"))
    svc = AccordService(store, bootstrap=SAMPLE_GRAPH)
    httpd = create_server("127.0.0.1", 0, svc)
    port = httpd.server_address[1]
    thread = __import__("threading").Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield ("http://127.0.0.1:%d" % port), svc
    httpd.shutdown()
    httpd.server_close()
    store.close()


def get(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode())


def post(base, path, payload):
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode())


def test_index_page_has_title(server):
    base, _ = server
    with urllib.request.urlopen(base + "/", timeout=5) as resp:
        html = resp.read().decode()
    assert "依赖环协商器" in html


def test_full_negotiation_flow(server):
    base, _ = server
    status, state = get(base, "/api/state")
    assert status == 200 and state["graph_version"] == 1

    status, analysis = post(base, "/api/analysis",
                            {"include_types": ["hard", "runtime", "test"]})
    assert status == 200
    assert analysis["cyclic"]
    assert analysis["candidates"], "expected break candidates on sample graph"
    candidate = analysis["candidates"][0]

    status, verify_report = post(base, "/api/verify", {
        "include_types": ["hard", "runtime", "test"],
        "operations": candidate["operations"],
    })
    assert verify_report["acyclic"] is True

    status, opinion = post(base, "/api/opinion", {
        "include_types": ["hard", "runtime", "test"],
        "candidate_key": candidate["key"],
        "author": "alice",
        "kind": "accept",
        "comment": "looks good",
    })
    assert opinion["version"] == 1

    status, decision = post(base, "/api/decide", {
        "include_types": ["hard", "runtime", "test"],
        "candidate_key": candidate["key"],
        "decided_by": "lead",
        "note": "approved",
    })
    assert decision["stale"] is False

    status, discussion = get(
        base,
        "/api/discussion?candidate=%s&types=hard,runtime,test" % candidate["key"],
    )
    assert len(discussion["opinions"]) == 1
    assert discussion["latest_decision"]["decided_by"] == "lead"


def test_type_filtering_hides_generated_self_loop(server):
    base, _ = server
    # With generated included, the inventory generated self-loop blocks a
    # legal split for that SCC -> no global candidates.
    _, all_types = post(base, "/api/analysis", {
        "include_types": ["hard", "runtime", "test", "generated"]
    })
    assert all_types["cyclic"]
    assert all_types["candidates"] == []
    assert any(not scc["splittable"] for scc in all_types["sccs"])

    _, filtered = post(base, "/api/analysis", {
        "include_types": ["hard", "runtime", "test"]
    })
    assert filtered["candidates"]
