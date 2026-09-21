"""Versioned approvals, append-only concurrent opinions, graph-change
invalidation, alternative-edge verification and persistence."""
import json
import threading

import pytest

from cycleaccord.model import Component, Edge, Graph, InterfaceSpec
from cycleaccord.persistence import Store
from cycleaccord.service import AccordService, ServiceError


TYPES = ["hard", "runtime", "test", "generated"]


def sample_payload():
    return {
        "components": [
            {"id": "a", "name": "A", "owner": "alice", "weight": 1},
            {"id": "b", "name": "B", "owner": "bob", "weight": 1},
        ],
        "edges": [
            {"id": "ab", "source": "a", "target": "b", "type": "hard"},
            {"id": "ba", "source": "b", "target": "a", "type": "hard"},
        ],
        "interfaces": [],
    }


@pytest.fixture()
def service(tmp_path):
    store = Store(str(tmp_path / "test.db"))
    svc = AccordService(store, bootstrap=sample_payload())
    yield svc
    store.close()


def first_key(service, types=TYPES):
    analysis = service.compute_analysis(types)
    assert analysis["candidates"], analysis
    return analysis["candidates"][0]["key"]


# --------------------------------------------------------------------------
# Concurrent opinions preserve per-author versions, never overwrite
# --------------------------------------------------------------------------
def test_concurrent_opinions_keep_separate_versions(service):
    key = first_key(service)
    versions = []
    errors = []

    def submit(author):
        try:
            result = service.submit_opinion(
                TYPES, key, author, "accept", comment="ok from %s" % author
            )
            versions.append((author, result["version"]))
        except Exception as exc:  # pragma: no cover - test diagnostic
            errors.append(exc)

    threads = [threading.Thread(target=submit, args=("owner%d" % i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    authors = [a for a, _ in versions]
    assigned = [v for _, v in versions]
    assert sorted(authors) == sorted("owner%d" % i for i in range(10))
    assert sorted(assigned) == list(range(1, 11))  # no lost/overwritten writes

    discussion = service.candidate_discussion(TYPES, key)
    assert len(discussion["opinions"]) == 10
    assert [o["version"] for o in discussion["opinions"]] == list(range(1, 11))
    # history is immutable: later submissions never change earlier rows
    assert all(not o["stale"] for o in discussion["opinions"])


def test_same_author_multiple_opinions_append_not_overwrite(service):
    key = first_key(service)
    v1 = service.submit_opinion(TYPES, key, "alice", "accept")
    v2 = service.submit_opinion(TYPES, key, "alice", "reject", comment="changed mind")
    assert v1["version"] == 1 and v2["version"] == 2
    opinions = service.candidate_discussion(TYPES, key)["opinions"]
    assert opinions[0]["kind"] == "accept"
    assert opinions[1]["kind"] == "reject"


# --------------------------------------------------------------------------
# Graph change invalidates prior approvals/opinions (kept as history)
# --------------------------------------------------------------------------
def test_approvals_become_history_after_graph_change(service):
    key = first_key(service)
    service.submit_opinion(TYPES, key, "alice", "accept")
    decision = service.decide(TYPES, key, "lead", note="ship it")
    assert decision["stale"] is False

    new_graph = sample_payload()
    # changed graph: cycle broken upstream in the new version
    new_graph["edges"] = [
        {"id": "ab", "source": "a", "target": "b", "type": "hard"}
    ]
    new_version = service.import_graph(new_graph, note="cycle removed")
    assert new_version == 2

    discussion = service.candidate_discussion(TYPES, key)
    assert all(o["stale"] for o in discussion["opinions"])
    assert discussion["latest_decision"]["stale"] is True
    assert discussion["latest_decision"]["graph_version"] == 1

    history = service.decisions_history(TYPES)
    assert history[-1]["stale"] is True


# --------------------------------------------------------------------------
# Alternative edge proposals must machine-verify to an acyclic copy
# --------------------------------------------------------------------------
def test_alternative_proposal_must_yield_dag(service):
    key = first_key(service)
    # Invalid alternative: no operations / a change that leaves the cycle
    with pytest.raises(ServiceError):
        service.submit_opinion(TYPES, key, "bob", "alternative", alternative_ops=[])

    with pytest.raises(ServiceError):
        service.submit_opinion(
            TYPES,
            key,
            "bob",
            "alternative",
            alternative_ops=[{"op": "weaken", "edge_id": "ab", "new_type": "hard"}],
        )

    saved = service.submit_opinion(
        TYPES,
        key,
        "bob",
        "alternative",
        alternative_ops=[{"op": "reverse", "edge_id": "ab"}],
        comment="try reversing instead",
    )
    assert saved["kind"] == "alternative"
    assert saved["alternative_ops"][0]["op"] == "reverse"


def test_verify_endpoint_reports_cyclic_components(service):
    bad = service.verify_operations(
        TYPES,
        [{"op": "reverse", "edge_id": "ab"}, {"op": "reverse", "edge_id": "ba"}],
    )
    assert bad["acyclic"] is False
    good = service.verify_operations(TYPES, [{"op": "reverse", "edge_id": "ab"}])
    assert good["acyclic"] is True


# --------------------------------------------------------------------------
# Persistence across service restarts + invalid imports rejected
# ---------------------------------------------------------------------------
def test_state_persists_across_restart(tmp_path):
    db_path = str(tmp_path / "persist.db")
    store = Store(db_path)
    svc = AccordService(store, bootstrap=sample_payload())
    key = first_key(svc)
    svc.submit_opinion(TYPES, key, "alice", "accept")
    svc.decide(TYPES, key, "lead")
    store.close()

    store2 = Store(db_path)
    svc2 = AccordService(store2)
    assert svc2.graph_version == 1
    discussion = svc2.candidate_discussion(TYPES, key)
    assert len(discussion["opinions"]) == 1
    assert discussion["latest_decision"]["decided_by"] == "lead"
    store2.close()


def test_invalid_graph_rejected_without_version_bump(service):
    bad = {"components": [], "edges": [
        {"id": "x", "source": "ghost", "target": "phantom", "type": "hard"}
    ]}
    with pytest.raises(ServiceError):
        service.import_graph(bad)
    assert service.graph_version == 1


def test_unknown_candidate_and_bad_kind_rejected(service):
    with pytest.raises(ServiceError):
        service.submit_opinion(TYPES, "nope", "alice", "accept")
    key = first_key(service)
    with pytest.raises(ServiceError):
        service.submit_opinion(TYPES, key, "", "accept")
    with pytest.raises(ServiceError):
        service.submit_opinion(TYPES, key, "alice", "maybe")
