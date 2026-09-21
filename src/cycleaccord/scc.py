"""强连通分量与环枚举（仅依赖标准库）。"""
from __future__ import annotations

import sys
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .models import DepEdge, EdgeOp, Graph

sys.setrecursionlimit(100000)


def view_edges(graph: Graph, types: Sequence[str]) -> List:
    wanted = set(types)
    return [e for e in graph.edges if e.type in wanted]


def adjacency(edges: Sequence, nodes: Sequence[str]):
    adj: Dict[str, List] = {n: [] for n in nodes}
    for e in edges:
        adj.setdefault(e.src, []).append(e)
        adj.setdefault(e.target, [])
    return adj


def strongly_connected_components(
    graph: Graph, types: Sequence[str]
) -> List[List[str]]:
    """Tarjan 迭代版 SCC。单节点 SCC 仅在存在自环时才算“有环 SCC”。"""
    edges = view_edges(graph, types)
    nodes = [c.id for c in graph.components]
    adj = adjacency(edges, nodes)

    index = 0
    indices: Dict[str, int] = {}
    lowlink: Dict[str, int] = {}
    on_stack: Set[str] = set()
    stack: List[str] = []
    result: List[List[str]] = []

    # 迭代 DFS 帧：(node, 下一条邻接下标)
    for root in sorted(nodes):
        if root in indices:
            continue
        work: List[Tuple[str, int]] = [(root, 0)]
        indices[root] = lowlink[root] = index
        index += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            v, pos = work[-1]
            neighbors = adj[v]
            if pos < len(neighbors):
                w = neighbors[pos].target
                work[-1] = (v, pos + 1)
                if w not in indices:
                    indices[w] = lowlink[w] = index
                    index += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, 0))
                elif w in on_stack:
                    lowlink[v] = min(lowlink[v], indices[w])
            else:
                work.pop()
                if work:
                    parent = work[-1][0]
                    lowlink[parent] = min(lowlink[parent], lowlink[v])
                if lowlink[v] == indices[v]:
                    comp: List[str] = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        comp.append(w)
                        if w == v:
                            break
                    result.append(sorted(comp))
    return sorted(result, key=lambda comp: (-len(comp), comp))


def cyclic_components(graph: Graph, types: Sequence[str]) -> List[List[str]]:
    """返回真正含环的 SCC（大小>1，或大小==1 但有自环）。"""
    edges = view_edges(graph, types)
    self_loops = {e.src for e in edges if e.src == e.target}
    out: List[List[str]] = []
    for comp in strongly_connected_components(graph, types):
        if len(comp) > 1:
            out.append(comp)
        elif comp[0] in self_loops:
            out.append(comp)
    return out


def find_one_cycle(
    graph: Graph,
    types: Sequence[str],
    allowed_edges: Optional[Set[str]] = None,
) -> Optional[List[str]]:
    """迭代 DFS 找出一个环，返回边 id 列表；无环返回 None。"""
    edges = view_edges(graph, types)
    if allowed_edges is not None:
        edges = [e for e in edges if e.id in allowed_edges]
    nodes = sorted({c.id for c in graph.components} | {e.src for e in edges} |
                   {e.target for e in edges})
    adj = adjacency(edges, nodes)

    state: Dict[str, int] = {}  # 0=在栈上, 1=完成
    dfs_stack: List[str] = []
    edge_stack: List[str] = []
    # 每个节点正在处理的邻接下标
    pos: Dict[str, int] = {}

    for root in nodes:
        if root in state:
            continue
        dfs_stack.append(root)
        pos[root] = 0
        state[root] = 0
        while dfs_stack:
            v = dfs_stack[-1]
            neighbors = adj[v]
            if pos[v] < len(neighbors):
                edge = neighbors[pos[v]]
                pos[v] += 1
                w = edge.target
                if state.get(w) == 0:
                    # 找到回边：从栈中 w 的位置开始收集边
                    start = dfs_stack.index(w)
                    cycle_nodes = dfs_stack[start:]
                    cycle_edges = edge_stack[start:] + [edge.id]
                    return cycle_edges
                if w not in state:
                    state[w] = 0
                    pos[w] = 0
                    dfs_stack.append(w)
                    edge_stack.append(edge.id)
            else:
                state[v] = 1
                dfs_stack.pop()
                if edge_stack:
                    edge_stack.pop()
    return None


def all_cycles(
    graph: Graph, types: Sequence[str], cap: int = 500
) -> List[List[str]]:
    """Johnson 算法枚举全部基础环（按边 id 稳定）。

    返回边 id 列表的列表；超过 cap 时截断（求解器会退化为“任意环”分支）。
    自环作为长度 1 的环。
    """
    edges = view_edges(graph, types)
    nodes = sorted({c.id for c in graph.components})
    self_cycles = [[e.id] for e in sorted(edges, key=lambda e: e.id)
                   if e.src == e.target]

    adj_all: Dict[str, List] = {n: [] for n in nodes}
    for e in edges:
        if e.src != e.target:
            adj_all[e.src].append(e)

    cycles: List[List[str]] = list(self_cycles)
    truncated = False

    def strongly_on(sub_nodes: Set[str], adj) -> List[Set[str]]:
        index = 0
        indices: Dict[str, int] = {}
        lowlink: Dict[str, int] = {}
        on: Set[str] = set()
        stack: List[str] = []
        comps: List[Set[str]] = []
        for root in sorted(sub_nodes):
            if root in indices:
                continue
            work: List[Tuple[str, int]] = [(root, 0)]
            indices[root] = lowlink[root] = index
            index += 1
            stack.append(root)
            on.add(root)
            while work:
                v, p = work[-1]
                nxt = [e for e in adj[v] if e.target in sub_nodes]
                if p < len(nxt):
                    w = nxt[p].target
                    work[-1] = (v, p + 1)
                    if w not in indices:
                        indices[w] = lowlink[w] = index
                        index += 1
                        stack.append(w)
                        on.add(w)
                        work.append((w, 0))
                    elif w in on:
                        lowlink[v] = min(lowlink[v], indices[w])
                else:
                    work.pop()
                    if work:
                        par = work[-1][0]
                        lowlink[par] = min(lowlink[par], lowlink[v])
                    if lowlink[v] == indices[v]:
                        comp: Set[str] = set()
                        while True:
                            w = stack.pop()
                            on.discard(w)
                            comp.add(w)
                            if w == v:
                                break
                        comps.append(comp)
        return comps

    def least_scc(sub_nodes: Set[str], adj):
        comps = [c for c in strongly_on(sub_nodes, adj) if len(c) > 1]
        if not comps:
            return None, None
        # 选编号最小的节点所在 SCC，保证确定性
        comp = min(comps, key=lambda c: min(c))
        start = min(comp)
        return comp, start

    blocked: Set[str] = set()
    blocked_map: Dict[str, Set[str]] = {n: set() for n in nodes}
    stack_nodes: List[str] = []
    stack_edges: List[str] = []

    def unblock(u: str) -> None:
        pending = [u]
        while pending:
            x = pending.pop()
            if x in blocked:
                blocked.discard(x)
                pending.extend(blocked_map[x])
                blocked_map[x].clear()

    def circuit(v: str, s: str, sub: Set[str], adj) -> bool:
        nonlocal truncated
        found = False
        stack_nodes.append(v)
        blocked.add(v)
        for edge in adj[v]:
            w = edge.target
            if w not in sub:
                continue
            if len(cycles) >= cap:
                truncated = True
                break
            if w == s:
                cycles.append(list(stack_edges) + [edge.id])
                found = True
            elif w not in blocked:
                stack_edges.append(edge.id)
                if circuit(w, s, sub, adj):
                    found = True
                stack_edges.pop()
        if found:
            unblock(v)
        else:
            for edge in adj[v]:
                w = edge.target
                if w in sub:
                    blocked_map[w].add(v)
        stack_nodes.pop()
        return found

    remaining = set(nodes)
    # 邻接表预先按目标排序，保证稳定
    for v in adj_all:
        adj_all[v].sort(key=lambda e: (e.target, e.id))

    while remaining and not truncated:
        sub_comp, s = least_scc(remaining, adj_all)
        if sub_comp is None:
            break
        for v in sub_comp:
            blocked.discard(v)
            blocked_map[v] = set()
        circuit(s, s, sub_comp, adj_all)
        remaining.discard(s)

    # 去重（同一组边不同顺序）并稳定排序
    seen_sig: Set[Tuple[str, ...]] = set()
    unique: List[List[str]] = []
    for cyc in cycles:
        sig = tuple(sorted(cyc))
        if sig not in seen_sig:
            seen_sig.add(sig)
            unique.append(cyc)
    unique.sort(key=lambda c: (len(c), c))
    return unique


def apply_ops(graph: Graph, ops: Sequence[EdgeOp]) -> Graph:
    """在图副本上应用原子边操作，返回新图（绝不修改原图）。"""
    remove_ids = {o.edge_id for o in ops if o.kind == "remove"}
    new_edges = [e for e in graph.edges if e.id not in remove_ids]
    existing = {e.id for e in new_edges}
    for op in ops:
        if op.kind != "add":
            continue
        if not op.edge_id:
            raise ValueError("add 操作缺少 edge_id")
        if op.edge_id in existing:
            raise ValueError(f"新增边 id 冲突: {op.edge_id}")
        if op.src not in {c.id for c in graph.components}:
            raise ValueError(f"新增边的 src 未知: {op.src}")
        if op.target not in {c.id for c in graph.components}:
            raise ValueError(f"新增边的 target 未知: {op.target}")
        if op.type not in ("hard", "runtime", "test", "generated"):
            raise ValueError(f"新增边类型非法: {op.type}")
        new_edges.append(
            # 新增边不携带接口登记（保持校验简单、确定）
            DepEdge(op.edge_id, op.src, op.target, op.type, op.note, ())
        )
        existing.add(op.edge_id)
    from dataclasses import replace

    return replace(graph, edges=tuple(new_edges))


def is_acyclic(graph: Graph, types: Sequence[str]) -> bool:
    """Kahn 拓扑判定：在给定类型视图下是否无环（含自环检测）。"""
    edges = view_edges(graph, types)
    nodes = {c.id for c in graph.components}
    indeg: Dict[str, int] = {n: 0 for n in nodes}
    adj: Dict[str, List[str]] = {n: [] for n in nodes}
    for e in edges:
        indeg[e.target] = indeg.get(e.target, 0) + 1
        adj.setdefault(e.src, []).append(e.target)
        indeg.setdefault(e.src, indeg.get(e.src, 0))
        adj.setdefault(e.target, adj.get(e.target, []))
    queue = sorted(n for n, d in indeg.items() if d == 0)
    seen = 0
    while queue:
        v = queue.pop(0)
        seen += 1
        for w in sorted(adj[v]):
            indeg[w] -= 1
            if indeg[w] == 0:
                queue.append(w)
        queue.sort()
    return seen == len(nodes)
