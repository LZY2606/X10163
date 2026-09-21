"""受影响路径与“变更后仍可达”分析。"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Set, Tuple

from .models import EdgeOp, Graph
from .scc import adjacency, view_edges


def affected_paths(
    graph: Graph,
    types: Sequence[str],
    removed_edge_ids: Set[str],
    max_paths: int = 12,
    max_steps: int = 12,
) -> List[List[str]]:
    """枚举被切断/改变的边所在 SCC 内的简单路径（边 id 序列）。

    这些路径解释“变更会影响哪些路径”：包含被移除边，或与新增边端点相关的路径。
    在循环 SCC 内做有上界的简单路径枚举，结果稳定。
    """
    edges = view_edges(graph, types)
    nodes = sorted({c.id for c in graph.components})
    adj = adjacency(edges, nodes)

    # 只关注被移除边所在的弱连通区域（简化：其两端点的可达闭包）
    focus_edges = {e for e in edges if e.id in removed_edge_ids}
    if not focus_edges:
        return []
    starts = sorted({e.src for e in focus_edges})

    results: List[List[str]] = []
    seen: Set[Tuple[str, ...]] = set()

    def dfs(edge, path_nodes: List[str], path_edges: List[str]) -> None:
        if len(results) >= max_paths:
            return
        path_edges = path_edges + [edge.id]
        path_nodes = path_nodes + [edge.target]
        if edge.id in removed_edge_ids:
            sig = tuple(path_edges)
            if sig not in seen:
                seen.add(sig)
                results.append(list(path_edges))
            # 命中被移除边后不再延伸（路径解释到此为止）
            return
        if len(path_edges) >= max_steps:
            return
        for nxt in adj[edge.target]:
            if nxt.target in path_nodes:
                continue
            dfs(nxt, list(path_nodes), list(path_edges))

    for start in starts:
        for first in adj.get(start, []):
            dfs(first, [start], [])
            if len(results) >= max_paths:
                break
    results.sort(key=lambda p: (len(p), p))
    return results[:max_paths]


def reachability_after(
    graph: Graph,
    types: Sequence[str],
    removed_edge_ids: Set[str],
    added_ops: Sequence[EdgeOp],
) -> Dict[str, List[str]]:
    """应用变更后，每个起点可达的节点集合（用于高亮仍可达路径）。"""
    edges = [e for e in view_edges(graph, types) if e.id not in removed_edge_ids]
    extra = [(o.src, o.target) for o in added_ops if o.kind == "add"]
    nodes = sorted({c.id for c in graph.components})
    adj: Dict[str, List[str]] = {n: [] for n in nodes}
    for e in edges:
        adj[e.src].append(e.target)
    for src, target in extra:
        adj.setdefault(src, []).append(target)

    out: Dict[str, List[str]] = {}
    for root in nodes:
        seen = {root}
        stack = [root]
        while stack:
            v = stack.pop()
            for w in adj.get(v, []):
                if w not in seen:
                    seen.add(w)
                    stack.append(w)
        seen.discard(root)
        out[root] = sorted(seen)
    return out


def edge_endpoints_in_paths(paths: Sequence[Sequence[str]], graph: Graph) -> List[List[str]]:
    """把边 id 路径转换为节点路径，便于页面绘制。"""
    by_id = {e.id: e for e in graph.edges}
    node_paths: List[List[str]] = []
    for path in paths:
        if not path:
            continue
        first = by_id.get(path[0])
        seq = [first.src, first.target] if first else []
        for eid in path[1:]:
            e = by_id.get(eid)
            if e:
                seq.append(e.target)
        node_paths.append(seq)
    return node_paths
