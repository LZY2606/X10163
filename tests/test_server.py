"""End-to-end: HTTP API serves state, opinions, and the UI page."""
import json
import threading
import urllib.request

import pytest

from cycleaccord.server import ThreadingHTTPServer, make_handler
from cycleaccord.store import Store


@pytest.fixture
def server(tmp_path):
    store = Store(str(tmp_path / "state.json"))
    store.load_graph({
        "components": [{"id": "a", "owner": "alice"}, {"id": "b", "owner": "bob"}],
        "edges": [
            {"id": "e1", "source": "a", "target": "b", "type": "hard"},
            {"id": "e2", "source": "b", "target": "a", "type": "hard"},
        ],
    })
    store.set_view(["hard"])
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def get(url):
    with urllib.request.urlopen(url) as r:
        return r.read().decode("utf-8")


def post(url, body):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode("utf-8"))


def test_index_page(server):
    html = get(server + "/")
    assert "依赖环协商器" in html


def test_api_flow(server):
    state = json.loads(get(server + "/api/state"))
    assert state["analysis"]["result"]["candidates"]
    cand = state["analysis"]["result"]["candidates"][0]["id"]
    r = post(server + "/api/opinion", {"candidate_id": cand, "owner": "alice",
                                       "action": "accept"})
    assert r["ok"] and r["opinion"]["seq"] == 1
    r = post(server + "/api/select", {"candidate_id": cand, "by": "alice"})
    assert r["selection"]["candidate_id"] == cand
    state = json.loads(get(server + "/api/state"))
    assert state["selection"]["status"] == "current"
    r = post(server + "/api/view", {"types": ["hard", "runtime"]})
    assert r["ok"]
    state = json.loads(get(server + "/api/state"))
    assert state["view"]["types"] == ["hard", "runtime"]
