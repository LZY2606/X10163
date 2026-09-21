"""Machine-verifiable edge operations and their application to a graph copy."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

from .model import Component, Edge, Graph, WEAKER_THAN_HARD

REVERSE = "reverse"
WEAKEN = "weaken"
INTRODUCE_INTERFACE = "introduce_interface"


@dataclass(frozen=True)
class EdgeOperation:
    op: str
    edge_id: str = ""
    new_type: str = ""
    interface_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "op": self.op,
            "edge_id": self.edge_id,
            "new_type": self.new_type,
            "interface_id": self.interface_id,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "EdgeOperation":
        return EdgeOperation(
            op=str(d["op"]),
            edge_id=str(d.get("edge_id", "")),
            new_type=str(d.get("new_type", "")),
            interface_id=str(d.get("interface_id", "")),
        )

    def signature(self) -> str:
        if self.op == REVERSE:
            return "reverse(%s)" % self.edge_id
        if self.op == WEAKEN:
            return "weaken(%s->%s)" % (self.edge_id, self.new_type)
        return "introduce_interface(%s)" % self.interface_id


@dataclass
class OperationReport:
    ok: bool
    errors: List[str]
    # Counts of surviving SCCs with 2+ nodes, or a self-loop.
    cyclic_components: List[List[str]]


def validate_operation(graph: Graph, operation: EdgeOperation) -> List[str]:
    errors: List[str] = []
    if operation.op == REVERSE or operation.op == WEAKEN:
        edge = graph.edges.get(operation.edge_id)
        if edge is None:
            errors.append("%s references unknown edge %r" % (operation.op, operation.edge_id))
            return errors
        if operation.op == WEAKEN:
            if edge.type != "hard":
                errors.append(
                    "weaken: edge %s is %r, only hard edges can be weakened"
                    % (edge.id, edge.type)
                )
            if operation.new_type not in WEAKER_THAN_HARD:
                errors.append(
                    "weaken: %r is not an allowed weaker type for edge %s"
                    % (operation.new_type, edge.id)
                )
    elif operation.op == INTRODUCE_INTERFACE:
        if operation.interface_id not in graph.interfaces:
            errors.append(
                "introduce_interface: interface %r is not registered" % operation.interface_id
            )
    else:
        errors.append("unknown operation %r" % operation.op)
    return errors


def apply_operations(graph: Graph, operations: List[EdgeOperation]) -> Tuple[Graph, OperationReport]:
    """Apply operations to an independent graph copy.

    Two operations touching the same original edge are rejected. Interface
    introduction removes the replaced edges once (regardless of which
    candidate action mentioned them).
    """
    errors: List[str] = []
    copy = Graph(
        components=dict(graph.components),
        edges=dict(graph.edges),
        interfaces=dict(graph.interfaces),
    )

    touched: Dict[str, str] = {}
    introduced: set = set()
    for operation in operations:
        errors.extend(validate_operation(copy, operation))
        if operation.op in (REVERSE, WEAKEN):
            prev = touched.get(operation.edge_id)
            if prev is not None:
                errors.append(
                    "edge %s is modified twice (%s then %s)"
                    % (operation.edge_id, prev, operation.op)
                )
            touched[operation.edge_id] = operation.op

    if errors:
        return copy, OperationReport(False, errors, [])

    for operation in operations:
        if operation.op == REVERSE:
            edge = copy.edges[operation.edge_id]
            copy.edges[edge.id] = Edge(
                id=edge.id,
                source=edge.target,
                target=edge.source,
                type=edge.type,
                locked=edge.locked,
            )
        elif operation.op == WEAKEN:
            edge = copy.edges[operation.edge_id]
            copy.edges[edge.id] = Edge(
                id=edge.id,
                source=edge.source,
                target=edge.target,
                type=operation.new_type,
                locked=edge.locked,
            )
        elif operation.op == INTRODUCE_INTERFACE:
            if operation.interface_id in introduced:
                continue
            introduced.add(operation.interface_id)
            spec = copy.interfaces[operation.interface_id]
            provider = copy.components[spec.provider]
            # Register the synthetic interface component (owned by provider).
            copy.components[spec.id] = Component(
                id=spec.id,
                name=spec.description or ("interface:%s" % spec.id),
                owner=provider.owner,
                weight=0.0,
            )
            for removed_id in spec.replaces:
                copy.edges.pop(removed_id, None)
                touched.pop(removed_id, None)
            for synthetic in graph.interface_edges(spec):
                copy.edges[synthetic.id] = synthetic

    return copy, OperationReport(True, [], [])


from .scc import strongly_connected_components  # noqa: E402


def verify(
    graph: Graph, operations: List[EdgeOperation], include_types: List[str]
) -> OperationReport:
    """Apply operations to a graph copy, then project by type and confirm the
    resulting directed graph is acyclic (self-loops included as cycles)."""
    copy, report = apply_operations(graph, operations)
    if not report.ok:
        return report
    components = strongly_connected_components(copy, include_types)
    cyclic: List[List[str]] = []
    for members in components:
        if len(members) > 1:
            cyclic.append(members)
            continue
        node = members[0]
        for edge in copy.edges_for_view(include_types):
            if edge.source == node and edge.target == node:
                cyclic.append(members)
                break
    return OperationReport(not cyclic, [], cyclic)
