"""Generate candidate change sets that break every cycle in the active view.

A candidate is a set of machine-checkable edge operations (see model.py).
Cost combines the operation kind, the criticality weight of the touched
components and the number of distinct owners involved. Ordering is fully
deterministic: (total cost, canonical op keys), so equal-cost results are
stable across runs.
"""
from __future__ import annotations

import hashlib
from itertools import combinations

from . import analysis
from .model import (
    DEFAULT_ACTIVE_TYPES,
    EDGE_TYPES,
    STRENGTH,
    GraphError,
    apply_ops,
    canonical_op,
    validate_op,
)

BASE_COST = {"reverse": 10, "downgrade": 6, "introduce_interface": 4}
CROSS_OWNER_PENALTY = 5
MAX_POOL = 16
MAX_COMBOS = 4000
DEFAULT_MAX_OPS = 3
DEFAULT_LIMIT = 8


def legal_ops_for_edge(graph, edge, active_types=DEFAULT_ACTIVE_TYPES):
    """All legal operations for one edge. Interfaces are never invented:
    only interfaces registered in the graph can be introduced."""
    active = set(active_types)
    ops = []
    if edge["source"] != edge["target"] and edge["type"] != "generated":
        ops.append({"op": "reverse", "edge": edge["id"]})
    if edge["type"] == "hard":
        for weaker in EDGE_TYPES:
            if STRENGTH[weaker] < STRENGTH["hard"] and weaker not in active:
                ops.append({"op": "downgrade", "edge": edge["id"], "to": weaker})
    if edge["source"] != edge["target"]:
        for iname in sorted(graph.interfaces):
            if graph.interfaces[iname]["provider"] == edge["target"]:
                ops.append(
                    {"op": "introduce_interface", "edge": edge["id"], "interface": iname}
                )
    for op in ops:
        validate_op(graph, op, active_types)
    return ops


def op_owners(graph, op):
    edge = graph.edge_by_id(op["edge"])
    owners = {graph.owner_of(edge["source"]), graph.owner_of(edge["target"])}
    if op["op"] == "introduce_interface":
        provider = graph.interfaces[op["interface"]]["provider"]
        owners.add(graph.owner_of(provider))
    return owners


def op_cost(graph, op):
    edge = graph.edge_by_id(op["edge"])
    weight = sum(
        graph.components[node]["critical"] for node in {edge["source"], edge["target"]}
    )
    owners = op_owners(graph, op)
    return BASE_COST[op["op"]] + weight + CROSS_OWNER_PENALTY * max(0, len(owners) - 1)


def _op_pool(graph, active_types):
    pool = []
    seen = set()
    for edge in analysis.cyclic_edges(graph, active_types):
        for op in legal_ops_for_edge(graph, edge, active_types):
            key = canonical_op(op)
            if key not in seen:
                seen.add(key)
                pool.append(op)
    pool.sort(key=lambda op: (op_cost(graph, op), canonical_op(op)))
    return pool[:MAX_POOL]


def _candidate_sort_key(graph, ops):
    return (
        sum(op_cost(graph, op) for op in ops),
        tuple(sorted(canonical_op(op) for op in ops)),
    )


def generate_candidates(
    graph,
    active_types=DEFAULT_ACTIVE_TYPES,
    max_ops=DEFAULT_MAX_OPS,
    limit=DEFAULT_LIMIT,
):
    """Minimal-cardinality, then minimal-cost candidate change sets.

    Returns a list of candidate dicts; empty when no legal split exists.
    """
    if analysis.is_acyclic(graph, active_types):
        return []
    pool = _op_pool(graph, active_types)
    if not pool:
        return []
    for size in range(1, max_ops + 1):
        level = []
        tried = 0
        for combo in combinations(pool, size):
            tried += 1
            if tried > MAX_COMBOS:
                break
            edge_ids = [op["edge"] for op in combo]
            if len(set(edge_ids)) != size:
                continue  # never apply two ops to the same edge
            try:
                result = apply_ops(graph, list(combo), active_types)
            except GraphError:
                continue
            if analysis.is_acyclic(result, active_types):
                level.append(list(combo))
        if level:
            level.sort(key=lambda ops: _candidate_sort_key(graph, ops))
            return [build_candidate(graph, active_types, ops) for ops in level[:limit]]
    return []


def build_candidate(graph, active_types, ops):
    ops = sorted(ops, key=canonical_op)
    result = apply_ops(graph, ops, active_types)
    verified = analysis.is_acyclic(result, active_types)
    broken = [
        list(cycle)
        for cycle in analysis.enumerate_cycles(graph, active_types)
        if not analysis.cycle_alive(result, active_types, cycle)
    ]
    affected = []
    for op in ops:
        edge = graph.edge_by_id(op["edge"])
        remaining = analysis.reachable_paths(
            result, active_types, edge["source"], edge["target"], limit=3
        )
        affected.append(
            {
                "edge": edge["id"],
                "from": edge["source"],
                "to": edge["target"],
                "remaining_paths": remaining,
            }
        )
    owners = sorted(set().union(*(op_owners(graph, op) for op in ops)))
    digest = hashlib.sha1(
        "|".join(canonical_op(op) for op in ops).encode("utf-8")
    ).hexdigest()[:10]
    return {
        "id": "cand-" + digest,
        "ops": ops,
        "cost": sum(op_cost(graph, op) for op in ops),
        "owners": owners,
        "broken_cycles": broken,
        "affected_paths": affected,
        "verified": verified,
    }


def explain_candidate(graph, active_types, candidate):
    """Human-readable explanation: why sufficient, what is affected, who confirms."""
    lines = []
    for op in candidate["ops"]:
        edge = graph.edge_by_id(op["edge"])
        if op["op"] == "reverse":
            lines.append(
                "反转边 %s(%s→%s):反向依赖不再构成原方向的环。"
                % (edge["id"], edge["source"], edge["target"])
            )
        elif op["op"] == "downgrade":
            lines.append(
                "降级边 %s(%s→%s)hard→%s:在当前分析视图中不再计入环。"
                % (edge["id"], edge["source"], edge["target"], op["to"])
            )
        else:
            lines.append(
                "引入已登记接口 %s 替换边 %s(%s→%s):依赖倒置,接口节点是汇点,不会成环。"
                % (op["interface"], edge["id"], edge["source"], edge["target"])
            )
    for cycle in candidate["broken_cycles"]:
        lines.append("切断环路:" + " → ".join(list(cycle) + [cycle[0]]))
    for aff in candidate["affected_paths"]:
        if aff["remaining_paths"]:
            for path in aff["remaining_paths"]:
                lines.append("仍可达路径:" + " → ".join(path))
        else:
            lines.append(
                "注意:%s 不再可达 %s(无剩余路径)。" % (aff["from"], aff["to"])
            )
    lines.append("需要负责人确认:" + ", ".join(candidate["owners"]))
    return lines
