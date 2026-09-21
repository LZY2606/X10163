"""Graph data model: components, typed edges, registered interfaces."""
from __future__ import annotations

import copy

EDGE_TYPES = ("hard", "runtime", "test", "generated")

# A hard edge may be downgraded to any weaker type, and so on.
WEAKER_TYPES = {
    "hard": ("runtime", "test", "generated"),
    "runtime": ("test", "generated"),
    "test": ("generated",),
    "generated": (),
}


class Graph:
    """In-memory dependency graph. The stored dict form is the source of truth."""

    def __init__(self, components=None, edges=None):
        self.components = {}
        for comp in components or []:
            c = dict(comp)
            c.setdefault("owner", "unassigned")
            c.setdefault("weight", 1)
            c.setdefault("kind", "component")
            c.setdefault("interfaces", [])
            self.components[c["id"]] = c
        self.edges = []
        for i, e in enumerate(edges or []):
            edge = dict(e)
            edge.setdefault("id", "e%d" % (i + 1))
            edge.setdefault("type", "hard")
            self.edges.append(edge)

    @classmethod
    def from_dict(cls, data):
        return cls(data.get("components", []), data.get("edges", []))

    def to_dict(self):
        return {
            "components": [copy.deepcopy(c) for c in self.components.values()],
            "edges": [copy.deepcopy(e) for e in self.edges],
        }

    def copy(self):
        return Graph.from_dict(self.to_dict())

    def component(self, cid):
        return self.components.get(
            cid,
            {"id": cid, "owner": "unassigned", "weight": 1,
             "kind": "component", "interfaces": []},
        )

    def edge(self, eid):
        for e in self.edges:
            if e["id"] == eid:
                return e
        raise KeyError("unknown edge: %s" % eid)

    def has_edge(self, eid):
        return any(e["id"] == eid for e in self.edges)

    def remove_edge(self, eid):
        self.edges = [e for e in self.edges if e["id"] != eid]

    def add_edge(self, edge):
        self.edges.append(dict(edge))

    def ensure_component(self, cid, **defaults):
        if cid not in self.components:
            c = {"id": cid, "owner": "unassigned", "weight": 1,
                 "kind": "component", "interfaces": []}
            c.update(defaults)
            self.components[cid] = c

    def node_ids(self):
        ids = set(self.components)
        for e in self.edges:
            ids.add(e["source"])
            ids.add(e["target"])
        return sorted(ids)

    def adjacency(self, included_types):
        """node -> list of (edge_id, target) restricted to the analysis view."""
        included = set(included_types)
        adj = {}
        for e in self.edges:
            if e["type"] in included:
                adj.setdefault(e["source"], []).append((e["id"], e["target"]))
        return adj
