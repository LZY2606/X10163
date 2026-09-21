"""Core algorithm tests: self loops, parallel edges, type filtering,
intersecting cycles, stable minimal candidates, no legal split."""
import pytest

from cycleaccord.model import Component, Edge, Graph, InterfaceSpec
from cycleaccord.operations import EdgeOperation, verify, apply_operations
from cycleaccord.scc import (
    strongly_connected_components,
    enumerate_simple_cycles,
    cyclic_components,
)
from cycleaccord.candidates import analyze


def make_graph(components, edges, interfaces=None):
    graph = Graph()
    for cid, owner, weight in components:
        graph.add_component(Component(id=cid, name=cid, owner=owner, weight=weight))
    for eid, src, dst, etype in (edges if not isinstance(edges[0], Edge) else []):
        graph.add_edge(Edge(id=eid, source=src, target=dst, type=etype))
    for edge in edges if edges and isinstance(edges[0], Edge) else []:
        graph.add_edge(edge)
    for spec in interfaces or []:
        graph.add_interface(spec)
    assert graph.validate() == []
    return graph


# --------------------------------------------------------------------------
# Self loop
# --------------------------------------------------------------------------
def test_self_loop_detected_as_cyclic_scc():
    graph = make_graph(
        [("a", "alice", 1), ("b", "bob", 1)],
        [("e1", "a", "b", "hard"), ("e2", "b", "b", "generated")],
    )
    all_types = ["hard", "runtime", "test", "generated"]
    sccs = strongly_connected_components(graph, all_types)
    assert ["a"] in sccs and ["b"] in sccs
    cyclic = cyclic_components(graph, all_types)
    assert (["b"], True) in cyclic

    # Reversing a self loop cannot help (it points to itself); only filtering
    # the generated type out of the view removes it.
    result = analyze(graph, all_types)
    assert result.cyclic
    assert result.candidates == []
    assert result.sccs[0].self_loop
    assert not result.sccs[0].splittable

    filtered = analyze(graph, ["hard"])
    assert filtered.cyclic is False
    assert filtered.candidates == []


def test_self_loop_can_be_removed_by_type_filter_via_weaken():
    graph = make_graph(
        [("a", "alice", 1)],
        [Edge(id="e1", source="a", target="a", type="hard")],
    )
    result = analyze(graph, ["hard", "runtime"])
    # weaken hard -> generated/test leaves the hard-only view acyclic
    keys = {op.signature() for cand in result.candidates for op in cand.operations}
    assert any("weaken(e1->generated)" in k for k in keys)
    for cand in result.candidates:
        report = verify(graph, cand.operations, ["hard", "runtime"])
        assert report.ok


# --------------------------------------------------------------------------
# Parallel edges
# --------------------------------------------------------------------------
def test_parallel_edges_both_must_be_cut():
    graph = make_graph(
        [("a", "alice", 1), ("b", "bob", 1)],
        [
            Edge(id="h1", source="a", target="b", type="hard"),
            Edge(id="r1", source="a", target="b", type="runtime"),
            Edge(id="h2", source="b", target="a", type="hard"),
        ],
    )
    types = ["hard", "runtime"]
    result = analyze(graph, types)
    assert len(result.sccs) == 1
    cycles = result.sccs[0].cycles
    # Two distinct simple cycles through the parallel forward edges h1/r1.
    edge_sets = {frozenset(cy) for cy in cycles}
    assert frozenset({"h1", "h2"}) in edge_sets
    assert frozenset({"r1", "h2"}) in edge_sets

    # A candidate cutting only h1 still leaves r1+h2; every reported candidate
    # must be globally acyclic (r1 handled by reversal or weakening).
    for cand in result.candidates:
        assert verify(graph, cand.operations, types).ok
    cheapest = result.candidates[0]
    handled = {
        (op.op, op.edge_id)
        for op in cheapest.operations
    }
    # Removing/reversing the single back edge h2 is enough (both forward
    # edges then point a -> b with no return path), and it is present in the
    # cheapest set either reversed or weakened.
    assert any(edge_id == "h2" for _, edge_id in handled)


# --------------------------------------------------------------------------
# Type filtering never mutates the original graph
# --------------------------------------------------------------------------
def test_type_filter_is_a_projection_original_graph_unchanged():
    graph = make_graph(
        [("a", "alice", 1), ("b", "bob", 1), ("c", "carol", 1)],
        [
            Edge(id="x", source="a", target="b", type="hard"),
            Edge(id="y", source="b", target="c", type="test"),
            Edge(id="z", source="c", target="a", type="test"),
        ],
    )
    before = sorted(graph.edges)
    assert analyze(graph, ["hard"]).cyclic is False
    with_cycle = analyze(graph, ["hard", "test"])
    assert with_cycle.cyclic is True
    assert sorted(graph.edges) == before
    assert graph.edges["z"].type == "test"


# --------------------------------------------------------------------------
# Multiple intersecting cycles: one change set breaks them all
# --------------------------------------------------------------------------
def test_multiple_intersecting_cycles_one_candidate_breaks_all():
    graph = make_graph(
        [("a", "alice", 1), ("b", "bob", 1), ("c", "carol", 1), ("d", "dave", 1)],
        [
            Edge(id="ab", source="a", target="b", type="hard"),
            Edge(id="ba", source="b", target="a", type="hard"),
            Edge(id="bc", source="b", target="c", type="hard"),
            Edge(id="cb", source="c", target="b", type="hard"),
            Edge(id="cd", source="c", target="d", type="hard"),
            Edge(id="db", source="d", target="b", type="hard"),
        ],
    )
    result = analyze(graph, ["hard"])
    assert len(result.sccs) == 1
    # The SCC contains two independent loops (a<->b and b<->{c,d}), so the
    # minimum feedback set needs two edges; a single change cannot do it.
    cheapest = result.candidates[0]
    assert verify(graph, cheapest.operations, ["hard"]).ok
    assert len(cheapest.operations) == 2
    # the two chosen edges must hit both loops: one incident to {a,b} and the
    # other breaking the b/c/d loop
    ids = {op.edge_id for op in cheapest.operations}
    assert ids & {"ab", "ba"}
    assert ids & {"bc", "cb", "cd", "db"}
    # candidate explanation reports all simple cycles broken
    total_broken = sum(
        len(ex["broken_cycles"]) for ex in cheapest.explanations
    )
    assert total_broken >= 3


# --------------------------------------------------------------------------
# Stable, minimum-cost candidates
# --------------------------------------------------------------------------
def test_stable_minimum_candidate_order():
    graph = make_graph(
        [("a", "alice", 1), ("b", "alice", 1)],
        [
            Edge(id="aa", source="a", target="b", type="hard"),
            Edge(id="bb", source="b", target="a", type="hard"),
        ],
    )
    # generated is excluded from the view, so a hard edge can leave the
    # projection by weakening to generated (cost 6, cheaper than reverse 10).
    types = ["hard", "runtime", "test"]
    first = analyze(graph, types)
    second = analyze(graph, types)
    assert first.candidates
    keys_first = [c.key for c in first.candidates]
    keys_second = [c.key for c in second.candidates]
    assert keys_first == keys_second  # deterministic
    costs = [c.cost for c in first.candidates]
    assert costs == sorted(costs)
    # cheapest is a single weakening (base 6 + node weights, same owner)
    cheapest = first.candidates[0]
    assert len(cheapest.operations) == 1
    assert cheapest.operations[0].op == "weaken"
    assert cheapest.cost_detail["base_change_cost"] == 6


def test_cost_includes_weight_and_cross_owner_penalty():
    light = make_graph(
        [("a", "alice", 1), ("b", "alice", 1)],
        [Edge(id="f", source="a", target="b", type="hard"),
         Edge(id="g", source="b", target="a", type="hard")],
    )
    heavy = make_graph(
        [("a", "alice", 10), ("b", "bob", 1)],
        [Edge(id="f", source="a", target="b", type="hard"),
         Edge(id="g", source="b", target="a", type="hard")],
    )
    types = ["hard", "runtime"]
    cheap = analyze(light, types).candidates[0]
    pricey = analyze(heavy, types).candidates[0]
    assert pricey.cost > cheap.cost
    assert pricey.cost_detail["cross_owner_penalty"] >= 3
    assert cheap.cost_detail["cross_owner_penalty"] == 0
