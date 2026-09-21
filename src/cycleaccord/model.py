"""Graph model and machine-checkable edge operations.

The original graph is never mutated by analysis: every operation is
validated and then applied to a copy, so a candidate change set can be
machine-verified (apply to copy -> the active view must be acyclic).
"""
from __future__ import annotations

import copy

EDGE_TYPES = ("hard", "runtime", "test", "generated")
STRENGTH = {"hard": 3, "runtime": 2, "test": 1, "generated": 0}
DEFAULT_ACTIVE_TYPES = ("hard", "runtime")


class GraphError(ValueError):
    """Raised for invalid graphs or illegal operations."""


class Graph:
    def __init__(self, components=None, edges=None, interfaces=None):
        self.components = components or {}
        self.edges = edges or []
        self.interfaces = interfaces or {}

    # -- construction / serialisation -------------------------------------
    @classmethod
    def from_dict(cls, data):
        components = {}
        for name, comp in (data.get("components") or {}).items():
            components[name] = {
                "owner": comp.get("owner", "unknown"),
                "critical": int(comp.get("critical", 1)),
            }
        edges = []
        for edge in data.get("edges") or []:
            edges.append(
                {
                    "id": edge["id"],
                    "source": edge["source"],
                    "target": edge["target"],
                    "type": edge["type"],
                }
            )
        interfaces = {}
        for name, iface in (data.get("interfaces") or {}).items():
            interfaces[name] = {
                "provider": iface["provider"],
                "description": iface.get("description", ""),
            }
        graph = cls(components, edges, interfaces)
        graph.validate()
        return graph

    def to_dict(self):
        return {
            "components": copy.deepcopy(self.components),
            "edges": copy.deepcopy(self.edges),
            "interfaces": copy.deepcopy(self.interfaces),
        }

    def copy(self):
        return Graph.from_dict(self.to_dict())

    def validate(self):
        ids = set()
        for edge in self.edges:
            if edge["id"] in ids:
                raise GraphError("duplicate edge id: %s" % edge["id"])
            ids.add(edge["id"])
            for endpoint in (edge["source"], edge["target"]):
                if endpoint not in self.components:
                    raise GraphError(
                        "edge %s references unknown component %s"
                        % (edge["id"], endpoint)
                    )
            if edge["type"] not in EDGE_TYPES:
                raise GraphError("edge %s has unknown type %s" % (edge["id"], edge["type"]))
        for name, iface in self.interfaces.items():
            if iface["provider"] not in self.components:
                raise GraphError("interface %s has unknown provider" % name)

    # -- accessors ---------------------------------------------------------
    def nodes(self):
        return sorted(self.components)

    def edge_by_id(self, edge_id):
        for edge in self.edges:
            if edge["id"] == edge_id:
                return edge
        raise GraphError("unknown edge id: %s" % edge_id)

    def active_edges(self, active_types):
        types = set(active_types)
        return [e for e in self.edges if e["type"] in types]

    def owner_of(self, component):
        comp = self.components.get(component)
        return comp["owner"] if comp else "unknown"


# ---------------------------------------------------------------------------
# Edge operations (machine-checkable)
# ---------------------------------------------------------------------------

OP_KINDS = ("reverse", "downgrade", "introduce_interface")


def canonical_op(op):
    """Stable string key used for deterministic ordering and dedup."""
    kind = op["op"]
    if kind == "reverse":
        return "reverse:%s" % op["edge"]
    if kind == "downgrade":
        return "downgrade:%s->%s" % (op["edge"], op["to"])
    if kind == "introduce_interface":
        return "iface:%s:%s" % (op["edge"], op["interface"])
    raise GraphError("unknown op kind: %s" % kind)


def validate_op(graph, op, active_types):
    kind = op.get("op")
    if kind not in OP_KINDS:
        raise GraphError("unknown op kind: %s" % kind)
    edge = graph.edge_by_id(op["edge"])
    if kind == "reverse":
        if edge["source"] == edge["target"]:
            raise GraphError("cannot reverse self-loop edge %s" % edge["id"])
        if edge["type"] == "generated":
            raise GraphError("cannot reverse generated edge %s" % edge["id"])
    elif kind == "downgrade":
        target = op.get("to")
        if edge["type"] != "hard":
            raise GraphError("only hard edges can be downgraded")
        if target not in EDGE_TYPES or STRENGTH[target] >= STRENGTH["hard"]:
            raise GraphError("downgrade target must be a weaker type")
        if target in set(active_types):
            raise GraphError("downgrade target %s is still in the active view" % target)
    elif kind == "introduce_interface":
        iname = op.get("interface")
        if iname not in graph.interfaces:
            raise GraphError("interface %s is not registered" % iname)
        if edge["source"] == edge["target"]:
            raise GraphError("cannot route a self-loop through an interface")
        if graph.interfaces[iname]["provider"] != edge["target"]:
            raise GraphError(
                "interface %s is not provided by %s" % (iname, edge["target"])
            )


def apply_ops(graph, ops, active_types=DEFAULT_ACTIVE_TYPES):
    """Return a NEW graph with the operations applied; input is untouched."""
    result = graph.copy()
    for op in ops:
        validate_op(result, op, active_types)
        kind = op["op"]
        edge = result.edge_by_id(op["edge"])
        if kind == "reverse":
            edge["source"], edge["target"] = edge["target"], edge["source"]
        elif kind == "downgrade":
            edge["type"] = op["to"]
        elif kind == "introduce_interface":
            iname = op["interface"]
            iface = result.interfaces[iname]
            provider = iface["provider"]
            result.edges = [e for e in result.edges if e["id"] != edge["id"]]
            result.components[iname] = {
                "owner": result.owner_of(provider),
                "critical": 0,
            }
            result.edges.append(
                {
                    "id": "%s~%s:use" % (edge["id"], iname),
                    "source": edge["source"],
                    "target": iname,
                    "type": "hard",
                }
            )
            result.edges.append(
                {
                    "id": "%s~%s:impl" % (edge["id"], iname),
                    "source": provider,
                    "target": iname,
                    "type": "hard",
                }
            )
    return result
