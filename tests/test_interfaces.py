"""Registered interface introduction and the no-legal-split condition."""
from cycleaccord.model import Component, Edge, Graph, InterfaceSpec
from cycleaccord.operations import EdgeOperation, verify
from cycleaccord.candidates import analyze


def build():
    graph = Graph()
    for cid, owner, weight in [("consumer", "alice", 2), ("provider", "bob", 3)]:
        graph.add_component(Component(id=cid, name=cid, owner=owner, weight=weight))
    graph.add_edge(Edge(id="need", source="consumer", target="provider", type="hard"))
    graph.add_edge(Edge(id="callback", source="provider", target="consumer", type="hard"))
    graph.add_interface(
        InterfaceSpec(
            id="iface",
            provider="provider",
            consumers=("consumer",),
            replaces=("need",),
            description="registered seam",
        )
    )
    return graph


def test_interface_candidate_breaks_cycle_and_is_a_sink():
    graph = build()
    result = analyze(graph, ["hard", "runtime"])
    ops_sigs = {
        tuple(op.signature() for op in c.operations) for c in result.candidates
    }
    assert any("introduce_interface(iface)" in sig for sig in ops_sigs)
    interface_candidate = next(
        c for c in result.candidates
        if any(op.op == "introduce_interface" for op in c.operations)
    )
    report = verify(graph, interface_candidate.operations, ["hard", "runtime"])
    assert report.ok
    # The interface node has no outgoing dependency edges.
    from cycleaccord.operations import apply_operations
    copy, _ = apply_operations(graph, interface_candidate.operations)
    assert "need" not in copy.edges
    assert all(edge.target == "iface" for edge in copy.edges.values() if "iface" in edge.id)


def test_system_does_not_invent_interfaces():
    graph = build()
    bogus = [EdgeOperation(op="introduce_interface", interface_id="not_registered")]
    report = verify(graph, bogus, ["hard", "runtime"])
    assert not report.ok
    assert any("not registered" in err for err in report.errors)


def test_locked_cycle_without_interface_has_no_legal_split():
    graph = Graph()
    for cid in ("a", "b"):
        graph.add_component(Component(id=cid, name=cid, owner="team-x"))
    graph.add_edge(Edge(id="ab", source="a", target="b", type="hard", locked=True))
    graph.add_edge(Edge(id="ba", source="b", target="a", type="hard", locked=True))
    result = analyze(graph, ["hard"])
    assert result.cyclic
    assert result.candidates == []
    assert not result.sccs[0].splittable
    assert "locked" in result.sccs[0].unsplittable_reason


def test_registered_interface_can_unlock_a_locked_cycle():
    graph = Graph()
    for cid, owner in [("a", "alice"), ("b", "bob")]:
        graph.add_component(Component(id=cid, name=cid, owner=owner, weight=1))
    graph.add_edge(Edge(id="ab", source="a", target="b", type="hard", locked=True))
    graph.add_edge(Edge(id="ba", source="b", target="a", type="hard", locked=True))
    graph.add_interface(
        InterfaceSpec(
            id="seam",
            provider="b",
            consumers=("a",),
            replaces=("ab",),
            description="pre-approved seam",
        )
    )
    result = analyze(graph, ["hard"])
    assert result.candidates
    candidate = next(
        c for c in result.candidates
        if any(op.op == "introduce_interface" and op.interface_id == "seam"
               for op in c.operations)
    )
    assert verify(graph, candidate.operations, ["hard"]).ok


def test_double_touching_same_edge_rejected():
    graph = build()
    ops = [
        EdgeOperation(op="reverse", edge_id="need"),
        EdgeOperation(op="weaken", edge_id="need", new_type="runtime"),
    ]
    report = verify(graph, ops, ["hard", "runtime"])
    assert not report.ok
    assert any("modified twice" in err for err in report.errors)


def test_weaken_only_from_hard_to_allowed_type():
    graph = build()
    # cannot weaken a runtime edge
    graph.add_edge(Edge(id="r", source="consumer", target="provider", type="runtime"))
    report = verify(
        graph,
        [EdgeOperation(op="weaken", edge_id="r", new_type="test")],
        ["hard", "runtime", "test"],
    )
    assert not report.ok
    # cannot weaken to an invalid type
    report2 = verify(
        graph,
        [EdgeOperation(op="weaken", edge_id="need", new_type="nonsense")],
        ["hard", "runtime"],
    )
    assert not report2.ok
