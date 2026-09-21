"""Cycle analysis on the active (type-filtered) view of a graph.

Filtering by dependency type never mutates the original graph; it only
changes which edges participate in the analysis view.
"""
from __future__ import annotations

from .model import DEFAULT_ACTIVE_TYPES


def adjacency(graph, active_types=DEFAULT_ACTIVE_TYPES):
    adj = {node: set() for node in graph.components}
    for edge in graph.active_edges(active_types):
        if edge["source"] in adj and edge["target"] in adj:
            adj[edge["source"]].add(edge["target"])
    return {node: sorted(targets) for node, targets in adj.items()}


def strongly_connected_components(graph, active_types=DEFAULT_ACTIVE_TYPES):
    """Iterative Tarjan. Returns a sorted list of sorted node lists."""
    adj = adjacency(graph, active_types)
    index_of = {}
    lowlink = {}
    on_stack = set()
    stack = []
    result = []
    counter = 0
    for root in sorted(adj):
        if root in index_of:
            continue
        index_of[root] = lowlink[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        work = [(root, iter(adj[root]))]
        while work:
            node, it = work[-1]
            descended = False
            for nxt in it:
                if nxt not in index_of:
                    index_of[nxt] = lowlink[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on_stack.add(nxt)
                    work.append((nxt, iter(adj[nxt])))
                    descended = True
                    break
                elif nxt in on_stack:
                    lowlink[node] = min(lowlink[node], index_of[nxt])
            if descended:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])
            if lowlink[node] == index_of[node]:
                scc = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    scc.append(member)
                    if member == node:
                        break
                result.append(sorted(scc))
    return sorted(result)


def cyclic_sccs(graph, active_types=DEFAULT_ACTIVE_TYPES):
    """SCCs that contain a cycle: size > 1, or a single node with a self-loop."""
    types = set(active_types)
    self_loops = {
        e["source"]
        for e in graph.edges
        if e["type"] in types and e["source"] == e["target"]
    }
    return [
        scc
        for scc in strongly_connected_components(graph, active_types)
        if len(scc) > 1 or scc[0] in self_loops
    ]


def is_acyclic(graph, active_types=DEFAULT_ACTIVE_TYPES):
    return not cyclic_sccs(graph, active_types)


def cyclic_edges(graph, active_types=DEFAULT_ACTIVE_TYPES):
    """Active edges that lie on some cycle (inside a cyclic SCC, or self-loops)."""
    sccs = cyclic_sccs(graph, active_types)
    members = {node for scc in sccs for node in scc}
    types = set(active_types)
    out = []
    for edge in graph.edges:
        if edge["type"] not in types:
            continue
        if edge["source"] in members and edge["target"] in members:
            out.append(edge)
    return sorted(out, key=lambda e: e["id"])


def _canonical_cycle(path):
    """Rotate a cycle so its smallest node comes first (dedup key)."""
    best = min(range(len(path)), key=lambda i: path[i])
    return tuple(path[best:] + path[:best])


def enumerate_cycles(graph, active_types=DEFAULT_ACTIVE_TYPES, limit=64):
    """Simple cycles inside cyclic SCCs, canonicalised and sorted."""
    adj = adjacency(graph, active_types)
    cycles = set()
    for scc in cyclic_sccs(graph, active_types):
        members = set(scc)
        for start in scc:
            stack = [(start, [start])]
            while stack and len(cycles) < limit:
                node, path = stack.pop()
                for nxt in adj.get(node, []):
                    if nxt == start:
                        cycles.add(_canonical_cycle(path))
                    elif nxt in members and nxt not in path and len(path) <= len(members):
                        stack.append((nxt, path + [nxt]))
    return sorted(cycles)


def cycle_alive(graph, active_types, cycle):
    """True if every consecutive pair of the cycle is still an active edge."""
    types = set(active_types)
    pairs = set()
    for edge in graph.edges:
        if edge["type"] in types:
            pairs.add((edge["source"], edge["target"]))
    n = len(cycle)
    if n == 1:
        return (cycle[0], cycle[0]) in pairs
    return all((cycle[i], cycle[(i + 1) % n]) in pairs for i in range(n))


def reachable_paths(graph, active_types, src, dst, limit=3):
    """Up to `limit` simple paths src -> dst, shortest then lexicographic."""
    adj = adjacency(graph, active_types)
    max_depth = len(adj) + 1
    found = []

    def dfs(node, path):
        if len(found) >= limit * 4 or len(path) > max_depth:
            return
        if node == dst:
            found.append(list(path))
            return
        for nxt in adj.get(node, []):
            if nxt not in path:
                dfs(nxt, path + [nxt])

    if src in adj and dst in adj:
        dfs(src, [src])
    found.sort(key=lambda p: (len(p), p))
    return found[:limit]
