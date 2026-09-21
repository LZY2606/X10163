"""Analysis tests: SCCs, cycles, candidate generation, cost stability."""
import pytest

from cycleaccord.analysis import (
    analyze, apply_ops, candidate_cost, cyclic_sccs, generate_candidates,
    is_acyclic, simple_cycles, verify_candidate,
)
from cycleaccord.model import Graph

ALL = ["hard", "runtime", "test", "generated"]


def make_graph(edges, components=None):
    ids = sorted({e["source"] for e in edges} | {e["target"] for e in edges})
    comps = components or [{"id": i, "owner": "o-%s" % i} for i in ids]
    return Graph(comps, edges)


def test_self_loop_breaks():
    g = make_graph([{"id": "e1", "source": "a", "target": "a", "type": "hard"}])
    assert cyclic_sccs(g, ["hard"]) == [["a"]]
    assert simple_cycles(g, ["hard"]) == [["e1"]]
    cands = generate_candidates(g, ["hard"])
    assert cands, "self-loop must have a legal fix"
    top = cands[0]
    assert top["ops"][0]["kind"] == "downgrade"  # cheapest op wins
    assert top["verifies"]
    assert is_acyclic(apply_ops(g, top["ops"]), ["hard"])
    # original graph untouched
    assert g.edge("e1")["type"] == "hard"


def test_parallel_edges_need_all_cut():
    edges = [
        {"id": "e1", "source": "a", "target": "b", "type": "hard"},
        {"id": "e2", "source": "a", "target": "b", "type": "hard"},
        {"id": "e3", "source": "b", "target": "a", "type": "hard"},
    ]
    g = make_graph(edges)
    cycles = simple_cycles(g, ["hard"])
    assert sorted(map(sorted, cycles)) == [["e1", "e3"], ["e2", "e3"]]
    # Cutting only one parallel edge is not enough.
    assert not verify_candidate(g, ["hard"],
                                [{"kind": "downgrade", "edge": "e1", "to": "test"}])
    # Minimal candidate: a single op on the shared edge e3.
    cands = generate_candidates(g, ["hard"])
    assert cands[0]["ops"] == [{"kind": "downgrade", "edge": "e3", "to": "runtime"}]
    for c in cands:
        assert verify_candidate(g, ["hard"], c["ops"])


def test_type_filtering_view():
    edges = [
        {"id": "e1", "source": "a", "target": "b", "type": "hard"},
        {"id": "e2", "source": "b", "target": "a", "type": "test"},
    ]
    g = make_graph(edges)
    assert cyclic_sccs(g, ["hard"]) == []           # test edge excluded
    assert cyclic_sccs(g, ALL) == [["a", "b"]]      # included -> cycle
    # original graph unchanged by views
    assert g.edge("e2")["type"] == "test"


def test_multiple_intersecting_cycles():
    edges = [
        {"id": "e1", "source": "a", "target": "b", "type": "hard"},
        {"id": "e2", "source": "b", "target": "c", "type": "hard"},
        {"id": "e3", "source": "c", "target": "a", "type": "hard"},
        {"id": "e4", "source": "b", "target": "d", "type": "hard"},
        {"id": "e5", "source": "d", "target": "b", "type": "hard"},
    ]
    g = make_graph(edges)
    cycles = simple_cycles(g, ["hard"])
    assert len(cycles) == 2
    cands = generate_candidates(g, ["hard"])
    assert cands
    for c in cands:
        assert verify_candidate(g, ["hard"], c["ops"])
        assert not simple_cycles(apply_ops(g, c["ops"]), ["hard"])


def test_stable_minimal_candidates():
    edges = [
        {"id": "e1", "source": "a", "target": "b", "type": "hard"},
        {"id": "e2", "source": "b", "target": "a", "type": "hard"},
    ]
    comps = [{"id": "a", "owner": "alice", "weight": 1},
             {"id": "b", "owner": "bob", "weight": 1}]
    g1, g2 = Graph(comps, edges), Graph(comps, edges)
    run1 = generate_candidates(g1, ["hard"])
    run2 = generate_candidates(g2, ["hard"])
    assert [c["ops"] for c in run1] == [c["ops"] for c in run2]  # stable order
    costs = [c["cost"] for c in run1]
    assert costs == sorted(costs)                                # min cost first
    # smallest change wins: a one-step downgrade is the cheapest legal op
    assert run1[0]["ops"][0] == {"kind": "downgrade", "edge": "e1", "to": "runtime"}
    # every candidate is machine-checkable against a graph copy
    for c in run1:
        assert verify_candidate(g1, ["hard"], c["ops"])


def test_cost_prefers_cheap_and_few_owners():
    edges = [
        {"id": "e1", "source": "a", "target": "b", "type": "hard"},
        {"id": "e2", "source": "b", "target": "a", "type": "hard"},
    ]
    same_owner = Graph([{"id": "a", "owner": "x"}, {"id": "b", "owner": "x"}], edges)
    diff_owner = Graph([{"id": "a", "owner": "x"}, {"id": "b", "owner": "y"}], edges)
    op = [{"kind": "downgrade", "edge": "e1", "to": "runtime"}]
    assert candidate_cost(same_owner, op) < candidate_cost(diff_owner, op)
    heavy = Graph([{"id": "a", "owner": "x", "weight": 9},
                   {"id": "b", "owner": "x"}], edges)
    assert candidate_cost(same_owner, op) < candidate_cost(heavy, op)


def test_no_legal_split_when_locked():
    edges = [
        {"id": "e1", "source": "a", "target": "b", "type": "hard", "locked": True},
        {"id": "e2", "source": "b", "target": "a", "type": "hard", "locked": True},
    ]
    g = make_graph(edges)
    assert simple_cycles(g, ["hard"])                 # cycle exists
    assert generate_candidates(g, ["hard"]) == []     # but no legal split


def test_interface_op_never_invented_and_breaks_cycle():
    comps = [{"id": "a", "owner": "x"},
             {"id": "b", "owner": "y", "interfaces": ["I-B"]}]
    edges = [
        {"id": "e1", "source": "a", "target": "b", "type": "hard"},
        {"id": "e2", "source": "b", "target": "a", "type": "hard"},
    ]
    g = Graph(comps, edges)
    cands = generate_candidates(g, ["hard"], limit=12)
    iface = [c for c in cands if c["ops"][0]["kind"] == "interface"]
    assert iface, "registered interface I-B should be offered"
    assert iface[0]["ops"][0]["interface"] == "I-B"
    after = apply_ops(g, iface[0]["ops"])
    assert after.has_edge("e1")
    assert after.edge("e1")["target"] == "I-B"
    assert is_acyclic(after, ["hard"])
    # no interface registered on `a` -> no op may reference one
    for c in cands:
        for op in c["ops"]:
            assert op.get("interface") in (None, "I-B")


def test_reverse_op():
    edges = [
        {"id": "e1", "source": "a", "target": "b", "type": "hard"},
        {"id": "e2", "source": "b", "target": "a", "type": "hard"},
    ]
    g = make_graph(edges)
    rev = [{"kind": "reverse", "edge": "e1"}]
    after = apply_ops(g, rev)
    assert after.edge("e1")["source"] == "b"
    assert is_acyclic(after, ["hard"])


def test_analyze_reports_sccs_and_candidates():
    edges = [
        {"id": "e1", "source": "a", "target": "b", "type": "hard"},
        {"id": "e2", "source": "b", "target": "a", "type": "runtime"},
        {"id": "e3", "source": "b", "target": "c", "type": "hard"},
    ]
    g = make_graph(edges)
    result = analyze(g, ["hard", "runtime"])
    assert result["sccs"] == [["a", "b"]]
    assert result["candidates"]
    assert all(c["verifies"] for c in result["candidates"])
