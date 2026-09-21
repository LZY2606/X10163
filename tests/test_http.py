import json
import threading
import urllib.error
import urllib.request

import pytest

from cycleaccord.demo import DEMO_GRAPH
from cycleaccord.server import make_server



@pytest.fixture()
def http_server(tmp_path):
    httpd, service = make_server("127.0.0.1", 0, str(tmp_path / "http.db"))
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    service.import_graph(DEMO_GRAPH)
    yield "http://127.0.0.1:%d" % port, service
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=2)


def _get(base, path):
    with urllib.request.urlopen(base + path) as r:
        return r.status, r.read().decode("utf-8")


def _post(base, path, payload):
    req = urllib.request.Request(
        base + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def test_index_page_title(http_server):
    base, _ = http_server
    status, body = _get(base, "/")
    assert status == 200
    assert "依赖环协商器" in body


def test_full_negotiation_flow(http_server):
    base, _ = http_server

    status, analysis = _post(base, "/api/analyses",
                             {"include_types": ["hard", "runtime", "test"]})
    assert status == 200
    aid = analysis["analysis"]["id"]
    cid = analysis["analysis"]["candidates"][0]["id"]
    assert analysis["analysis"]["sccs"]

    status, verify = _get(base, "/api/verify?analysis_id=%s&candidate_id=%s"
                          % (aid, cid))
    verify = json.loads(verify)
    assert verify["acyclic"] is True

    status, opinion = _post(base, "/api/opinions", {
        "analysis_id": aid, "candidate_id": cid, "owner": "lin",
        "decision": "accept", "comment": "ok"})
    assert status == 200 and opinion["ok"]

    status, approval = _post(base, "/api/approve", {
        "analysis_id": aid, "candidate_id": cid, "owner": "lin"})
    assert status == 200 and approval["selection"]["current"] is True

    status, state = _get(base, "/api/state")
    assert status == 200
    state = json.loads(state)
    rec = next(a for a in state["analyses"] if a["id"] == aid)
    assert rec["stale"] is False
    assert len(rec["selections"]) == 1


def test_graph_change_marks_old_analysis_stale(http_server):
    base, _ = http_server
    _, analysis = _post(base, "/api/analyses",
                        {"include_types": ["hard", "runtime", "test"]})
    aid = analysis["analysis"]["id"]
    cid = analysis["analysis"]["candidates"][0]["id"]

    _, imported = _post(base, "/api/import", {"graph": {
        "nodes": [{"id": "solo"}], "edges": []}})
    assert imported["graph_version"] == 2

    _, approval = _post(base, "/api/approve", {
        "analysis_id": aid, "candidate_id": cid, "owner": "lin"})
    assert approval["selection"]["current"] is False

    _, state_raw = _get(base, "/api/state")
    state = json.loads(state_raw)
    rec = next(a for a in state["analyses"] if a["id"] == aid)
    assert rec["stale"] is True


def test_bad_json_returns_400(http_server):
    base, _ = http_server
    req = urllib.request.Request(
        base + "/api/opinions", data=b"not-json",
        headers={"Content-Type": "application/json"})
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 400
