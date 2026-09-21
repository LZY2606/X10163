"""Strongly connected components (iterative Tarjan), cycle and reachability
helpers. Everything works on an explicit list of edges so analysis views can
project the graph without mutating it."""
from __future__ import annotations

import sys
from collections import deque
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .model import Graph


def adjacency(
    graph: Graph, include_types: Optional[Sequence[str]] = None
) -> Tuple[Dict[str, List[str]], Dict[str, List[str]], List[str]]:
    edges = (
        list(graph.edges.values())
        if include_types is None
        else graph.edges_for_view(list(include_types))
    )
    nodes = set(graph.components)
    out: Dict[str, List[str]] = {n: [] for n in nodes}
    incoming: Dict[str, List[str]] = {n: [] for n in nodes}
    for edge in edges:
        nodes.add(edge.source)
        nodes.add(edge.target)
        out.setdefault(edge.source, []).append(edge.target)
        incoming.setdefault(edge.target, []).append(edge.source)
        out.setdefault(edge.target, out.get(edge.target, []))
        incoming.setdefault(edge.source, incoming.get(edge.source, []))
    for targets in out.values():
        targets.sort()
    for sources in incoming.values():
        sources.sort()
    return out, incoming, sorted(nodes)


def strongly_connected_components(
    graph: Graph, include_types: Optional[Sequence[str]] = None
) -> List[List[str]]:
    """Return SCCs as node lists, ordered deterministically (smallest node of
    the SCC ascending; members sorted within each SCC)."""
    index_of: Dict[str, int] = {}
    lowlink: Dict[str, int] = {}
    on_stack: Set[str] = set()
    stack: List[str] = []
    result: List[List[str]] = []
    counter = 0

    out, _, ordered_nodes = adjacency(graph, include_types)

    for root in ordered_nodes:
        if root in index_of:
            continue
        work: List[Tuple[str, int]] = [(root, 0)]
        while work:
            node, neighbor_pos = work[-1]
            if neighbor_pos == 0:
                index_of[node] = counter
                lowlink[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            neighbors = out.get(node, [])
            if neighbor_pos < len(neighbors):
                work[-1] = (node, neighbor_pos + 1)
                child = neighbors[neighbor_pos]
                if child not in index_of:
                    work.append((child, 0))
                elif child in on_stack:
                    lowlink[node] = min(lowlink[node], index_of[child])
            else:
                if lowlink[node] == index_of[node]:
                    component: List[str] = []
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        component.append(member)
                        if member == node:
                            break
                    result.append(sorted(component))
                work.pop()
                if work:
                    parent = work[-1][0]
                    lowlink[parent] = min(lowlink[parent], lowlink[node])

    result.sort(key=lambda members: members[0])
    return result


def cyclic_components(
    graph: Graph, include_types: Sequence[str]
) -> List[Tuple[List[str], bool]]:
    """Return (members, self_loop_only) for every SCC that participates in a
    cycle under the given type projection."""
    view_edges = graph.edges_for_view(list(include_types))
    loops = {
        edge.source
        for edge in view_edges
        if edge.source == edge.target
    }
    cyclic: List[Tuple[List[str], bool]] = []
    for members in strongly_connected_components(graph, include_types):
        if len(members) > 1:
            cyclic.append((members, False))
        elif members[0] in loops:
            cyclic.append((members, True))
    return cyclic


def _edge_map(
    graph: Graph, include_types: Sequence[str]
) -> Tuple[Dict[str, List[Tuple[str, str]]], Dict[Tuple[str, str], List[str]]]:
    """Node adjacency carrying edge ids (parallel edges supported)."""
    node_edges: Dict[str, List[Tuple[str, str]]] = {}
    edge_ids: Dict[Tuple[str, str], List[str]] = {}
    for edge in graph.edges_for_view(list(include_types)):
        node_edges.setdefault(edge.source, []).append((edge.target, edge.id))
        edge_ids.setdefault((edge.source, edge.target), []).append(edge.id)
    for targets in node_edges.values():
        targets.sort(key=lambda pair: (pair[0], pair[1]))
    return node_edges, edge_ids


def enumerate_simple_cycles(
    graph: Graph,
    members: Sequence[str],
    include_types: Sequence[str],
    cap: int = 200,
    step_budget: int = 50000,
) -> List[List[str]]:
    """Enumerate simple cycles within one SCC as lists of edge ids.

    For each node ``r`` in ascending order, DFS only over members that are
    lexicographically >= ``r``; a cycle is reported when an edge returns to
    ``r``. Every simple cycle is thus found exactly once (rooted at its
    smallest member). Self-loops and parallel edges are handled naturally. The
    search is bounded for large components.
    """
    member_set = set(members)
    node_edges, _ = _edge_map(graph, include_types)
    cycles: List[List[str]] = []
    steps = 0
    ordered = sorted(member_set)

    sys.setrecursionlimit(max(sys.getrecursionlimit(), 10000))

    def dfs(start: str, node: str, path: List[str], visited: Set[str]) -> bool:
        nonlocal steps
        for target, edge_id in node_edges.get(node, []):
            steps += 1
            if steps > step_budget or len(cycles) >= cap:
                return False
            if target not in member_set or target < start:
                continue
            if target == start:
                cycles.append(path + [edge_id])
                if len(cycles) >= cap:
                    return False
            elif target not in visited:
                visited.add(target)
                if not dfs(start, target, path + [edge_id], visited):
                    return False
                visited.discard(target)
        return True

    for root in ordered:
        if len(cycles) >= cap or steps > step_budget:
            break
        if not dfs(root, root, [], {root}):
            break

    cycles.sort(key=lambda edge_ids: (len(edge_ids), edge_ids))
    return cycles[:cap]


def shortest_path_edges(
    graph: Graph,
    source: str,
    target: str,
    include_types: Sequence[str],
) -> Optional[List[str]]:
    """BFS shortest directed path between two nodes, returning edge ids."""
    node_edges, _ = _edge_map(graph, include_types)
    if source == target:
        return []
    previous: Dict[str, Tuple[str, str]] = {}
    seen = {source}
    queue = deque([source])
    while queue:
        node = queue.popleft()
        for nxt, edge_id in node_edges.get(node, []):
            if nxt in seen:
                continue
            seen.add(nxt)
            previous[nxt] = (node, edge_id)
            if nxt == target:
                path: List[str] = []
                cur = target
                while cur != source:
                    parent, eid = previous[cur]
                    path.append(eid)
                    cur = parent
                path.reverse()
                return path
            queue.append(nxt)
    return None


def reachable_nodes(
    graph: Graph, sources: Iterable[str], include_types: Sequence[str]
) -> Set[str]:
    node_edges, _ = _edge_map(graph, include_types)
    seen: Set[str] = set()
    stack = list(sources)
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        for nxt, _ in node_edges.get(node, []):
            if nxt not in seen:
                stack.append(nxt)
    return seen
