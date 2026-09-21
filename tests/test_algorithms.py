from cycleaccord.algorithms import (
    cyclic_sccs,
    elementary_cycles,
    effective_edges,
    has_cycle,
    strongly_connected_components,
)
from cycleaccord.graphio import parse_graph

from conftest import ALL_TYPES


def test_self_loop_is_its_own_cyclic_scc():
    g = parse_graph({
        "nodes": [{"id": "a"}, {"id": "b"}],
        "edges": [
            {"id": "e1", "src": "a", "dst": "b"},
            {"id": "e2", "src": "a", "dst": "a"},
        ],
    })
    sccs = cyclic_sccs(g, ALL_TYPES)
    assert sccs == [["a"]]
    cycles = elementary_cycles(["a"], effective_edges(g, ALL_TYPES))
    assert cycles == [["a"]]


def test_parallel_edges_both_participate_in_scc():
    g = parse_graph({
        "nodes": [{"id": "a"}, {"id": "b"}],
        "edges": [
            {"id": "p1", "src": "a", "dst": "b", "type": "hard"},
            {"id": "p2", "src": "a", "dst": "b", "type": "test"},
            {"id": "q1", "src": "b", "dst": "a", "type": "runtime"},
        ],
    })
    sccs = cyclic_sccs(g, ALL_TYPES)
    assert sccs == [["a", "b"]]
    # 仅保留一条平行边时仍然成环
    assert has_cycle(["a", "b"], [g.edges["p1"], g.edges["q1"]])
    # 两条平行边都去掉才无环
    assert not has_cycle(["a", "b"], [])


def test_scc_deterministic_order():
    g = parse_graph({
        "nodes": [{"id": n} for n in ("z", "y", "x", "w")],
        "edges": [
            {"id": "1", "src": "z", "dst": "y"},
            {"id": "2", "src": "y", "dst": "z"},
            {"id": "3", "src": "x", "dst": "w"},
            {"id": "4", "src": "w", "dst": "x"},
        ],
    })
    first = strongly_connected_components(list(g.nodes), list(g.edges.values()))
    second = strongly_connected_components(list(g.nodes), list(g.edges.values()))
    assert first == second == [["w", "x"], ["y", "z"]]


def test_multiple_intersecting_cycles_enumerated():
    g = parse_graph({
        "nodes": [{"id": n} for n in ("a", "b", "c")],
        "edges": [
            {"id": "e1", "src": "a", "dst": "b"},
            {"id": "e2", "src": "b", "dst": "c"},
            {"id": "e3", "src": "c", "dst": "a"},
            {"id": "e4", "src": "c", "dst": "b"},
        ],
    })
    comp = ["a", "b", "c"]
    cycles = elementary_cycles(comp, effective_edges(g, ALL_TYPES))
    assert ["a", "b", "c"] in cycles
    assert ["b", "c"] in cycles
