"""Store tests: versioning, concurrent opinions, approval invalidation."""
import threading

import pytest

from cycleaccord.model import Graph
from cycleaccord.store import Store


def graph_dict(extra_edges=()):
    return {
        "components": [{"id": "a", "owner": "alice"},
                       {"id": "b", "owner": "bob"}],
        "edges": [
            {"id": "e1", "source": "a", "target": "b", "type": "hard"},
            {"id": "e2", "source": "b", "target": "a", "type": "hard"},
        ] + list(extra_edges),
    }


@pytest.fixture
def store(tmp_path):
    s = Store(str(tmp_path / "state.json"))
    s.load_graph(graph_dict())
    s.set_view(["hard"])
    return s


def test_persistence_roundtrip(tmp_path):
    path = str(tmp_path / "state.json")
    s1 = Store(path)
    s1.load_graph(graph_dict())
    s1.set_view(["hard"])
    cand = s1.current_analysis()["result"]["candidates"][0]["id"]
    s1.add_opinion(cand, "alice", "accept", "looks fine")
    s1.select(cand, "alice")
    s2 = Store(path)  # reload from disk
    st = s2.state()
    assert st["graph"] == Graph.from_dict(graph_dict()).to_dict()
    assert st["graph_version"] == 1
    assert len(st["opinions"]) == 1
    assert st["selection"]["candidate_id"] == cand
    assert st["selection"]["status"] == "current"


def test_concurrent_opinions_all_kept(store):
    cand = store.current_analysis()["result"]["candidates"][0]["id"]
    results = []

    def submit(i):
        results.append(store.add_opinion(cand, "owner-%d" % (i % 3),
                                         "accept", "msg %d" % i))
    threads = [threading.Thread(target=submit, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    opinions = store.state()["opinions"]
    assert len(opinions) == 12                      # nothing overwritten
    assert len({o["id"] for o in opinions}) == 12
    assert sorted(o["seq"] for o in opinions) == list(range(1, 13))
    # per-owner versions are monotonic, later writes never clobber earlier
    by_owner = {}
    for o in opinions:
        by_owner.setdefault(o["owner"], []).append(o["owner_version"])
    for versions in by_owner.values():
        assert sorted(versions) == list(range(1, len(versions) + 1))


def test_approval_invalidated_by_graph_change(store):
    cand = store.current_analysis()["result"]["candidates"][0]["id"]
    store.add_opinion(cand, "alice", "accept")
    store.select(cand, "alice")
    st = store.state()
    assert st["opinions"][0]["status"] == "current"
    assert st["selection"]["status"] == "current"

    store.load_graph(graph_dict())  # graph changes -> old approvals are history
    st = store.state()
    assert st["graph_version"] == 2
    assert st["opinions"][0]["status"] == "historical"
    assert st["selection"]["status"] == "historical"
    assert all(o["action"] for o in st["opinions"])  # history preserved


def test_view_change_keeps_original_graph(store):
    before = store.state()["graph"]
    store.set_view(["hard", "test"])
    st = store.state()
    assert st["graph"] == before
    assert st["view"]["types"] == ["hard", "test"]
    assert len(st["analysis_versions"]) == 3  # load + 2 view changes


def test_unknown_candidate_rejected(store):
    with pytest.raises(ValueError):
        store.add_opinion("cand-999", "alice", "accept")
    with pytest.raises(ValueError):
        store.add_opinion("cand-001", "alice", "maybe")
