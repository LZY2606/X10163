"""核心图算法：SCC（迭代 Tarjan）、有环判定、环枚举、边操作应用。"""

from __future__ import annotations

from typing import Dict, Iterable, List, Sequence, Tuple

from .model import DEP_TYPES, Edge, EdgeOp, Graph


# ---------------------------------------------------------------------------
# 邻接结构
# ---------------------------------------------------------------------------

def build_adjacency(edges: Sequence[Edge]) -> Tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    """返回 (节点->按边id排序的后继, 有序对(src,dst)->边id列表)。

    平行边通过按边 id 排序获得确定性；边 id 列表保留平行边。
    """
    adj: Dict[str, List[str]] = {}
    pair_edges: Dict[Tuple[str, str], List[str]] = {}
    for e in sorted(edges, key=lambda x: x.id):
        adj.setdefault(e.src, []).append(e.dst)
        pair_edges.setdefault((e.src, e.dst), []).append(e.id)
    for dst_list in adj.values():
        dst_list.sort()
    return adj, pair_edges


def effective_edges(graph: Graph, include_types: Sequence[str]) -> List[Edge]:
    wanted = frozenset(include_types)
    return [e for e in graph.edges.values() if e.dep_type in wanted]


# ---------------------------------------------------------------------------
# 强连通分量（迭代版 Tarjan，避免大图递归深度问题）
# ---------------------------------------------------------------------------

def strongly_connected_components(node_ids: Iterable[str], edges: Sequence[Edge]):
    """返回 SCC 列表。每个 SCC 为有序节点列表；整体按 (最小节点, 大小) 稳定排序。

    自环节点自成大小为 1 的平凡（但有环）分量。
    """
    adj_raw, _ = build_adjacency(edges)
    adj: Dict[str, List[str]] = {n: sorted(set(adj_raw.get(n, []))) for n in node_ids}

    index = {}
    low = {}
    on_stack = set()
    stack: List[str] = []
    counter = [0]
    sccs: List[List[str]] = []

    for root in sorted(adj.keys()):
        if root in index:
            continue
        work = [(root, 0)]
        index[root] = low[root] = counter[0]
        counter[0] += 1
        stack.append(root)
        on_stack.add(root)

        while work:
            v, ni = work[-1]
            neigh = adj.get(v, [])
            if ni < len(neigh):
                w = neigh[ni]
                work[-1] = (v, ni + 1)
                if w not in index:
                    index[w] = low[w] = counter[0]
                    counter[0] += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, 0))
                elif w in on_stack:
                    low[v] = min(low[v], index[w])
            else:
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[v])
                if low[v] == index[v]:
                    comp = []
                    while True:
                        w = stack.pop()
                        on_stack.discard(w)
                        comp.append(w)
                        if w == v:
                            break
                    sccs.append(sorted(comp))

    sccs.sort(key=lambda c: (c[0], len(c)))
    return sccs


def cyclic_sccs(graph: Graph, include_types: Sequence[str]) -> List[List[str]]:
    """非平凡 SCC：多于一个节点，或单节点上有自环。"""
    edges = effective_edges(graph, include_types)
    all_nodes = list(graph.nodes.keys())
    sccs = strongly_connected_components(all_nodes, edges)
    self_loop = {e.src for e in edges if e.src == e.dst}
    result = []
    for comp in sccs:
        if len(comp) > 1:
            result.append(comp)
        elif comp[0] in self_loop:
            result.append(comp)
    return result


# ---------------------------------------------------------------------------
# 有环判定（Kahn 拓扑）
# ---------------------------------------------------------------------------

def has_cycle(node_ids: Sequence[str], edges: Sequence[Edge]) -> bool:
    indeg: Dict[str, int] = {n: 0 for n in node_ids}
    succ: Dict[str, List[str]] = {n: [] for n in node_ids}
    for e in edges:
        if e.src in indeg and e.dst in indeg:
            indeg[e.dst] += 1
            succ[e.src].append(e.dst)
    queue = sorted(n for n, d in indeg.items() if d == 0)
    seen = 0
    while queue:
        v = queue.pop(0)
        seen += 1
        for w in sorted(succ[v]):
            indeg[w] -= 1
            if indeg[w] == 0:
                queue.append(w)
        queue.sort()
    return seen != len(indeg)


# ---------------------------------------------------------------------------
# 环枚举（Johnson 简化：按最小节点过滤的基本环，有界）
# ---------------------------------------------------------------------------

def elementary_cycles(nodes: Sequence[str], edges: Sequence[Edge], limit: int = 200):
    """枚举有向基本环，环以节点序列表示（首尾不同，闭合）。

    每个环按其字典序最小节点恰好报告一次。自环返回 [v]。
    超过 limit 时停止（候选充分性不依赖枚举完整性）。
    """
    node_set = set(nodes)
    adj_raw, _ = build_adjacency([e for e in edges if e.src in node_set and e.dst in node_set])
    adj: Dict[str, List[str]] = {n: sorted(set(adj_raw.get(n, []))) for n in node_set}

    cycles: List[List[str]] = []
    ordered = sorted(node_set)
    start_pos = {n: i for i, n in enumerate(ordered)}

    for i, start in enumerate(ordered):
        if len(cycles) >= limit:
            break
        if start not in adj:
            continue
        path = [start]
        blocked_set = {start}
        b_map: Dict[str, set] = {}

        def circuit(v: str) -> bool:
            if len(cycles) >= limit:
                return False
            found_here = False
            for w in adj.get(v, []):
                if start_pos[w] < i:
                    continue
                if w == start:
                    cycles.append(list(path))
                    found_here = True
                    if len(cycles) >= limit:
                        return True
                elif w not in blocked_set:
                    blocked_set.add(w)
                    path.append(w)
                    if circuit(w):
                        found_here = True
                    path.pop()
            if found_here:
                _unblock_local(v)
            else:
                for w in adj.get(v, []):
                    if start_pos[w] >= i:
                        b_map.setdefault(w, set()).add(v)
            return found_here

        def _unblock_local(u: str) -> None:
            blocked_set.discard(u)
            while b_map.get(u):
                w = b_map[u].pop()
                if w in blocked_set:
                    _unblock_local(w)

        circuit(start)

    return cycles


def cycles_to_edge_ids(cycles_nodes: Sequence[Sequence[str]], graph: Graph) -> List[List[str]]:
    """把节点环转换为边 id 序列（每段取 id 最小的边，确定性）。"""
    _, pair_edges = build_adjacency(list(graph.edges.values()))
    edge_cycles = []
    for cyc in cycles_nodes:
        ids = []
        ok = True
        for idx in range(len(cyc)):
            a = cyc[idx]
            b = cyc[(idx + 1) % len(cyc)]
            opts = pair_edges.get((a, b))
            if not opts:
                ok = False
                break
            ids.append(opts[0])
        if ok:
            edge_cycles.append(ids)
    return edge_cycles


# ---------------------------------------------------------------------------
# 边操作应用
# ---------------------------------------------------------------------------

def apply_operations(graph: Graph, ops: Sequence[EdgeOp], include_types: Sequence[str]):
    """把操作应用到图副本，返回 (新边列表, 新增的接口边列表)。

    操作始终在"完整图"上执行，再用 include_types 过滤后做无环校验：
    这样反转/接口引入的新边即使类型不同也会被真实建模，而降级边
    只有在目标类型被当前视图排除时才会从有效图中消失。
    """
    by_id = dict(graph.edges)
    added: List[Edge] = []
    counter = 0
    for op in ops:
        original = by_id.get(op.edge_id)
        if original is None:
            raise ValueError("操作引用了不存在的边: %s" % op.edge_id)
        if op.kind == "reverse":
            by_id[op.edge_id] = Edge(
                id=original.id,
                src=original.dst,
                dst=original.src,
                dep_type=original.dep_type,
                owner=original.owner,
                weight=original.weight,
                downgrade=original.downgrade,
                locked=original.locked,
            )
        elif op.kind == "downgrade":
            if op.target_type not in DEP_TYPES:
                raise ValueError("降级目标类型非法: %s" % op.target_type)
            by_id[op.edge_id] = Edge(
                id=original.id,
                src=original.src,
                dst=original.dst,
                dep_type=op.target_type,
                owner=original.owner,
                weight=original.weight,
                downgrade=original.downgrade,
                locked=original.locked,
            )
        elif op.kind == "interface":
            iface = graph.interfaces.get(op.interface_id)
            if iface is None or iface.replaces_edge != op.edge_id:
                raise ValueError("接口未登记或与边不匹配: %s" % op.interface_id)
            counter += 1
            new_id = "%s::iface_%s" % (op.edge_id, op.interface_id)
            del by_id[op.edge_id]
            new_edge = Edge(
                id=new_id,
                src=iface.new_src,
                dst=iface.new_dst,
                dep_type=iface.new_type,
                owner=iface.owner or original.owner,
                weight=original.weight,
            )
            by_id[new_id] = new_edge
            added.append(new_edge)
        else:
            raise ValueError("未知操作类型: %s" % op.kind)
    return list(by_id.values()), added


def verify_candidate(graph: Graph, ops: Sequence[EdgeOp], include_types: Sequence[str]) -> bool:
    """机器校验：操作应用到图副本后，在分析视图下无环。"""
    new_edges, _ = apply_operations(graph, ops, include_types)
    wanted = frozenset(include_types)
    effective = [e for e in new_edges if e.dep_type in wanted]
    return not has_cycle(list(graph.nodes.keys()), effective)
