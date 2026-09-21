"""Cycle analysis: SCCs, simple-cycle enumeration, candidate change sets."""
from __future__ import annotations

import itertools
import json

from .model import WEAKER_TYPES, Graph

OP_BASE_COST = {"interface": 2, "reverse": 3}
CROSS_OWNER_PENALTY = 4
MAX_CYCLES = 500
MAX_CYCLE_LEN = 12
MAX_OPS_PER_CANDIDATE = 3
MAX_OP_POOL = 60


def strongly_connected_components(nodes, adj):
    """Iterative Tarjan. adj: node -> list of (edge_id, target)."""
    index_of, low = {}, {}
    on_stack, stack, result = set(), [], []
    counter = 0
    for root in nodes:
        if root in index_of:
            continue
        work = [(root, 0)]
        while work:
            node, child_i = work[-1]
            if child_i == 0:
                index_of[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            edges = adj.get(node, [])
            descended = False
            while child_i < len(edges):
                tgt = edges[child_i][1]
                child_i += 1
                if tgt not in index_of:
                    work[-1] = (node, child_i)
                    work.append((tgt, 0))
                    descended = True
                    break
                elif tgt in on_stack:
                    low[node] = min(low[node], index_of[tgt])
            if descended:
                continue
            work.pop()
            if low[node] == index_of[node]:
                scc = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    scc.append(w)
                    if w == node:
                        break
                result.append(sorted(scc))
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
    return result


def cyclic_sccs(graph, included_types):
    """SCCs that contain a cycle: size > 1, or a self-loop."""
    adj = graph.adjacency(included_types)
    nodes = graph.node_ids()
    sccs = strongly_connected_components(nodes, adj)
    out = []
    for scc in sccs:
        cyclic = len(scc) > 1
        if not cyclic:
            only = scc[0]
            cyclic = any(t == only for _, t in adj.get(only, []))
        if cyclic:
            out.append(scc)
    return out


def simple_cycles(graph, included_types, max_cycles=MAX_CYCLES,
                  max_len=MAX_CYCLE_LEN):
    """Edge-aware simple-cycle enumeration (handles parallel edges, self-loops).

    Returns a list of cycles; each cycle is a list of edge ids.
    """
    adj = graph.adjacency(included_types)
    order = graph.node_ids()
    rank = {n: i for i, n in enumerate(order)}
    cycles = []
    for start in order:
        # DFS from `start`, only through nodes ranked >= start, so each
        # cycle is reported exactly once (from its smallest node).
        stack = [(start, [], frozenset([start]))]
        while stack:
            node, path, visited = stack.pop()
            if len(path) >= max_len:
                continue
            for eid, tgt in adj.get(node, []):
                if tgt == start:
                    cycles.append(path + [eid])
                    if len(cycles) >= max_cycles:
                        return cycles
                elif tgt not in visited and rank.get(tgt, -1) >= rank[start]:
                    stack.append((tgt, path + [eid], visited | {tgt}))
    return cycles


def is_acyclic(graph, included_types):
    return not cyclic_sccs(graph, included_types)


# ---------------------------------------------------------------------------
# Change operations
# ---------------------------------------------------------------------------

def ops_for_edge(graph, edge):
    """All legal ops for one edge. Never invents interfaces: only registered
    interfaces of the edge's target component may be introduced."""
    if edge.get("locked"):
        return []
    ops = []
    for weaker in WEAKER_TYPES.get(edge["type"], ()):
        ops.append({"kind": "downgrade", "edge": edge["id"], "to": weaker})
    if edge["source"] != edge["target"]:
        ops.append({"kind": "reverse", "edge": edge["id"]})
    target = graph.component(edge["target"])
    for iface in target.get("interfaces", []):
        ops.append({"kind": "interface", "edge": edge["id"], "interface": iface})
    return ops


def apply_op(graph, op):
    """Return a new graph with the op applied. Original graph is untouched."""
    g = graph.copy()
    edge = g.edge(op["edge"])
    kind = op["kind"]
    if kind == "downgrade":
        if op["to"] not in WEAKER_TYPES.get(edge["type"], ()):
            raise ValueError("cannot downgrade %s to %s" % (edge["type"], op["to"]))
        edge["type"] = op["to"]
    elif kind == "reverse":
        g.remove_edge(edge["id"])
        g.add_edge({"id": edge["id"], "source": edge["target"],
                    "target": edge["source"], "type": edge["type"],
                    "reversed": True})
    elif kind == "interface":
        iface = op["interface"]
        if iface not in g.component(edge["target"]).get("interfaces", []):
            raise ValueError("interface %s is not registered on %s"
                             % (iface, edge["target"]))
        provider = g.component(edge["target"])
        g.ensure_component(iface, kind="interface",
                           owner=provider.get("owner", "unassigned"), weight=0)
        g.remove_edge(edge["id"])
        g.add_edge({"id": edge["id"], "source": edge["source"],
                    "target": iface, "type": edge["type"], "via": "interface"})
    else:
        raise ValueError("unknown op kind: %s" % kind)
    return g


def apply_ops(graph, ops):
    g = graph
    for op in ops:
        g = apply_op(g, op)
    return g


def verify_candidate(graph, included_types, ops):
    """Machine-checkable: applying the ops to a copy must yield an acyclic
    graph under the analysis view."""
    try:
        g = apply_ops(graph, ops)
    except (KeyError, ValueError):
        return False
    return is_acyclic(g, included_types)


# ---------------------------------------------------------------------------
# Cost model
# ---------------------------------------------------------------------------

def op_cost(graph, op):
    """Downgrades cost one per weakening step (smaller change = cheaper)."""
    if op["kind"] == "downgrade":
        edge = graph.edge(op["edge"])
        return WEAKER_TYPES[edge["type"]].index(op["to"]) + 1
    return OP_BASE_COST[op["kind"]]


def candidate_cost(graph, ops):
    base = sum(op_cost(graph, o) for o in ops)
    touched = set()
    for o in ops:
        e = graph.edge(o["edge"])
        touched.add(e["source"])
        touched.add(e["target"])
    weight = sum(graph.component(n).get("weight", 1) for n in touched)
    owners = {graph.component(n).get("owner", "unassigned") for n in touched}
    cross = CROSS_OWNER_PENALTY * max(0, len(owners) - 1)
    return base + weight + cross


def _stable_key(ops):
    return json.dumps(ops, sort_keys=True, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Candidate generation & explanation
# ---------------------------------------------------------------------------

def _shortest_path(adj, src, dst, max_len=MAX_CYCLE_LEN):
    """BFS shortest path src -> dst; returns list of node ids or None."""
    if src == dst:
        return [src]
    queue = [(src, [src])]
    seen = {src}
    while queue:
        node, path = queue.pop(0)
        if len(path) > max_len:
            continue
        for _, tgt in adj.get(node, []):
            if tgt == dst:
                return path + [tgt]
            if tgt not in seen:
                seen.add(tgt)
                queue.append((tgt, path + [tgt]))
    return None


def explain_candidate(graph, included_types, ops):
    """Why sufficient, which paths are affected, who must confirm."""
    after = apply_ops(graph, ops)
    adj_after = after.adjacency(included_types)
    cut_edges = []
    affected_paths = []
    for op in ops:
        e = graph.edge(op["edge"])
        cut_edges.append(e["id"])
        # After the change, is the target still reachable from the source?
        # (i.e. the dependency still flows indirectly -> impacted path)
        path = _shortest_path(adj_after, e["source"], e["target"])
        if path and len(path) > 1:
            affected_paths.append({"edge": e["id"], "path": path})
    touched = set()
    for op in ops:
        e = graph.edge(op["edge"])
        touched.add(e["source"])
        touched.add(e["target"])
    owners = sorted({graph.component(n).get("owner", "unassigned")
                     for n in touched})
    return {
        "cut_edges": sorted(cut_edges),
        "affected_paths": affected_paths,
        "owners": owners,
        "verifies": is_acyclic(after, included_types),
    }


def generate_candidates(graph, included_types, limit=8,
                        max_ops=MAX_OPS_PER_CANDIDATE):
    """Generate min-cost change sets that break every cycle in the view.

    Cost ties are broken by a stable serialized-op ordering, so repeated
    runs return identical results.
    """
    cycles = simple_cycles(graph, included_types)
    if not cycles:
        return []
    edge_ids = sorted({eid for cyc in cycles for eid in cyc})
    pool = []
    for eid in edge_ids:
        pool.extend(ops_for_edge(graph, graph.edge(eid)))
    pool = pool[:MAX_OP_POOL]
    if not pool:
        return []

    found = {}
    for size in range(1, max_ops + 1):
        level = []
        for combo in itertools.combinations(pool, size):
            if len({o["edge"] for o in combo}) < size:
                continue  # at most one op per edge
            ops = sorted(combo, key=_stable_key)
            key = _stable_key(ops)
            if key in found:
                continue
            if verify_candidate(graph, included_types, ops):
                level.append((candidate_cost(graph, ops), key, ops))
                found[key] = True
        level.sort(key=lambda item: (item[0], item[1]))
        if level and size >= 1:
            # Prefer fewer ops; only look deeper if this level was empty.
            candidates = [ops for _, _, ops in level]
            return _build_candidates(graph, included_types, candidates, limit)
    return []


def _build_candidates(graph, included_types, op_sets, limit):
    out = []
    for i, ops in enumerate(op_sets[:limit]):
        info = explain_candidate(graph, included_types, ops)
        out.append({
            "id": "cand-%03d" % (i + 1),
            "ops": ops,
            "cost": candidate_cost(graph, ops),
            "owners": info["owners"],
            "cut_edges": info["cut_edges"],
            "affected_paths": info["affected_paths"],
            "verifies": info["verifies"],
        })
    return out


def analyze(graph, included_types, limit=8):
    """Full analysis view over the (unchanged) original graph."""
    included = [t for t in included_types]
    adj = graph.adjacency(included)
    sccs = strongly_connected_components(graph.node_ids(), adj)
    cyclic = set()
    for scc in sccs:
        is_cyc = len(scc) > 1
        if not is_cyc:
            only = scc[0]
            is_cyc = any(t == only for _, t in adj.get(only, []))
        if is_cyc:
            cyclic.update(scc)
    cycles = simple_cycles(graph, included)
    return {
        "types": included,
        "sccs": [s for s in sccs if any(n in cyclic for n in s)],
        "cycles": cycles,
        "candidates": generate_candidates(graph, included, limit=limit),
    }
